# Sistema de Reservas — Aerolínea Regional
## Parte III — Implementación en AWS: ETL, catálogo de datos y costos (sección 5.7)

> Continúa las Partes I y II. Referencias `RF-xx` / `RNF-Xn` / `DEC-n` / `SUP-n` apuntan a esos documentos.

---

## 5.7.1 Arquitectura implementada

```
        ┌─────────────────── VPC ──────────────────────────────────────┐
        │                                                              │
        │  ┌───────────────────┐         ┌───────────────────┐         │
  API   │  │  RDS PostgreSQL   │         │  RDS PostgreSQL   │         │
 (5.6)──┼─▶│  airline-oltp     │         │  airline-olap     │◀── BI   │
        │  │  db.t3.small      │         │  db.t3.micro      │  (A7)   │
        │  │  esquema 3FN      │         │  esquema estrella │         │
        │  └─────────┬─────────┘         └─────────▲─────────┘         │
        │            │                             │                   │
        │            │   ┌─────────────────────┐   │                   │
        │            └──▶│  AWS Glue           │───┘                   │
        │   lectura      │  Python Shell job   │   escritura           │
        │   incremental  │  0.0625 DPU         │   idempotente         │
        │   (watermark)  │  diario 03:00 COT   │                       │
        │                └─────────────────────┘                       │
        │                                                              │
        │  ┌───────────────┐            ┌───────────────┐              │
        │  │ oltp-crawler  │            │ olap-crawler  │              │
        │  └───────┬───────┘            └───────┬───────┘              │
        │          │        conexiones JDBC     │                      │
        │          ▼                            ▼                      │
        │      ┌────────────────────────────────────────┐              │
        │      │       AWS Glue Data Catalog            │              │
        │      │  airline_oltp_catalog (30 objetos)     │              │
        │      │  airline_olap_catalog (13 objetos)     │              │
        │      └────────────────────────────────────────┘              │
        │                                                              │
        │  Security Group airline-db-sg (auto-referenciado, todos los   │
        │  puertos: Glue lo exige)                                      │
        └──────────────────────────────────────────────────────────────┘
                              Rol IAM: LabRole
```

Provisión completa y reproducible en [`etl/infra/setup_aws.sh`](../etl/infra/setup_aws.sh); el código del ETL en [`etl/glue_job_oltp_to_olap.py`](../etl/glue_job_oltp_to_olap.py).

---

## 5.7.2 Decisiones justificadas

### ¿Qué se copia a la base analítica, y se transforma o se copia tal cual?

**Se transforma.** Copiar `ticket` tal cual reproduciría en la analítica el mismo problema que tiene el OLTP: responder "ingresos por tarifa" exigiría seis `JOIN` en cada consulta de BI. El ETL desnormaliza a **esquema en estrella** y precalcula las métricas de negocio.

Se cargan **tres hechos**, uno por cada pregunta que el enunciado pone en boca de la gerencia comercial:

| Hecho | Grano | Pregunta que responde | Origen en el OLTP |
|---|---|---|---|
| `fact_ticket_sale` | Un tiquete = pasajero × tramo | **Ingresos por tarifa** | `ticket` ⋈ `itinerary_segment` ⋈ `reservation` ⋈ `ancillary` |
| `fact_flight_occupancy` | Una cabina de un vuelo | **Ocupación por ruta** | `flight_inventory` ⋈ `flight_instance` + ingreso agregado de `ticket` |
| `fact_reservation_lifecycle` | Una reserva | **Patrones de cancelación** | **`reservation_event`** ⋈ `reservation` ⋈ `itinerary` |

Cuatro decisiones de transformación que conviene poder defender una por una:

1. **El grano del hecho principal es el tiquete, no la reserva.** Es el grano más fino con valor monetario. Desde el tiquete se agrega a ruta, tarifa, vuelo o fecha; al revés no se puede. Elegir la reserva como grano habría hecho imposible responder "ingresos por tarifa" en un itinerario multi-tramo con clases distintas por tramo.

2. **`days_before_departure` se precalcula en el ETL.** Es el eje de la pregunta "ingresos por tarifa **según anticipación**" — que es la política tarifaria del enunciado. Calcularla al vuelo obliga a un `JOIN` con `dim_date` en cada consulta de BI.

