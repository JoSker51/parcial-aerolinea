# Anexo A — Bitácora de prompts

> **Requisito de la sección 4 del enunciado:** *“el documento final debe incluir, como anexo, una bitácora breve de los principales prompts usados durante el ejercicio y de cómo fueron refinados o corregidos por el equipo.”*

**Asistente utilizado:** Claude (Opus 5), a través de Claude Code. **Alcance de la asistencia:** redacción del documento, diseño del modelo de datos, implementación del backend y del ETL, guiones de infraestructura, diagramas.

------------------------------------------------------------------------

## Cómo está organizada esta bitácora

Cada entrada tiene la misma estructura:

| Campo | Qué contiene |
|----|----|
| **Prompt inicial** | Lo que se escribió la primera vez, tal cual. Casi siempre corto e impreciso |
| **Prompt refinado** | La versión que realmente produjo el resultado útil, después de ver qué faltaba |
| **Qué produjo** | El resultado que entregó el asistente |
| **Corrección del equipo** | Lo que hubo que cambiar, rechazar o exigir. **Es la columna que importa** |
| **Artefacto** | Dónde quedó el resultado |

La diferencia entre el prompt inicial y el refinado **es** el aprendizaje del ejercicio: un prompt corto obtiene una respuesta genérica; un prompt que fija restricciones, formato y criterio de aceptación obtiene algo que se puede sustentar.

------------------------------------------------------------------------

## P-01 · Encuadre del trabajo

**Prompt inicial** \> *Ayúdame con este parcial* — adjuntando `parcial12026-2-EN.docx`

**Prompt refinado** \> *Lee el enunciado completo. Antes de producir nada, dime qué alcance necesitas de mí y qué información te falta que cambie materialmente el trabajo. No asumas la restricción de la sección 3: pregúntame si ya la tengo.*

**Qué produjo** El asistente leyó el enunciado y, en vez de empezar a escribir, planteó dos preguntas: el alcance (entrega parcial, diseño completo, o todo) y **si ya se había recibido la restricción confidencial de la sección 3**.

**Corrección del equipo** La segunda pregunta era la importante y no la habíamos previsto. Sin ella, el documento se habría escrito sin lugar para la restricción y habría que rehacerlo. La respuesta llevó a que el documento incluyera el **punto de extensión PE-0**: tres lugares concretos donde la restricción entra sin rediseño, con ejemplos de cómo aterrizaría según su tipo.

**Artefacto** — Sección 2.3 del documento final.

------------------------------------------------------------------------

## P-02 · Traducción del enunciado

**Prompt inicial** \> *Traduce todos los puntos*

**Prompt refinado** \> *Traduce el enunciado íntegro al español conservando la numeración original, las tablas de preguntas guía y la rúbrica completa con sus porcentajes. No resumas: cada criterio de evaluación debe quedar literal.*

**Qué produjo** Traducción completa, incluida la rúbrica con los cuatro niveles por criterio.

**Corrección del equipo** Ninguna sobre el contenido. Pero el efecto fue metodológico: con la rúbrica en español y a la vista, **el documento se estructuró contra la rúbrica, no contra el orden de lectura del enunciado**. Cada sección del documento final responde a una fila de la rúbrica.

**Artefacto** — `docs/00-enunciado-traducido.md`.

------------------------------------------------------------------------

## P-03 · Supuestos y requisitos (secciones 5.1 y 5.2)

**Prompt inicial** \> *Haz los requisitos funcionales y no funcionales*

**Prompt refinado** \> *Escribe los requisitos funcionales en tabla (ID, descripción, actor, prioridad MoSCoW) resolviendo explícitamente las cinco preguntas guía de 5.1. Para los no funcionales, exige una métrica con umbral numérico, un método de verificación y el riesgo de negocio concreto que mitiga: rechaza cualquier RNF que diga “rápido” o “seguro” sin número. Antes de los requisitos, lista los supuestos que estás tomando sobre lo que el enunciado deja abierto, y para cada uno di qué parte del diseño se rompe si el supuesto cambia.*

