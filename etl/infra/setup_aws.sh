#!/usr/bin/env bash
# =====================================================================
#  Provisión de la sección 5.7 en AWS Academy Learner Lab
#
#  Crea: dos instancias RDS PostgreSQL (OLTP y OLAP), dos conexiones JDBC
#  de Glue, dos crawlers que registran ambas bases en el Glue Data Catalog,
#  y el job de ETL.
#
#  Restricciones del Learner Lab que condicionan el guion:
#    * El rol IAM DEBE ser `LabRole`. No se pueden crear roles ni políticas.
#    * Región fija: us-east-1.
#    * Sin NAT Gateway (cuesta ~33 USD/mes y el lab no lo permite mantener):
#      se usan subredes con acceso saliente y un VPC endpoint para S3.
#    * Las credenciales del lab expiran cada 4 horas: reexportarlas antes
#      de correr esto.
#
#  Uso:  bash setup_aws.sh
# =====================================================================
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="airline"
DB_PASSWORD="${DB_PASSWORD:?Exporte DB_PASSWORD antes de ejecutar}"

# El Learner Lab solo permite este rol.
LAB_ROLE_ARN="$(aws iam get-role --role-name LabRole --query 'Role.Arn' --output text)"
echo "Rol IAM: ${LAB_ROLE_ARN}"

VPC_ID="$(aws ec2 describe-vpcs --region "$REGION" \
  --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
SUBNETS=($(aws ec2 describe-subnets --region "$REGION" \
  --filters Name=vpc-id,Values="$VPC_ID" \
  --query 'Subnets[].SubnetId' --output text))
echo "VPC: ${VPC_ID} · subredes: ${SUBNETS[*]}"

# La AZ de la conexión de Glue TIENE que ser la de la subred que se le pasa.
# Asumirla (p. ej. "${REGION}a") falla en cuanto la primera subred que devuelve
# la API está en otra zona, que es lo habitual: el orden no está garantizado.
SUBNET_AZ="$(aws ec2 describe-subnets --region "$REGION" \
  --subnet-ids "${SUBNETS[0]}" \
  --query 'Subnets[0].AvailabilityZone' --output text)"
echo "Subred de Glue: ${SUBNETS[0]} (${SUBNET_AZ})"

# ---------------------------------------------------------------------
# 1. Grupo de seguridad
#
#    La regla auto-referenciada NO es opcional: Glue coloca sus ENIs dentro
#    de este mismo grupo, y sin ella los crawlers y el job no pueden abrir
#    el puerto 5432 de RDS. Es la causa número uno de crawlers que se
#    quedan colgados en RUNNING sin mensaje de error.
# ---------------------------------------------------------------------
SG_ID="$(aws ec2 create-security-group --region "$REGION" \
  --group-name "${PREFIX}-db-sg" \
  --description "Acceso a RDS desde Glue" \
  --vpc-id "$VPC_ID" --query 'GroupId' --output text 2>/dev/null \
  || aws ec2 describe-security-groups --region "$REGION" \
       --filters Name=group-name,Values="${PREFIX}-db-sg" \
       --query 'SecurityGroups[0].GroupId' --output text)"

aws ec2 authorize-security-group-ingress --region "$REGION" \
  --group-id "$SG_ID" --protocol tcp --port 5432 \
  --source-group "$SG_ID" 2>/dev/null || true

# La auto-referencia limitada al 5432 NO le basta a Glue: rechaza el crawler con
#   "At least one security group must open all ingress ports."
# Sus ENIs se hablan entre sí por puertos efímeros, no solo por el de la base.
# La regla seguirá siendo cerrada al exterior: el origen es el propio grupo.
aws ec2 authorize-security-group-ingress --region "$REGION" \
  --group-id "$SG_ID" \
  --ip-permissions "IpProtocol=-1,UserIdGroupPairs=[{GroupId=${SG_ID}}]" \
  2>/dev/null || true
echo "Grupo de seguridad: ${SG_ID} (auto-referenciado en todos los puertos)"

# ---------------------------------------------------------------------
# 2. Instancias RDS PostgreSQL
#
#    OLTP: Multi-AZ en producción (RNF-D1). Aquí Single-AZ porque el
#    Learner Lab no lo permite; la desviación queda declarada en 5.7.
#    OLAP: siempre Single-AZ. Una analítica caída no detiene la venta,
#    y el dato se puede reconstruir corriendo el ETL otra vez.
# ---------------------------------------------------------------------
create_db () {
  local id="$1" db="$2" user="$3" class="$4" storage="$5"
  if aws rds describe-db-instances --region "$REGION" \
       --db-instance-identifier "$id" >/dev/null 2>&1; then
    echo "  ${id} ya existe"
    return
  fi
  aws rds create-db-instance --region "$REGION" \
    --db-instance-identifier "$id" \
    --db-instance-class "$class" \
    --engine postgres --engine-version 16.15 \
    --master-username "$user" --master-user-password "$DB_PASSWORD" \
    --db-name "$db" \
    --allocated-storage "$storage" --storage-type gp3 \
    --vpc-security-group-ids "$SG_ID" \
    --backup-retention-period 7 \
    --no-publicly-accessible \
    --no-multi-az >/dev/null
  echo "  ${id} creándose…"
}