3. **`load_factor` se materializa.** Es una métrica **semiaditiva**: se promedia por ruta, no se suma. Dejarla al analista es garantizar que alguien la sume y reporte una ocupación del 4.300%.

4. **`fact_flight_occupancy` se agrega en el origen**, no fila por fila para agregar después. Reduce el volumen que viaja por la red, que es exactamente donde está el costo del ETL en Glue.

> **El punto no obvio, y el más defendible de esta sección:** `reservation_event` —el log de auditoría de DEC-10, creado para cumplir RNF-A1— resulta ser **la única fuente posible** de los patrones de cancelación. Una reserva cancelada que solo guarda su estado final no dice ni cuándo se canceló, ni cuánto tiempo vivió, ni a cuántos días de la salida. **Un requisito de cumplimiento terminó habilitando una capacidad analítica.** Esa conexión entre RNF-A1 y esta sección es la respuesta a "¿cómo se relaciona la arquitectura transaccional con el pipeline de Big Data?".

### ¿El esquema analítico debe ser idéntico al transaccional?

**No, y esto es continuación directa de la discusión de normalización de 5.3.2.**

| | OLTP (`airline`) | OLAP (`analytics`) |
|---|---|---|
| Modelo | 3FN, 26 tablas | Estrella, 5 dimensiones + 3 hechos |
| Optimizado para | Escribir con integridad, transacciones cortas | Leer agregando, escaneos amplios |
| Redundancia | Mínima (3 excepciones declaradas) | Deliberada: la ruta se repite en cada fila de hecho |
| Claves | Naturales y sustitutas con FK estrictas | Sustitutas, sin integridad referencial estricta |
| Historia | Estado actual + log de eventos | Snapshot por corrida, con `loaded_at` |

Un único modelo para ambos fines queda **mal normalizado para transaccionar y mal desnormalizado para analizar**. La 3FN del OLTP existe para que `flight_inventory` sea el único lugar donde vive el conteo de sillas (DEC-3) — condición de RNF-C1. La estrella existe para que "ocupación por ruta en diciembre" sea un `GROUP BY` sobre una tabla y no un plan de ejecución con seis `JOIN`. **El ETL es precisamente la traducción entre ambos mundos, y por eso es un componente de diseño, no una tubería trivial.**

### ¿Qué servicio de AWS implementa el ETL?

Opciones reales dentro del Learner Lab, evaluadas:

| Opción | A favor | En contra | Veredicto |
|---|---|---|---|
| **Glue ETL (Spark)** | Escala a terabytes; conectores nativos. | Mínimo **2 DPU** y ~1 min de arranque de cluster. **≈16× más caro** para mover decenas de miles de filas, y más lento por el arranque en frío. | ✗ Sobredimensionado para SUP-1/SUP-3. |
| **Glue Python Shell** ✅ | **0.0625 DPU** (el mínimo facturable de AWS). Arranca en segundos. Un solo proceso `psycopg` mueve el volumen de sobra. Integrado con el Data Catalog, las conexiones JDBC y los *triggers* programados. | No escala más allá de un proceso. | ✅ **Elegido.** |
| **Lambda + psycopg** | Aún más barato; capa gratuita generosa. | **Límite duro de 15 min** de ejecución: una recarga completa lo excedería y el job fallaría justo cuando más se necesita. Fuera del ecosistema Glue: habría que cablear las conexiones a mano. | ✗ El límite de tiempo es un riesgo operativo real. |
| **DMS** | Replicación continua, CDC. | Replica **esquemas**, no los transforma. Se necesitaría de todos modos un segundo paso para construir la estrella. Instancia de replicación **siempre encendida**: ~$25/mes adicionales. | ✗ Resuelve otro problema. |
| **Step Functions + Lambda** | Orquestación fina, reintentos. | Complejidad de orquestación para un pipeline de un solo paso. | ✗ Sin beneficio a esta escala. |

**Justificación en una frase:** el volumen del ETL (≈15.000 tiquetes/mes en el escenario base) cabe holgadamente en un proceso Python; pagar el mínimo de 2 DPU de Spark sería pagar 16 veces más por arrancar un cluster para mover lo que cabe en memoria. Si el volumen creciera ×100 —no ×10— la migración a Glue Spark sería un cambio de `--command` y de las funciones de carga, sin tocar el modelo.