**Qué produjo** 15 supuestos, 30 RF, 19 RNF y 6 riesgos de negocio nombrados.

**Corrección del equipo**

| Problema en la primera respuesta | Corrección exigida |
|----|----|
| Los RNF decían *“la búsqueda debe ser rápida”* sin número | Se exigió métrica + umbral + método de verificación + riesgo. Resultado: p95 ≤ 400 ms directos, p95 ≤ 1.200 ms con escalas, medido con k6 a 120 búsquedas/s |
| Los supuestos eran una lista plana | Se agregó la columna **“qué se rompe si cambia”**. Es lo que convierte un supuesto en algo sustentable |
| Propuso disponibilidad de **99,99%** “porque es mejor” | Se corrigió a **99,9%** con el argumento de que 99,99% exige multi-región activa, y eso entra en conflicto directo con la no-sobreventa. Se cuantificó el costo del salto: +28,58 USD/mes |

**Artefacto** — Secciones 2, 5, 6 y 7 del documento final.

------------------------------------------------------------------------

## P-04 · Modelo entidad-relación (sección 5.3)

**Prompt inicial** \> *Diseña el modelo de base de datos con las entidades que dice el parcial*

**Prompt refinado** \> *Diseña el modelo E-R en notación Crow’s Foot. No asumas que la lista de entidades del enunciado es correcta ni completa: justifica cada entidad que agregues o partas. Resuelve explícitamente: cómo se representa un vuelo que se repite todos los días, dónde vive el control de inventario para que dos compras simultáneas no sobrevendan, si la silla es parte de la compra o del check-in, y qué normalización usas sabiendo que el modelo alimenta un OLTP y un pipeline analítico. Para cada decisión, di qué alternativa descartaste y por qué.*

**Qué produjo** 26 tablas en 3FN, diagrama, diccionario de datos, cinco decisiones estructurales con alternativa descartada.

**Corrección del equipo**

| Problema en la primera respuesta | Corrección exigida |
|----|----|
| Una sola entidad `flight` con una fila por fecha | Se partió en `scheduled_flight` e `flight_instance` (**DEC-1**): la capacidad es de la instancia, no del programado |
| Relación directa `reservation N—N flight_instance` | Se introdujo `itinerary` como entidad propia (**DEC-2**): el puente plano no distingue una conexión de dos vuelos independientes |
| **Inventario controlado marcando sillas individuales** | Se cambió a **conteo por cabina** (**DEC-3**). Con sillas individuales, dos compradores tocan filas distintas: nadie se bloquea, pero nadie controla el total. **Fue la corrección de mayor impacto de todo el ejercicio** |
| La vista de conciliación contaba tramos | Debía contar sillas (tramos × pasajeros con silla). Se detectó revisando el caso de una reserva de tres pasajeros |

**Artefacto** — Sección 8 del documento final, `backend/sql/01_schema.sql`.

------------------------------------------------------------------------

## P-05 · Arquitectura y concurrencia (secciones 5.4 y 5.5)

**Prompt inicial** \> *¿Monolito o microservicios? ¿Cómo evito la sobreventa?*

**Prompt refinado** \> *Para cada decisión de arquitectura, nómbrala, conéctala al requisito no funcional que la origina, y describe la alternativa que descartaste y por qué. Para el control de sobreventa, evalúa las tres opciones —bloqueo optimista con reintentos, cola serializadora por vuelo, y bloqueo pesimista— contra el escenario real de 40 solicitudes simultáneas por la última silla. No elijas por costumbre: elige por el escenario.*

**Qué produjo** Monolito modular con réplicas de lectura; bloqueo pesimista con `SELECT … FOR UPDATE`; pago delegado con máquina de estados propia; API B2B separada; frontend híbrido SSR/SPA.

**Corrección del equipo**