echo "Creando instancias RDS…"
create_db "${PREFIX}-oltp" airline   airline   db.t3.small 20
create_db "${PREFIX}-olap" analytics analytics db.t3.micro 20

echo "Esperando a que queden disponibles (puede tardar ~8 min)…"
aws rds wait db-instance-available --region "$REGION" \
  --db-instance-identifier "${PREFIX}-oltp"
aws rds wait db-instance-available --region "$REGION" \
  --db-instance-identifier "${PREFIX}-olap"

OLTP_HOST="$(aws rds describe-db-instances --region "$REGION" \
  --db-instance-identifier "${PREFIX}-oltp" \
  --query 'DBInstances[0].Endpoint.Address' --output text)"
OLAP_HOST="$(aws rds describe-db-instances --region "$REGION" \
  --db-instance-identifier "${PREFIX}-olap" \
  --query 'DBInstances[0].Endpoint.Address' --output text)"
echo "OLTP: ${OLTP_HOST}"
echo "OLAP: ${OLAP_HOST}"

# ---------------------------------------------------------------------
# 3. Conexiones JDBC de Glue
#
#    Glue necesita la conexión para DOS cosas distintas: que el crawler
#    lea metadatos, y que el job lea y escriba datos. Es el mismo objeto.
# ---------------------------------------------------------------------
make_connection () {
  local name="$1" host="$2" db="$3" user="$4"
  cat > "/tmp/${name}.json" <<JSON
{
  "Name": "${name}",
  "ConnectionType": "JDBC",
  "ConnectionProperties": {
    "JDBC_CONNECTION_URL": "jdbc:postgresql://${host}:5432/${db}",
    "USERNAME": "${user}",
    "PASSWORD": "${DB_PASSWORD}",
    "JDBC_ENFORCE_SSL": "true"
  },
  "PhysicalConnectionRequirements": {
    "SubnetId": "${SUBNETS[0]}",
    "SecurityGroupIdList": ["${SG_ID}"],
    "AvailabilityZone": "${SUBNET_AZ}"
  }
}
JSON
  # En Git Bash sobre Windows, `aws` es un binario nativo: no entiende rutas
  # POSIX como /tmp/x.json y responde "No such file or directory" aunque el
  # archivo exista. `cygpath -m` la traduce a C:/... , que sí resuelve.
  local json_path="/tmp/${name}.json"
  if command -v cygpath >/dev/null 2>&1; then
    json_path="$(cygpath -m "$json_path")"
  fi

  aws glue create-connection --region "$REGION" \
    --connection-input "file://${json_path}" 2>/dev/null \
    || aws glue update-connection --region "$REGION" \
         --name "${name}" --connection-input "file://${json_path}"
  echo "  conexión ${name} lista"
}

echo "Creando conexiones JDBC de Glue…"
make_connection "${PREFIX}-oltp-conn" "$OLTP_HOST" airline   airline
make_connection "${PREFIX}-olap-conn" "$OLAP_HOST" analytics analytics

# Verificación explícita: si la conexión falla aquí, el crawler se quedará
# colgado sin mensaje útil. Mejor descubrirlo ahora.
for conn in "${PREFIX}-oltp-conn" "${PREFIX}-olap-conn"; do
  echo "  probando ${conn}…"
  aws glue start-connection-test --region "$REGION" \
    --connection-name "$conn" 2>/dev/null \
    || echo "    (start-connection-test no disponible; se validará con el crawler)"
done

# ---------------------------------------------------------------------
# 4. Bases de datos del catálogo y crawlers
#
#    Requisito explícito del enunciado: AMBAS bases deben quedar
#    registradas en el Glue Data Catalog.
# ---------------------------------------------------------------------
for db in "${PREFIX}_oltp_catalog" "${PREFIX}_olap_catalog"; do
  aws glue create-database --region "$REGION" \
    --database-input "{\"Name\":\"${db}\"}" 2>/dev/null || true
done