### ¿Cómo se conectan ambas bases al Glue Data Catalog?

Cuatro piezas, y las cuatro tienen que estar bien o el crawler se queda colgado sin mensaje útil:

| Pieza | Configuración | Por qué |
|---|---|---|
| **Conexión JDBC** | `jdbc:postgresql://<host>:5432/<db>`, usuario y clave, `JDBC_ENFORCE_SSL=true`. Una por base. | Es el mismo objeto que usan el crawler (para metadatos) y el job (para datos). |
| **Rol IAM** | `LabRole` — el Learner Lab no permite crear roles. Necesita `AWSGlueServiceRole` y lectura del secreto. | Restricción del entorno, declarada. |
| **Red** | Subredes de la VPC + **Security Group auto-referenciado en TODOS los puertos** (`IpProtocol=-1` con origen el propio grupo). | **La causa número uno de fallos.** Glue crea sus ENIs *dentro* de ese grupo de seguridad y necesita que el grupo se hable consigo mismo. **Verificado en el despliegue real:** una auto-referencia limitada al 5432 **no basta** — el crawler falla con `InvalidInputException: At least one security group must open all ingress ports`. Sigue siendo una regla cerrada al exterior, porque el origen es el propio grupo, no un CIDR. |
| **Crawler** | Un crawler por base, con `Path = <db>/<schema>/%`. Sin el comodín no descubre tablas. | Registra `airline_oltp_catalog` y `airline_olap_catalog`, que es el requisito explícito del enunciado. **Conteo real medido:** 30 objetos en el OLTP (26 tablas **+ las 4 vistas**, que el crawler JDBC también registra) y 13 en el OLAP (10 tablas + 3 vistas de negocio). |

Verificación de que el requisito se cumplió:

```bash
aws glue get-tables --database-name airline_oltp_catalog --query 'TableList[].Name'
```

```bash
aws glue get-tables --database-name airline_olap_catalog --query 'TableList[].Name'
```

### ¿El ETL corre una vez, periódicamente o por eventos?

**Periódico diario, a las 03:00 hora de Colombia (08:00 UTC).**

| Opción | Frescura | Costo/mes | Cuándo se justifica |
|---|---|---|---|
| Una sola vez | Datos congelados | ~$0 | Solo para una carga histórica inicial. |
| **Diario (elegido)** | **hasta 24 h** | **~$0,06** | Preguntas tácticas de gerencia comercial. |
| Cada hora | hasta 1 h | ~$1,32 | Si el pricing dinámico consumiera la analítica. |
| Por eventos (CDC) | segundos | ~$25+ (instancia DMS siempre encendida) | Solo si una decisión operativa dependiera del dato en vivo. |

**Justificación:** las tres preguntas del enunciado —ocupación por ruta, ingresos por tarifa, patrones de cancelación— son **tácticas, no operativas**. Nadie cambia una ruta en respuesta a lo que pasó hace diez minutos. Una frescura de 24 h es funcionalmente equivalente a una de 10 minutos para ese uso, y cuesta dos órdenes de magnitud menos.

**Por qué a las 03:00:** es el valle de tráfico (SUP-4) y la ventana con menos escrituras. Además el ETL lee de la **réplica de lectura**, no del primario (DEC-11), así que aun en pico no competiría con la ruta crítica de reserva — la elección horaria es un margen de seguridad adicional, no la garantía principal. **La garantía de RNF-P4 es la réplica; el horario es el cinturón sobre los tirantes.**

**Extracción incremental** por marca de agua (`etl_watermark`): solo se leen los tiquetes con `issued_at` posterior a la última corrida, con una ventana de reproceso de 60 minutos para no perder filas escritas por transacciones que estaban abiertas cuando la corrida anterior tomó su instantánea. Sin esto, el escaneo completo diario crecería de forma lineal con la historia acumulada y el job se encarecería para siempre.

---

## 5.7.3 Justificación de servicios AWS