| Problema en la primera respuesta | Corrección exigida |
|----|----|
| Propuso **bloqueo optimista** “porque es el patrón más habitual” | Se rechazó tras analizar el escenario: con 40 solicitudes por 1 silla, 39 fallan y reintentan, y en el reintento vuelven a chocar. El optimista es óptimo cuando los conflictos son raros; aquí el conflicto **es** el caso de uso |
| El protocolo de bloqueo no fijaba orden de adquisición | Se añadió el **orden canónico** (`ORDER BY flight_instance_id`) al notar que dos itinerarios con tramos en sentido inverso producirían un abrazo mortal |
| La restricción de no-sobreventa vivía solo en el código | Se exigió una `CHECK` **en la base** como última línea de defensa: la regla de negocio más importante no puede depender únicamente de la aplicación |

**Artefacto** — Secciones 9 y 10 del documento final.

------------------------------------------------------------------------

## P-06 · Implementación y prueba de concurrencia (sección 5.6)

**Prompt inicial** \> *Implementa la API y una prueba de concurrencia*

**Prompt refinado** \> *Implementa en FastAPI las operaciones de buscar, crear reserva y consultar reserva sobre PostgreSQL, siendo fiel al modelo de 5.3 y al bloqueo pesimista de 5.4. Escribe una prueba de concurrencia que NO pueda dar falso positivo: hilos reales del sistema operativo, sincronizados con una barrera para que salgan simultáneos, contra la API real con varios procesos, y con la aserción final directamente contra la base de datos, no contra los códigos HTTP. Declara toda desviación entre el diseño y lo implementado, con su motivo.*

**Qué produjo** API con siete endpoints, 11 pruebas automatizadas, script de estrés que genera evidencia, y siete desviaciones declaradas.

**Corrección del equipo**

| Problema en la primera respuesta | Por qué invalidaba la prueba | Corrección |
|----|----|----|
| La prueba usaba `asyncio.gather` | Corrutinas cooperativas: el propio bucle de eventos serializa. **La prueba habría pasado sin ningún mecanismo de bloqueo** | Hilos reales del sistema operativo |
| Lanzaba las solicitudes en un bucle | La primera terminaba antes de que la segunda empezara: no había contención que medir | `threading.Barrier` |
| Un solo proceso de uvicorn | La aplicación podía serializar por sí sola y ocultar un fallo de la capa de datos | 4 procesos |
| Solo verificaba códigos HTTP | “La API respondió bien” no es lo mismo que “la base quedó consistente” | Aserción contra `v_oversell_check` |

**Ajustes de diseño que la implementación reveló** (declarados como D-1 a D-7): el más relevante fue que **el precio hay que congelarlo al retener, no al emitir**, porque entre ambos momentos hay 20 minutos y las tarifas cambian.

**Artefacto** — Secciones 11 y 12 del documento final, `backend/`.

------------------------------------------------------------------------

## P-07 · AWS, ETL y costos (sección 5.7)

**Prompt inicial** \> *Haz la parte de AWS con Glue*

**Prompt refinado** \> *Para el ETL entre la base transaccional y una segunda PostgreSQL analítica: evalúa las opciones disponibles en el Learner Lab (Glue Spark, Glue Python Shell, Lambda, DMS) por costo y límites, y elige justificando. Diseña el esquema analítico distinto del transaccional si corresponde, y explica por qué. Para cada servicio AWS, conéctalo a un RF/RNF concreto y a uno o más de los seis pilares Well-Architected explicando por qué ese pilar. Proyecta el costo mensual con supuestos declarados, y separa el escenario ×10 en volumen y frecuencia porque se comportan distinto. Declara toda desviación que imponga el Learner Lab.*

**Qué produjo** Modelo estrella con 5 dimensiones y 3 hechos, job de Glue Python Shell incremental e idempotente, tabla de 11 servicios con pilares, proyección de costos base/producción/×10.

**Corrección del equipo**