make_crawler () {
  local name="$1" conn="$2" path="$3" target_db="$4"
  aws glue create-crawler --region "$REGION" \
    --name "$name" \
    --role "$LAB_ROLE_ARN" \
    --database-name "$target_db" \
    --targets "{\"JdbcTargets\":[{\"ConnectionName\":\"${conn}\",\"Path\":\"${path}\"}]}" \
    --schema-change-policy '{"UpdateBehavior":"UPDATE_IN_DATABASE","DeleteBehavior":"LOG"}' \
    2>/dev/null || echo "  crawler ${name} ya existe"
}

echo "Creando crawlers…"
# El Path usa el patrón base/esquema/% : sin el comodín el crawler no
# descubre tablas.
make_crawler "${PREFIX}-oltp-crawler" "${PREFIX}-oltp-conn" \
  "airline/airline/%"   "${PREFIX}_oltp_catalog"
make_crawler "${PREFIX}-olap-crawler" "${PREFIX}-olap-conn" \
  "analytics/analytics/%" "${PREFIX}_olap_catalog"

echo "Ejecutando crawlers…"
aws glue start-crawler --region "$REGION" --name "${PREFIX}-oltp-crawler" || true
aws glue start-crawler --region "$REGION" --name "${PREFIX}-olap-crawler" || true

# ---------------------------------------------------------------------
# 5. Job de ETL (Python Shell, 0.0625 DPU)
#
#    Python Shell y no Spark: el volumen del ETL (decenas de miles de filas
#    diarias) cabe de sobra en un solo proceso. Un job de Spark cobra un
#    mínimo de 2 DPU, es decir ~16 veces más caro, para mover los mismos
#    datos más lento por el arranque del cluster.
# ---------------------------------------------------------------------
BUCKET="${PREFIX}-glue-scripts-$(aws sts get-caller-identity --query Account --output text)"
aws s3 mb "s3://${BUCKET}" --region "$REGION" 2>/dev/null || true
aws s3 cp ../glue_job_oltp_to_olap.py "s3://${BUCKET}/scripts/" --region "$REGION"

aws glue create-job --region "$REGION" \
  --name "${PREFIX}-etl-oltp-to-olap" \
  --role "$LAB_ROLE_ARN" \
  --command "{\"Name\":\"pythonshell\",\"PythonVersion\":\"3.9\",\"ScriptLocation\":\"s3://${BUCKET}/scripts/glue_job_oltp_to_olap.py\"}" \
  --connections "{\"Connections\":[\"${PREFIX}-oltp-conn\",\"${PREFIX}-olap-conn\"]}" \
  --max-capacity 0.0625 \
  --default-arguments "{
    \"--oltp_dsn\":\"postgresql://airline:${DB_PASSWORD}@${OLTP_HOST}:5432/airline\",
    \"--olap_dsn\":\"postgresql://analytics:${DB_PASSWORD}@${OLAP_HOST}:5432/analytics\",
    \"--lookback_minutes\":\"60\",
    \"--additional-python-modules\":\"psycopg[binary]==3.2.3\"
  }" 2>/dev/null || echo "  job ya existe"

# ---------------------------------------------------------------------
# 6. Planificación: diaria a las 03:00 hora de Colombia (08:00 UTC)
#
#    Diaria y no continua: las preguntas de la gerencia (ocupación,
#    ingresos, cancelaciones) son tácticas, no de tiempo real. Frescura de
#    24 h es suficiente y cuesta una fracción. Justificación en 5.7.2.
# ---------------------------------------------------------------------
aws glue create-trigger --region "$REGION" \
  --name "${PREFIX}-etl-nightly" \
  --type SCHEDULED \
  --schedule "cron(0 8 * * ? *)" \
  --actions "[{\"JobName\":\"${PREFIX}-etl-oltp-to-olap\"}]" \
  --start-on-creation 2>/dev/null || echo "  trigger ya existe"

echo
echo "==================== LISTO ===================="
echo "OLTP        : ${OLTP_HOST}"
echo "OLAP        : ${OLAP_HOST}"
echo "Catálogos   : ${PREFIX}_oltp_catalog · ${PREFIX}_olap_catalog"
echo "Job de ETL  : ${PREFIX}-etl-oltp-to-olap (diario 08:00 UTC)"
echo
echo "Verificar el registro en el catálogo:"
echo "  aws glue get-tables --database-name ${PREFIX}_oltp_catalog --query 'TableList[].Name'"
echo "  aws glue get-tables --database-name ${PREFIX}_olap_catalog --query 'TableList[].Name'"
echo
echo "Ejecutar el ETL a demanda:"
echo "  aws glue start-job-run --job-name ${PREFIX}-etl-oltp-to-olap"