Los seis pilares del Well-Architected Framework: **OE** Excelencia Operativa · **SEG** Seguridad · **FIA** Fiabilidad · **EFI** Eficiencia del Rendimiento · **COS** Optimización de Costos · **SOS** Sostenibilidad.

| Servicio AWS | Alternativa considerada | Requisito que respalda la elección | Pilar(es) Well-Architected |
|---|---|---|---|
| **RDS PostgreSQL** `airline-oltp` (db.t3.small, gp3 20 GB, Multi-AZ en prod.) | Aurora PostgreSQL; PostgreSQL autogestionado en EC2 | **RNF-C1**: se necesita `SELECT … FOR UPDATE` con semántica ACID estricta sobre una fila (DEC-4); PostgreSQL gestionado lo da sin operar el motor. **RNF-D1** (99,9%): Multi-AZ da conmutación automática. **RNF-D3**: PITR cubre RPO ≤ 5 min. | **FIA** — Multi-AZ y PITR son la implementación literal del SLO. **OE** — parches y respaldos gestionados liberan al equipo. **COS** — Aurora cuesta ~2,5× y su ventaja (escalado de lectura masivo) no se necesita a 120 búsquedas/s. |
| **RDS PostgreSQL** `airline-olap` (db.t3.micro, gp3 20 GB, Single-AZ) | Redshift Serverless; Athena sobre S3 | **RNF-P4**: aísla la carga analítica del transaccional. **RF-30**: expone los datos a A7 sin tocar el OLTP. Single-AZ a propósito: una analítica caída **no detiene la venta** y el dato se reconstruye reejecutando el ETL. | **EFI** — separación de cargas por perfil de acceso. **COS** — Redshift Serverless factura por RPU con un mínimo que a este volumen es puro desperdicio; el enunciado además exige PostgreSQL. **SOS** — instancia dimensionada al volumen real, no al hipotético. |
| **AWS Glue — job Python Shell** (0.0625 DPU) | Glue Spark (2 DPU mín.); Lambda; DMS | **RF-30** y **RNF-P4**: mueve el dato sin tocar el primario. El volumen (≈15k tiquetes/mes) cabe en un proceso. | **COS** — 0,0625 DPU es el mínimo facturable de AWS: ~16× más barato que el mínimo de Spark. **SOS** — se consume el cómputo estrictamente necesario; nada de arrancar un cluster para mover megabytes. **EFI** — sin arranque en frío de cluster, el job termina en minutos. |
| **AWS Glue Data Catalog** | Catálogo propio en tablas; documentación manual | Requisito explícito del enunciado. Sirve a **RF-30** dando descubribilidad del esquema a A7. | **OE** — un catálogo vivo evita la deriva entre el esquema real y su documentación. **EFI** — habilita Athena/QuickSight sin trabajo adicional. Sin costo real: los primeros 1M de objetos y de peticiones son gratuitos. |
| **Glue Crawlers** (2, semanales) | Registro manual de tablas; `CREATE TABLE` en el catálogo | Mantienen el catálogo sincronizado cuando el esquema evoluciona. | **OE** — detección automática de cambios de esquema; con `UpdateBehavior=UPDATE_IN_DATABASE` y `DeleteBehavior=LOG` los cambios se registran sin destruir metadatos. **COS** — semanales y no diarios: el esquema cambia con cada despliegue, no cada noche. |
| **Glue Connections (JDBC)** | Credenciales en el código del job | **RNF-S3**: credenciales fuera del código, tránsito con `JDBC_ENFORCE_SSL=true`. | **SEG** — sin secretos en el repositorio; cifrado en tránsito. |
| **Rol IAM `LabRole`** | Rol dedicado con permisos mínimos | **RNF-S4**: identidad atribuible para las acciones automatizadas. | **SEG** — *(desviación declarada:* en producción correspondería un rol propio con privilegio mínimo, un rol distinto por componente. El Learner Lab **no permite crear roles**, así que se usa `LabRole`, que es más permisivo de lo que el principio de mínimo privilegio recomienda. Se declara explícitamente en lugar de presentarlo como diseño intencional.*)* |
| **VPC + Security Group auto-referenciado** | Bases con acceso público | **RNF-S3**: ninguna base es accesible desde internet. | **SEG** — superficie de exposición nula: las bases solo aceptan conexiones desde dentro del grupo de seguridad. **FIA** — aísla el fallo de red. |
| **EventBridge / Glue Trigger** (cron diario) | Ejecución manual; ejecución continua | Frescura de 24 h acordada arriba. | **OE** — el pipeline no depende de que alguien se acuerde de lanzarlo. **COS/SOS** — cómputo solo cuando hay trabajo que hacer. |
| **S3** (scripts del job) | Empaquetar el script en la imagen del job | Requisito de Glue: el script vive en S3. | **OE** — versionado del artefacto desplegado. Costo despreciable (<$0,01/mes). |
| **CloudWatch Logs** | Sin registro centralizado | **RNF-A3** y diagnóstico de corridas fallidas. | **OE** — sin logs no hay operación posible. Retención a 30 días para acotar el costo. |