| Problema en la primera respuesta | Corrección exigida |
|----|----|
| Propuso **Glue Spark** por defecto | Se cambió a **Python Shell (0,0625 DPU)**: Spark cobra un mínimo de 2 DPU, ~16 veces más caro y más lento por el arranque del cluster, para mover decenas de miles de filas que caben en memoria |
| Los pilares se citaban de forma decorativa (“Seguridad: porque es seguro”) | Se exigió nombrar el RF/RNF concreto y explicar **por qué ese pilar y no otro** |
| No aparecían las limitaciones del Learner Lab | Se agregaron las desviaciones **A-1 a A-4** (Single-AZ, `LabRole`, sin réplica de lectura, sin Secrets Manager). Presentar `LabRole` como diseño intencional habría sido deshonesto |
| La proyección ×10 mezclaba volumen y frecuencia | Se separaron. El hallazgo solo aparece al separarlos: **×10 volumen ⇒ ×2,8 costo, pero ×10 frecuencia ⇒ +2%**. El cómputo del ETL no es el costo; las bases encendidas sí |

**Artefacto** — Sección 13 del documento final, `etl/`.

------------------------------------------------------------------------

## P-08 · Verificación real

**Prompt inicial** \> *¿Funciona?*

**Prompt refinado** \> *Levanta el stack completo desde cero con Docker, ejecuta la suite de pruebas, corre la prueba de concurrencia en tres escenarios (2/1, 40/1 y 60/5) y el ETL contra la base analítica. Si algo falla, corrígelo y documenta qué era. No me digas que funciona: muéstrame la salida.*

**Qué produjo** Tres bugs de tipado de parámetros que **no eran visibles leyendo el código** (SQLAlchemy no reconoce `:param::tipo`; PostgreSQL no infiere el tipo de un parámetro nulo), y un problema de aislamiento entre pruebas.

**Corrección del equipo** Los cuatro se corrigieron y se declararon como **D-6 y D-7**. Uno de ellos es revelador: al intentar limpiar la base entre pruebas, el `DELETE` falló porque el trigger de inmutabilidad del log de auditoría lo prohíbe. **La prueba chocó contra una garantía del propio diseño y la garantía ganó**: es la evidencia de que RNF-A2 está implementado, no solo declarado.

Resultado final: **11/11 pruebas**, cero sobreventa en los tres escenarios, ETL cargando 2.435 filas en 0,8 s.

**Artefacto** — `docs/evidencia/`, sección 12.3 del documento final.

------------------------------------------------------------------------

## P-09 · Consolidación del documento

**Prompt inicial** \> *Todo en un documento*

**Prompt refinado** \> *Consolida todo en un único documento que responda, en este orden y de forma verificable: cómo se resolvió cada pregunta guía, qué se asumió, qué métricas concretas hay y a qué riesgo apuntan, el modelo E-R, dónde se refleja cada requisito en el código, la implementación y sus pruebas, AWS con los seis pilares, y una matriz de trazabilidad en los dos sentidos: de riesgo a prueba, y de decisión a requisito. Genera Word con índice y diagramas embebidos, y verifica la estructura del archivo antes de entregarlo.*

**Qué produjo** Documento de 19 secciones, 268 tablas y 3 diagramas embebidos, con índice automático.

**Corrección del equipo** La primera versión tenía la **numeración duplicada** (pandoc añadía la suya sobre la de los títulos) y **el título repetido**. Se corrigió el conversor. Más tarde, al generar el diccionario de datos, la primera versión salió **sin tablas**: los saltos de línea de Windows se habían duplicado y cada fila quedó separada por una línea en blanco. Se detectó porque se verifica la estructura del `.docx` después de generarlo, y se normalizaron los finales de línea.

**Artefacto** — `docs/DOCUMENTO-FINAL.docx`.

------------------------------------------------------------------------

## P-10 · Diagramas de las bases de datos

**Prompt inicial** \> *Ayúdame con un diagrama para explicar mejor las bases de datos*

**Prompt refinado** \> *El E-R completo de 26 tablas es demasiado denso para explicar. Haz dos diagramas de nivel superior: uno con los ocho bloques funcionales del transaccional ordenados por volatilidad, mostrando solo las dependencias entre bloques y resaltando la tabla crítica; y otro que muestre las dos bases, qué tablas del transaccional alimentan cada hecho del analítico, y qué pregunta de negocio responde cada uno. Genera SVG y PNG e insértalos en el diccionario de datos.*

**Qué produjo** Dos diagramas en SVG y PNG, insertados en el diccionario en lugar del panorama dibujado en texto.