---

## 5.7.4 Proyección de costos

### Supuestos declarados

Un costo sin supuestos no es una proyección, es un número.

| # | Supuesto | Valor |
|---|---|---|
| C-1 | Región | `us-east-1` (Norte de Virginia) |
| C-2 | Precios | Bajo demanda, públicos, **consultados el 2026-09-02**. Los precios de AWS cambian: verificar en la calculadora antes de presentar. |
| C-3 | Horas/mes | 730 |
| C-4 | Volumen (base) | SUP-1 a SUP-4: 35 rutas, ~3.600 instancias de vuelo/mes, ~15.000 tiquetes/mes, ~12.000 reservas/mes |
| C-5 | Almacenamiento | 20 GB gp3 por instancia (OLTP crece ~1,5 GB/mes; 20 GB cubren ~12 meses) |
| C-6 | Duración del ETL | ~4 min por corrida diaria en el escenario base |
| C-7 | Crawlers | 2 DPU × 10 min mínimo, ejecución **semanal** (4,3 corridas/mes cada uno) |
| C-8 | Multi-AZ | **Solo el OLTP** en producción (RNF-D1). El Learner Lab no lo permite: la tabla muestra ambos escenarios. |
| C-9 | Transferencia de datos | Todo el tráfico dentro de la misma VPC y AZ ⇒ sin cargo de salida |
| C-10 | Sin NAT Gateway | Se evita deliberadamente: costaría **~$33/mes**, más que todo el resto del pipeline junto. |

### Escenario base (Learner Lab / desarrollo)

| Concepto | Cálculo | USD/mes |
|---|---|---|
| RDS OLTP `db.t3.small` Single-AZ | $0,036/h × 730 h | **26,28** |
| RDS OLTP almacenamiento gp3 20 GB | $0,115/GB-mes × 20 | 2,30 |
| RDS OLAP `db.t3.micro` Single-AZ | $0,018/h × 730 h | **13,14** |
| RDS OLAP almacenamiento gp3 20 GB | $0,115/GB-mes × 20 | 2,30 |
| Respaldos automáticos | Gratis hasta el 100% del almacenamiento aprovisionado | 0,00 |
| Glue job Python Shell | 0,0625 DPU × (4/60) h × $0,44 × 30 corridas | **0,06** |
| Glue crawlers (2, semanales) | 2 DPU × (10/60) h × $0,44 × 4,3 × 2 crawlers | **1,26** |
| Glue Data Catalog | 39 tablas, ~2.600 peticiones/mes → dentro de la capa gratuita (1M/1M) | 0,00 |
| S3 (scripts) | < 1 MB | 0,01 |
| CloudWatch Logs | ~0,5 GB ingestados, retención 30 días | 0,30 |
| **TOTAL** | | **≈ 45,65** |

### Escenario de producción (con Multi-AZ en el OLTP, RNF-D1)

| Concepto | USD/mes |
|---|---|
| RDS OLTP `db.t3.small` **Multi-AZ** ($0,072/h × 730) | 52,56 |
| RDS OLTP almacenamiento 20 GB (se replica) | 4,60 |
| RDS OLAP `db.t3.micro` Single-AZ + 20 GB | 15,44 |
| Glue (job + crawlers + catálogo) | 1,32 |
| S3 + CloudWatch | 0,31 |
| **TOTAL** | **≈ 74,23** |

> **El costo de RNF-D1 es explícito y cuantificado: +$28,58/mes** (+63%) por pasar de Single-AZ a Multi-AZ. Ese es el precio numérico de la decisión de disponibilidad tomada en la Parte I. Poder decir esta cifra en la sustentación es lo que separa "elegimos 99,9%" de "elegimos 99,9% y sabemos lo que cuesta".

### Proyección ×10

El enunciado pide proyectar si **el volumen de datos o la frecuencia de actualización** se multiplican por 10. Son dos ejes distintos y conviene separarlos, porque se comportan de forma muy diferente.

#### Eje A — Volumen de datos ×10

350 rutas, ~150.000 tiquetes/mes, ~36.000 instancias de vuelo/mes.

| Concepto | Base | ×10 | Δ |
|---|---|---|---|
| RDS OLTP: `db.t3.small` → `db.t3.medium` | 26,28 | 52,56 | +26,28 |
| Almacenamiento OLTP: 20 → 200 GB | 2,30 | 23,00 | +20,70 |
| RDS OLAP: `db.t3.micro` → `db.t3.small` | 13,14 | 26,28 | +13,14 |
| Almacenamiento OLAP: 20 → 200 GB | 2,30 | 23,00 | +20,70 |
| Glue job (4 → ~18 min por corrida) | 0,06 | 0,26 | +0,20 |
| Glue crawlers (más tablas, misma frecuencia) | 1,26 | 1,40 | +0,14 |
| CloudWatch | 0,30 | 2,00 | +1,70 |
| **TOTAL** | **45,65** | **128,50** | **+82,85 (×2,8)** |

#### Eje B — Frecuencia ×10 (de diario a cada 2,4 h ≈ 10 corridas/día)

| Concepto | Base | ×10 frecuencia | Δ |
|---|---|---|---|
| Glue job (300 corridas/mes en vez de 30) | 0,06 | 0,55 | +0,49 |
| Instancias RDS (sin cambio: ya están encendidas) | 41,72 | 41,72 | 0 |
| Resto | 3,87 | 4,20 | +0,33 |
| **TOTAL** | **45,65** | **46,47** | **+0,82 (×1,02)** |

#### Eje A + B combinados

**≈ $131/mes** — el cruce no es multiplicativo, porque los dos ejes cargan sobre partidas distintas.

### Lectura de la proyección — pilares de Costos y Sostenibilidad

Tres conclusiones que hay que llevar a la sustentación, porque son el punto de toda esta sección:

**1. El cómputo del ETL no es el costo. Las bases encendidas sí.**
Multiplicar el volumen por 10 sube la factura ×2,8, y multiplicar la frecuencia por 10 la sube apenas un **2%**. En el escenario base, Glue es el **2,9%** del total; RDS es el **95%**. La consecuencia práctica es contraintuitiva y es la que hay que sostener: **si el negocio pidiera datos más frescos, se puede pasar de diario a cada 2 horas por menos de un dólar al mes.** La restricción a diario **no es económica** — es de valor de negocio (nadie decide nada con esa diferencia de frescura). Confundir ambas cosas llevaría a negar una mejora que es casi gratis.

**2. El crecimiento es sublineal en costo (×10 volumen → ×2,8 costo).**
Porque el gasto está dominado por capacidad reservada, no por consumo. Eso es bueno para el margen y malo para la disciplina: se paga capacidad ociosa el 90% del tiempo. **Optimización de costos** en la siguiente iteración, en orden de retorno:
- Reservas de 1 año en las instancias RDS: **−30 a −40%** sobre la partida que domina la factura. Es la palanca de mayor impacto por lejos.
- `gp3` con IOPS base en vez de aprovisionadas mientras la carga no lo exija.
- Apagar el OLAP fuera de la ventana analítica (7:00–20:00 días hábiles): **−60%** de esa instancia. Viable justamente porque el OLAP es Single-AZ y reconstruible.