**Corrección del equipo**

| Problema en la primera respuesta | Corrección exigida |
|----|----|
| El mapa de bloques dibujaba **todas** las claves foráneas: 2.330 px de ancho, ilegible | Se redujo a dependencias **entre bloques**. Pasó a 1.191 px y se entiende de un vistazo |
| Los acentos salían corruptos | Faltaba declarar UTF-8 en la página de renderizado |
| En el diagrama de las dos bases, los recuadros se solapaban | Los títulos eran más anchos que su contenido; se acortaron y la descripción pasó a un nodo interno |

**Artefacto** — `docs/diagramas/mapa-bloques-oltp.png`, `docs/diagramas/dos-bases-de-datos.png`.

------------------------------------------------------------------------

## P-11 · Guía de sustentación

**Prompt inicial** \> *Haznos un documento para saber cómo resolvió cada sección*

**Prompt refinado** \> *La sustentación es eliminatoria por sección. Escribe una guía interna con, por sección, las preguntas que probablemente haga el docente y una respuesta corta memorizable para cada una, más una ampliación por si insiste. Incluye las variaciones de escenario más plausibles con su respuesta preparada —incluida al menos una que sí rompa el diseño, respondida con honestidad— y las preguntas trampa que buscan distinguir criterio de memorización.*

**Qué produjo** Guía con las cuatro respuestas que hay que saber de memoria, preguntas por sección, diez variaciones de escenario, ocho preguntas trampa, qué no decir, y un reparto sugerido por integrante.

**Corrección del equipo** Se pidió explícitamente que la guía **no fingiera que todo estaba previsto**: la variación “operar sin conexión en aeropuertos remotos” rompe la garantía de serialización, y la respuesta honesta es decirlo y proponer cómo se acotaría.

**Artefacto** — `docs/GUIA-SUSTENTACION.md` (interno, no se entrega).

------------------------------------------------------------------------

------------------------------------------------------------------------

# Segunda sesión — auditoría, despliegue real y documentación

> Las entradas anteriores corresponden a la sesión de **diseño**. Las siguientes son de una segunda sesión, con otro integrante, cuyo objetivo fue distinto: **verificar lo diseñado, desplegarlo de verdad en AWS y documentar lo aprendido**.
>
> **Una observación que vale la pena hacer explícita:** en esta sesión los prompts llegaron **ya refinados desde el primer intento** —con restricciones, formato y criterio de aceptación incluidos—, sin pasar por la versión corta e imprecisa. Eso es el aprendizaje de la primera sesión aplicado, y es la razón de que aquí no haya diferencia entre "prompt inicial" y "prompt refinado".

------------------------------------------------------------------------

## P-12 · Auditar antes de escribir

**Prompt** > *Léelo todo primero. Recorre `docs/*.md`, `backend/` y `etl/` completos antes de escribir nada. **No asumas nada del contenido a partir de nombres de archivo.***

**Qué produjo** Una auditoría completa del estado real del proyecto contra el enunciado.

**Corrección del equipo** Ninguna: la instrucción **era** la corrección, y fue del equipo. Sin ella el asistente habría inferido por los nombres de archivo que la sección 5.7 estaba terminada —existían `03-aws-etl-catalogo-costos.md`, `etl/glue_job_oltp_to_olap.py` y `etl/infra/setup_aws.sh`— y habría dado por desplegado algo que solo estaba escrito. **La auditoría demostró lo contrario**, y ese hallazgo redefinió todo el trabajo posterior.

**Artefacto** — `docs/06-guia-defensa.md`.

------------------------------------------------------------------------

## P-13 · Guía de defensa con estructura impuesta

**Prompt** > *Para cada sección del enunciado, una tabla de decisión → por qué → qué requisito la respalda → qué pregunta de la sustentación podría atacarla y cómo respondería. Una lista aparte de todos los supuestos, con nota de si siguen siendo válidos dado el estado actual del código. Verifica que la restricción de la sección 3 esté integrada. Y un checklist cruzando la rúbrica contra lo que existe hoy, marcando qué criterio ya cumpliría "Sobresaliente" y cuál se quedaría en "Aceptable" o peor, **con la razón concreta**. Sácalo de lo que YA está decidido: **no inventes decisiones nuevas** salvo que encuentres un vacío real.*