**3. Sostenibilidad — dónde está el desperdicio real.**
El pilar de sostenibilidad se traduce aquí en tres decisiones concretas, no en una declaración de intenciones:
- **Extracción incremental por marca de agua.** Sin ella, cada corrida diaria reprocesaría la historia completa: al año, ~365× más cómputo y E/S para producir exactamente el mismo resultado. Es el mayor ahorro energético del pipeline y ya está implementado.
- **Agregación en el origen** (`fact_flight_occupancy`): mueve por la red el resultado, no las filas crudas.
- **Dimensionar al volumen real, no al hipotético.** `db.t3.micro` para el OLAP no es tacañería: es no encender capacidad que nadie va a usar. Y `t3` es *burstable*: consume proporcionalmente a la carga real en vez de mantener un piso constante.
- **Diario en vez de continuo**, mientras el valor de negocio no lo exija. Con la salvedad del punto 1: la razón es el valor, no el costo.

---

## 5.7.5 Cómo verificar el cumplimiento del requisito

El enunciado exige que **ambas bases queden registradas en el Glue Data Catalog**. Evidencia a adjuntar en la entrega:

| # | Verificación | Comando / captura |
|---|---|---|
| 1 | Ambas instancias RDS disponibles | `aws rds describe-db-instances --query 'DBInstances[].[DBInstanceIdentifier,DBInstanceStatus]'` |
| 2 | Conexiones JDBC creadas | `aws glue get-connections --query 'ConnectionList[].Name'` |
| 3 | Crawlers en estado `SUCCEEDED` | `aws glue get-crawler --name airline-oltp-crawler --query 'Crawler.LastCrawl'` |
| 4 | **Tablas del OLTP en el catálogo** | `aws glue get-tables --database-name airline_oltp_catalog --query 'TableList[].Name'` |
| 5 | **Tablas del OLAP en el catálogo** | `aws glue get-tables --database-name airline_olap_catalog --query 'TableList[].Name'` |
| 6 | Corrida exitosa del ETL | `aws glue get-job-runs --job-name airline-etl-oltp-to-olap --query 'JobRuns[0].[JobRunState,ExecutionTime]'` |
| 7 | Datos en la analítica | `SELECT * FROM analytics.v_occupancy_by_route LIMIT 20;` |
| 8 | Captura de la consola de Glue mostrando ambas bases del catálogo | — |

---

## 5.7.6 Desviaciones declaradas frente al diseño

El enunciado pide declarar explícitamente cualquier desviación en lugar de dejarla sin mencionar.

| # | Desviación | Motivo | Impacto |
|---|---|---|---|
| **A-1** | OLTP **Single-AZ** en el Learner Lab, no Multi-AZ como exige RNF-D1. | El Learner Lab no habilita Multi-AZ. | Solo de entorno. El costo del diseño correcto está cuantificado en 5.7.4 ($74,23/mes). |
| **A-2** | Rol IAM compartido `LabRole` en vez de roles con privilegio mínimo por componente. | El Learner Lab no permite crear roles ni políticas. | Debilita RNF-S4 en el entorno de práctica. En producción: un rol por componente. |
| **A-3** | El ETL lee del **primario** en el Learner Lab, no de una réplica de lectura como establece DEC-11. | No hay presupuesto para una tercera instancia en el lab. | Viola RNF-P4 **solo en el lab**. Se mitiga corriendo el ETL en la ventana de menor tráfico. En producción, la réplica es obligatoria. |
| **A-4** | Contraseñas por parámetro del job en vez de **Secrets Manager**. | El Learner Lab restringe Secrets Manager de forma intermitente. | Debilita RNF-S3. En producción: Secrets Manager ($0,40/secreto/mes) con rotación. |
| **A-5** | Acceso público **temporal** a ambas instancias RDS durante la carga inicial del esquema, con el 5432 restringido a una sola IP, y **revertido a `no-publicly-accessible` al terminar**. | El Learner Lab no tiene bastión ni NAT, y los `.sql` había que cargarlos desde la máquina del equipo. | Ventana acotada y cerrada al terminar; el estado final cumple RNF-S3. En producción la carga se haría desde un bastión o desde la propia VPC. |
| **A-6** | El driver `psycopg` se entrega como **wheels en S3** vía `--extra-py-files`, en vez de `--additional-python-modules`. | El job corre dentro de la VPC (por las conexiones JDBC) y **sin NAT no hay ruta a PyPI**: el `pip install` agota el tiempo de espera. Es consecuencia directa de la decisión C-10 de no pagar NAT Gateway. | Ninguno funcional; el job queda además **más rápido y determinista**, sin depender de PyPI en cada corrida. Detalle en §5.7.7. |