**Qué produjo** La guía con las cinco partes pedidas, y un diagnóstico incómodo.

**Corrección del equipo** La restricción *"no inventes decisiones nuevas"* es la que impidió que la auditoría se convirtiera en un rediseño encubierto: su trabajo era **evaluar lo existente**, no proponer alternativas. **Hallazgo principal: el criterio de mayor peso de la rúbrica (20%, ETL y catálogo) calificaba como Insuficiente**, porque el código existía pero nunca se había ejecutado contra AWS.

**Artefacto** — `docs/06-guia-defensa.md`.

------------------------------------------------------------------------

## P-14 · La restricción de la sección 3

**Aporte del equipo** > *No tenemos restricción alguna, por si acaso.*

**Por qué se registra** El asistente tenía instrucción de **detenerse y preguntar** si no encontraba la restricción en ningún documento; el equipo se adelantó. La decisión de fondo es del equipo.

**Corrección del equipo** Se recomendó no dejarlo como estaba: el texto decía *"aún no ha recibido la restricción"*, que **suena a pendiente**. La recomendación fue confirmarlo por escrito con el docente y cambiarlo por *"se confirmó que no se asignó restricción"*, que es una decisión cerrada. Es una edición de una frase, pero cambia cómo la lee un evaluador.

**Artefacto** — Sección 2.3 del documento final (pendiente de la confirmación del docente).

------------------------------------------------------------------------

## P-15 · Despliegue real en AWS

**Prompt** > *Guíame paso a paso en la terminal para configurar las credenciales del Learner Lab, cargar los esquemas contra RDS, correr `setup_aws.sh` y **depurar en vivo** cualquier error de aws cli, conexión JDBC o crawler que aparezca. Verifica con `aws glue get-tables` que ambos catálogos quedaron poblados.* — condicionado a que el checklist confirmara que 5.7 seguía sin desplegar.

**Qué produjo** El despliegue real: ambas bases registradas en el Glue Data Catalog (30 y 13 objetos) y el ETL en `SUCCEEDED` a los 112 segundos.

**Corrección del equipo** La parte de *"depurar en vivo"* resultó ser la importante: **aparecieron seis fallos que ningún diseño en papel anticipa**, documentados en §13.8. Uno de ellos **corrigió una afirmación equivocada del propio documento**:

| Lo que el documento afirmaba | Lo que el despliegue demostró |
|---|---|
| *"Security Group auto-referenciado en el **5432**"* | **No basta.** Glue exige auto-referencia en **todos** los puertos, o el crawler falla con `InvalidInputException`. El documento acertaba en el síntoma y erraba en el remedio |

**Y un aprendizaje de método:** la primera corrida de los crawlers terminó en `SUCCEEDED` **con el catálogo vacío**, porque se ejecutaron antes de cargar los esquemas. **Un proceso "exitoso" no prueba que el requisito se cumplió** — hay que verificar el efecto, no el estado. Es el mismo error, en otra escala, que una prueba de concurrencia que mira códigos HTTP en vez del estado de la base.

**Artefacto** — §13.7 y §13.8 del documento final, `docs/evidencia/aws-despliegue.md`.

------------------------------------------------------------------------

## P-16 · El presupuesto como restricción de diseño

**Aporte del equipo** > *Tengo 50 dólares para el Learner Lab. Haz todo para que me cueste lo menos posible, y ten en cuenta que no se me acaben.*

**Qué produjo** Convirtió el costo en una restricción activa, no en un número de una tabla.

**Corrección del equipo** La revisión detectó que **tres instancias RDS de ejercicios anteriores llevaban semanas encendidas** y habían consumido aproximadamente la mitad del presupuesto —lo confirmó después el panel de costos de la consola—. Ante tres opciones (borrarlas, apagarlas o dejarlas), **el equipo eligió apagarlas**: reversible y conserva los datos de esos ejercicios, descartando la opción más barata. Se detuvo además el disparador nocturno del ETL por una razón **que no es de costo**: con las bases apagadas el cron habría fallado cada noche y **la última corrida del job pasaría a `FAILED`**, arruinando la evidencia.