---

## 5.7.7 Lo que solo apareció al desplegar de verdad

> El enunciado pide declarar los ajustes en vez de omitirlos: *"un ajuste no es un error: es información valiosa que debe documentarse"*. Estos seis no son visibles leyendo el diseño — **solo aparecen ejecutando contra AWS real**, y por eso valen como evidencia de que el despliegue ocurrió.

| # | Síntoma | Causa raíz | Corrección |
|---|---|---|---|
| **1** | `create-db-instance` falla: la versión de motor no existe | **PostgreSQL 16.3 quedó deprecada** en RDS. Las versiones de motor se retiran con el tiempo, así que fijar una versión exacta en un guion de infraestructura tiene fecha de caducidad | Se fijó `16.15`, la última 16.x disponible. Se conservan las clases `db.t3.small` / `db.t3.micro` porque siguen siendo orderables y son las de la proyección de costos de §5.7.4 |
| **2** | La conexión de Glue queda inconsistente | El guion fijaba `AvailabilityZone = "${REGION}a"` pero tomaba `SUBNETS[0]`, y **el orden en que la API devuelve las subredes no está garantizado**: la primera resultó estar en `us-east-1d` | La AZ se **deriva** de la subred elegida (`describe-subnets`), en vez de asumirse |
| **3** | `Unable to load paramfile file:///tmp/...json: No such file or directory` | El `aws` de Windows es un binario nativo y **no entiende rutas POSIX** de Git Bash, aunque el archivo exista | La ruta se traduce con `cygpath -m` antes de pasarla. (El mismo fenómeno afecta a los nombres de log group que empiezan con `/`: requieren `MSYS_NO_PATHCONV=1`) |
| **4** | Crawler `FAILED`: `At least one security group must open all ingress ports` | **Este documento estaba equivocado.** La auto-referencia del security group limitada al 5432 **no le basta a Glue**: sus ENIs se comunican entre sí por puertos efímeros | Regla auto-referenciada con `IpProtocol=-1` (todos los puertos). Sigue cerrada al exterior: el origen es el propio grupo. Corregido también en §5.7.2 |
| **5** | Crawlers `SUCCEEDED` pero **catálogo con 0 tablas** | El guion lanza los crawlers **inmediatamente después de crear las bases**, cuando todavía están vacías. Un crawler sobre una base sin tablas termina bien y no registra nada | El orden correcto es: crear RDS → **cargar los esquemas** → ejecutar los crawlers. Un crawler exitoso no implica un catálogo poblado: **hay que verificar el conteo, no el estado** |
| **6** | Job de ETL colgado ~10 min y luego `Connection to pypi.org timed out` | El job corre **dentro de la VPC** (por llevar conexiones JDBC adjuntas) y, sin NAT Gateway (decisión C-10), **no tiene ruta a internet**, así que `--additional-python-modules` no puede instalar el driver. Un segundo intento con `--extra-py-files` avanzó pero falló igual: Glue instala cada wheel en un `--target` separado, así que al instalar `psycopg` **pip no considera instalada** la `typing-extensions` que acababa de poner y sale a buscarla por red | Los wheels de Linux se **precargan en S3** (que sí es alcanzable) y se entregan por `--extra-py-files`, con el wheel de `psycopg` **parcheado para no declarar la dependencia**, que se instala aparte. Queda como desviación **A-6** |

**La lectura que conviene llevar a la sustentación:** los seis son fallos de *entorno*, no de diseño — el modelo de datos, el modelo estrella y la elección de servicios no cambiaron. Pero el número 5 tiene una moraleja que sí es de método: **un crawler en estado `SUCCEEDED` no demuestra que el requisito se cumplió.** El requisito del enunciado es que las tablas *aparezcan en el catálogo*, y eso solo lo demuestra `aws glue get-tables` devolviendo un conteo mayor que cero. Verificar el estado del proceso en vez de su efecto es exactamente el tipo de falso positivo que este ejercicio busca que el equipo aprenda a detectar.