**Resultado** El gasto diario bajó de ~2,83 a ~0,38 USD.

**Artefacto** — §9 de `docs/06-guia-defensa.md` (runbook de costos y reactivación).

------------------------------------------------------------------------

## P-17 · Profundización y qué se entrega

**Prompts** > *¿Qué decisiones de diseño tomaste y por qué se hizo de esta forma y no de ninguna otra?* · *Necesito saber qué clases hay y **por qué están ordenadas como están ordenadas**.* · *Un documento detallado de todas las tablas, por qué existen y sus relaciones.* · *¿Qué se entrega al profesor?*

**Qué produjo** Tres documentos de profundización y una aclaración de alcance.

**Corrección del equipo** Las preguntas sacaron a la luz **tres cosas que ningún documento explicaba**:

| Hallazgo | Por qué importaba |
|---|---|
| `fare_class` tiene una columna **`rank`** | Ordena las clases de más restrictiva a más flexible. `P`, `Y` y `B` **son el mismo asiento físico**: lo que se cobra distinto es la flexibilidad |
| La **clave foránea compuesta** de `itinerary_segment` hacia `flight_inventory` | Impide vender un tramo en una combinación de vuelo y cabina que no tenga fila de inventario |
| El criterio de **borrado en cascada** | Lo hay en las relaciones de pertenencia, pero **no** desde la reserva hacia tiquetes, pagos y auditoría: en producción una reserva no se borra, se cancela |

**Y una aclaración de alcance:** se verificó contra el enunciado que el entregable es **un solo documento más el repositorio**; los documentos de profundización son material de estudio del equipo, **no de entrega**.

**Artefacto** — `docs/07`, `docs/08`, `docs/09`, `docs/10` (internos).

------------------------------------------------------------------------

## Balance del uso de IA

**Dónde aportó más** - Velocidad de redacción y exhaustividad de las tablas. - **El análisis comparativo de alternativas**: tener las tres opciones de concurrencia escritas con sus contras permitió elegir con criterio en vez de por costumbre. - Detectar en la verificación errores invisibles en revisión de código.

**Dónde hubo que corregirla — patrones que se repitieron**

| Patrón | Ejemplos | Cómo se compensó |
|----|----|----|
| Propone el patrón **más común**, no el más adecuado al escenario | Bloqueo optimista · Glue Spark · 99,99% de disponibilidad | Exigir siempre la alternativa descartada y el porqué, contra el escenario concreto |
| Tiende a **métricas sin número** | “Debe ser rápido”, “debe ser seguro” | Rechazar cualquier RNF sin umbral y sin método de verificación |
| Escribe **pruebas que pasan** antes que pruebas que prueban | La primera prueba de concurrencia habría pasado sin ningún bloqueo | Exigir que la prueba no pueda dar falso positivo, y explicar por qué |
| Omite las **desviaciones incómodas** | Single-AZ, `LabRole`, precio congelado tarde | Pedir explícitamente la lista de desviaciones y su justificación |

**Consecuencia para la sustentación** Las decisiones sustentables de este trabajo son las que **sobrevivieron a una corrección**, y cada una está documentada con la alternativa que se descartó y el motivo. Los cuatro patrones de la tabla son exactamente donde el docente puede preguntar *“¿por qué no la otra opción?”* — y la respuesta ya está escrita.

------------------------------------------------------------------------

> **Criterio de esta bitácora:** registra únicamente lo ocurrido, en dos sesiones y con dos integrantes distintos, distinguiendo lo que propuso el asistente de lo que decidió el equipo. La razón es práctica antes que ética: **una bitácora inflada sube la vara que después hay que saltar en la sustentación**, que es eliminatoria. Las decisiones atribuidas al equipo son las que el equipo puede defender; las atribuidas al asistente están documentadas con su alternativa descartada, que es lo que permite sustentarlas aunque no se hayan originado en el equipo.
