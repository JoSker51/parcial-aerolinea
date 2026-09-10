# Guía de defensa — Parcial 1, Aerolínea Regional

> Este documento **no inventa decisiones nuevas**. Es una relectura de `00` a `05` y del código en `backend/` y `etl/` organizada para la sustentación oral: qué se decidió, por qué, qué lo respalda, y qué pregunta incómoda puede hacer el docente sobre cada punto.
>
> Convenciones: igual que en el resto del proyecto — `SUP-n` supuesto, `RF-nn`/`RNF-Xn` requisito, `DEC-n` decisión de arquitectura, `D-n` desviación diseño↔código, `A-n` desviación de entorno AWS, `PE-n` punto de extensión.

---

## 0. Resumen ejecutivo — léase primero

Auditoría completa de `docs/`, `backend/` y `etl/` contra el enunciado (`docs/00-enunciado-traducido.md`). El trabajo documental es inusualmente sólido: 30 RF, 19 RNF, 15 decisiones de arquitectura, todas con alternativa descartada y trazabilidad explícita, backend implementado y probado con evidencia real de concurrencia (3 escenarios, 11/11 pruebas), y un diseño de AWS completo con justificación por pilar Well-Architected y proyección de costos.

Dos huecos reales, en orden de gravedad:

| # | Hueco | Sección afectada | Gravedad |
|---|---|---|---|
| **1** | ~~La sección 5.7 (AWS) está diseñada y codificada, pero no hay evidencia de haberse desplegado.~~ **RESUELTO.** El despliegue se ejecutó de verdad: ambas instancias RDS creadas, los tres esquemas cargados, **ambas bases registradas en el Glue Data Catalog** (30 y 13 objetos) y el job de ETL corrido con éxito (`SUCCEEDED`, 112 s) poblando los tres hechos del modelo estrella. Evidencia en [`evidencia/aws-despliegue.md`](evidencia/aws-despliegue.md); los seis obstáculos encontrados quedaron documentados en §5.7.7 del documento de AWS. | 5.7 | ✅ Cerrado |
| **2** | **La restricción confidencial de la sección 3 del enunciado nunca se integró.** `PE-0` en los tres documentos de diseño dice textualmente que "el equipo aún no ha recibido la restricción". El usuario confirmó en esta sesión que **no existe ninguna restricción asignada**. El enunciado (§3 y §7) y la rúbrica (criterio de RF) tratan esto como obligatorio: *"la restricción debe integrarse coherentemente en todos los entregables"* y la sustentación es *"eliminatoria"* si no se puede sustentar. Ver §4 de esta guía para la recomendación concreta. | 3, 5.1 | 🟡 Alta, pero resoluble sin rediseño |

Todo lo demás — modelo de datos, arquitectura, implementación del backend, evidencia de concurrencia, trazabilidad — está en condición de sustentarse tal como está. El §5 de esta guía (checklist de rúbrica) detalla criterio por criterio.

---

## 1. Sección 5.1 — Requisitos funcionales

| Decisión documentada | Por qué (razón dada en los docs) | Respaldo (FR/NFR/DEC) | Pregunta de sustentación que la ataca | Cómo responder |
|---|---|---|---|---|
| Una reserva multi-tramo es **un PNR con `itinerary` como entidad intermedia**, no varias reservas encadenadas | El pasajero compró un viaje, no vuelos sueltos; el pago es uno solo; una tarifa ida-vuelta no se puede tarifar por tramo aislado | RF-08, RF-09, DEC-2 | *"¿Qué pasa si cancelo solo el regreso de una ida-y-vuelta?"* | Se cancela el `itinerary` de regreso; la ida (otro `itinerary` del mismo PNR) sigue `ACTIVE`. El PNR completo solo pasa a `CANCELLED` cuando el último itinerario activo se cancela — está implementado literalmente así en `booking.py::cancel_reservation` (recuento de itinerarios `ACTIVE` restantes) |
| **Se permite reservar sin pagar** (`HELD`, retención de 20 min); el pago **no** es atómico con la reserva | Sin retención el usuario pierde la silla mientras paga; 20 min ≈ checkout con 3-D Secure + margen | SUP-7, RF-10, RF-12, DEC-5 | *"¿Por qué 20 minutos y no 5, o 60?"* | Es un supuesto declarado (SUP-7), no una medición: se puede defender con el argumento de tiempo típico de checkout, pero también hay que admitir que **no hay dato empírico** que lo confirme — es el tipo de número que en producción se ajustaría con analítica de abandono real |
| Las agencias usan **el mismo flujo de dominio**, con contrato de API y medio de pago distintos (cupo de crédito, no tarjeta) | Si el canal B2B tuviera su propia lógica de inventario, la garantía de no-sobreventa habría que probarla dos veces | RF-20 a RF-23, SUP-13, DEC-7 | *"Muéstrame en el código dónde el flujo de agencia toca el mismo bloqueo que el pasajero final"* | `booking.py::create_reservation` tiene una única rama de bloqueo (`_LOCK_INVENTORY_SQL`); lo único condicional por canal es el paso 12 (débito de `credit_used`), que ocurre **dentro de la misma transacción**. Es defendible mostrando que no hay una segunda copia de la lógica de inventario en ningún lado del código |
| El PNR es de 6 caracteres, **no secuencial**, alfabeto sin `I/O/0/1` | Un PNR predecible permite enumerar reservas ajenas | RF-13, RNF-S2 | *"¿Qué pasa si dos intentos de generación chocan?"* | `_new_pnr()` usa `secrets.choice` (aleatorio criptográfico) sobre 32 caracteres → 32⁶ ≈ 10⁹ combinaciones; el código reintenta hasta 5 veces ante colisión de `UNIQUE(pnr)` antes de fallar — la probabilidad de agotar 5 intentos es despreciable a esta escala |
| Los infantes **no consumen inventario** (`passenger_type <> 'INF'`) | Un infante viaja en brazos, no ocupa silla | D-3 (desviación declarada, no estaba en el diseño original) | *"¿Por qué esto no estaba en el modelo desde el principio?"* | Es honesto responder que apareció al implementar: el diccionario de datos declaraba `passenger_type` pero no decía cómo afectaba el conteo. Se corrigió de forma consistente en los cuatro caminos que tocan contadores (crear, confirmar, cancelar, expirar) — es exactamente el tipo de ajuste que el enunciado pide declarar, no ocultar |

---

## 2. Sección 5.2 — Requisitos no funcionales

| Decisión documentada | Por qué | Respaldo | Pregunta de sustentación | Cómo responder |
|---|---|---|---|---|
| **Bloqueo pesimista** como mecanismo de concurrencia (no optimista, no cola) | Con 40 solicitudes por 1 silla, el optimista produce 39 reintentos que vuelven a chocar; una cola añade infraestructura y asincronía que el volumen no justifica | RNF-C1, RNF-C2, DEC-4 | *"¿Y si la aerolínea creciera 100x, seguiría siendo la decisión correcta?"* | No, y el documento ya lo admite (5.6.3): con la pendiente medida (~5 ms/solicitud en cola) haría falta ~600 solicitudes simultáneas sobre el **mismo vuelo y cabina** para agotar el `lock_timeout` de 3 s — 15× el pico asumido. Más allá de eso, la respuesta preparada es migrar a la cola serializadora ya evaluada y descartada en DEC-4, **sin cambiar el modelo de datos** (el punto de contención sigue siendo la misma fila) |
| Disponibilidad **99,9%** y no 99,99% | 99,99% exige multi-región activa, que no puede garantizar barato la serialización del inventario — chocaría con RNF-C1 | RNF-D1 | *"¿Por qué no simplemente pagar más por la disponibilidad extra?"* | Porque no es solo dinero: una base escribible en dos regiones rompe la garantía de "exactamente una fila, un bloqueo, una decisión" que sostiene RNF-C1. **Es un trade-off técnico, no solo económico**, y el documento lo cuantifica igual: +$28,58/mes (+63%) es el costo de Multi-AZ, que sí se puede pagar sin ese conflicto |
| El sistema **nunca ve el PAN** de la tarjeta (SAQ-A, no SAQ-D) | Delegar el instrumento de pago reduce el alcance de cumplimiento PCI de una auditoría de meses a un cuestionario | SUP-14, RNF-S1, DEC-5 | *"¿Cómo se confirma entonces que el pago realmente ocurrió?"* | Por webhook firmado (HMAC) del PSP, con `UNIQUE(psp_event_id)` como mecanismo de idempotencia (RNF-C4) — verificado con la prueba de reentrega de eventos. El sistema no procesa el pago, pero sí **es responsable de su máquina de estados**: si el webhook llega tarde y el vuelo ya se llenó, hay que reembolsar automáticamente (caso límite documentado en DEC-5) |
| Primer cuello de botella al crecer 20 rutas: **CPU de búsqueda con escalas**, no almacenamiento | Los itinerarios con conexión crecen aproximadamente con el cuadrado del número de rutas que comparten aeropuerto de conexión | RNF-E1 | *"¿Cómo lo comprobarían sin esperar a tener 55 rutas reales?"* | Con una prueba de carga contra un catálogo **sintético** de 55 rutas, antes de comprometer las rutas comercialmente — está declarado como el método de verificación en el propio RNF-E1, pero **no se ha ejecutado todavía**: es una prueba planificada, no una medición real. Hay que decir esto explícitamente si se pregunta |
| Las métricas de rendimiento (RNF-P1 a P4) tienen umbral (p95 ≤ 400 ms, etc.) y método de verificación (k6) | La rúbrica exige métrica verificable, no un deseo | RNF-P1 a P4 | *"¿Corrieron esa prueba de carga? Muéstrenme el resultado"* | **No.** Solo se ejecutó y evidenció la prueba de **concurrencia** (RNF-C1 a C4), que es la que el enunciado exige explícitamente en 5.6. Las pruebas de carga de rendimiento (k6) están diseñadas y el umbral está declarado, pero **no hay evidencia empírica** — es un vacío real, hay que admitirlo en vez de insinuar que se corrió |

---

## 3. Sección 5.3 — Modelo entidad-relación

| Decisión | Por qué | Respaldo | Pregunta de sustentación | Cómo responder |
|---|---|---|---|---|
| **DEC-1** — `scheduled_flight` (plantilla) ≠ `flight_instance` (fecha concreta) | Sin la separación, cambiar un horario recurrente exige `UPDATE` masivo con riesgo de tocar fechas vendidas; y la capacidad es de la instancia, no del programado | RF-24, RF-25, RF-28, SUP-2 | *"El 14 de junio cambian el avión de un A320 a un ATR-72. ¿Qué pasa con las reservas ya hechas?"* | `flight_instance.aircraft_id` cambia; `flight_inventory` (por instancia × cabina) debe reconciliarse contra la nueva capacidad. Si `seats_sold` de una cabina supera la nueva capacidad, hay un caso de **sobreventa inducida por cambio de aeronave** que el modelo puede *representar* (RF-28) pero cuyo flujo de reacomodación **no está implementado** — es honesto decir que quedó como diseño (RF-28 = ○ en el estado de implementación) |
| **DEC-2** — `itinerary` como entidad propia entre `reservation` y `flight_instance` (no una tabla puente plana) | La tabla puente plana no puede responder si dos tramos son una conexión o dos compras independientes, ni sobrevivir a una cancelación parcial | RF-08, RF-09, RF-16, RNF-C2 | *"¿No es una entidad de más? ¿Por qué no bastaba `reservation ↔ flight_instance`?"* | Porque el itinerario y el tramo son **granos distintos**: el itinerario es la unidad de cancelación y tarifación (¿este viaje se cancela completo?), el tramo es la unidad de inventario (¿qué fila se bloquea?). Colapsarlos obligaría a inventar esa distinción en la aplicación en vez del modelo — y es justo el tipo de decisión que la rúbrica premia (Modelo E-R, 15%) |
| **DEC-3** — inventario por **conteo por cabina** (`flight_inventory`), no por silla individual | Con sillas individuales dos compradores tocan filas distintas: nadie se bloquea, pero nadie controla el total — el `COUNT` para saber cuántas quedan es exactamente la condición de carrera que hay que evitar | RF-10, RF-27, RNF-C1, SUP-11 | *"Entonces, ¿cómo evitan que dos pasajeros elijan la misma silla física?"* | Con una restricción distinta y aparte: `UNIQUE(flight_instance_id, seat_number)` en `seat_assignment`. Es deliberado separar los dos problemas: **capacidad** (cuántas sillas quedan, se resuelve con bloqueo pesimista sobre una fila) y **unicidad** (qué silla exacta, se resuelve con una restricción de unicidad normal, sin necesidad de bloqueo). El error común sería intentar resolver ambos con el mismo mecanismo |
| 3FN estricta en el OLTP, con **tres excepciones nombradas** (agregados de inventario, montos congelados, timestamps UTC precalculados) | Un modelo que sirve para todo queda mal normalizado para transaccionar y mal desnormalizado para analizar | §5.3.2 | *"¿No es eso romper la normalización que acaban de justificar?"* | No: son excepciones **nombradas y justificadas una por una**, no una desnormalización generalizada. `seats_sold`/`seats_held` son redundantes con `ticket`/`itinerary_segment`, pero la consistencia se garantiza porque **solo** el módulo de inventario los toca, siempre en la misma transacción — es la diferencia entre "desnormalizar por pereza" y "desnormalizar por una razón de concurrencia documentada" |

---

## 4. Sección 5.4 — Arquitectura del backend

| Decisión | Por qué | Respaldo | Pregunta de sustentación | Cómo responder |
|---|---|---|---|---|
| **DEC-6** — Monolito modular (4 módulos, 1 despliegue, 1 base) con réplicas de lectura, no microservicios | Retener un itinerario multi-tramo exige actualizar N filas atómicamente; con servicios separados eso sería una saga con compensaciones, y durante la ventana de compensación el sistema estaría temporalmente sobrevendido | RNF-C1, RNF-P1, RNF-E3 | *"Si el equipo creciera a 20 personas, ¿seguiría siendo monolito?"* | El documento ya fija un umbral explícito de cuándo dividir (§9.1 / DEC-6): más de 8 instancias en pico sostenido de búsqueda, o equipo de más de ~15 personas. Tener ese número escrito de antemano es lo que evita que la pregunta se responda por instinto en la sustentación |
| **DEC-4** — `SELECT ... FOR UPDATE` con **orden canónico** (`ORDER BY flight_instance_id, cabin_code`) antes del bloqueo | Dos itinerarios con tramos en sentido inverso (A→B→C vs C→B→A) producirían un abrazo mortal sin orden fijo | RNF-C1, RNF-C2 | *"¿Por qué el `ORDER BY` va antes del `FOR UPDATE` y no da igual el orden en SQL?"* | Porque PostgreSQL coloca el nodo `LockRows` **por encima** del nodo `Sort` en el plan de ejecución: las filas se bloquean en el orden ya ordenado, no en el orden en que llegan del disco. Es una propiedad del motor, no una convención de estilo — está verificado en la implementación real (`booking.py::_LOCK_INVENTORY_SQL`) |
| **DEC-5** — pago delegado al PSP, pero **la máquina de estados de la venta es propia** | El PSP no sabe nada de inventario ni de retenciones; confundir "delegar el instrumento" con "delegar la venta" es el error común | RNF-S1, SUP-14 | *"¿Qué pasa si el webhook del PSP llega después de que la retención ya expiró?"* | Caso límite explícitamente resuelto en el diseño (tabla de DEC-5, 5.4): se reintenta la retención; si hay cupo, confirma; si no, reembolso automático y notificación. **No está implementado en el código actual** — `confirm_reservation` asume que la reserva sigue en `HELD` y lanza `InvalidState` si no — es una brecha real entre diseño e implementación que conviene admitir si se pregunta directamente por ese caso |
| **DEC-7** — API B2B separada (`/b2b/v1/**`), dominio compartido | Son contratos con ciclos de vida distintos: la web se cambia el martes, un integrador B2B necesita meses de aviso | RF-20 a RF-22, RNF-E2, RNF-S5 | *"En el código veo un solo `main.py` con un solo conjunto de rutas. ¿Dónde está la separación?"* | **No está implementada como superficie separada** — está declarado explícitamente como fuera de alcance en 5.6.1: "el descuento de agencia y el cupo de crédito sí están implementados en el flujo, pero expuestos por la misma superficie". La decisión de diseño (DEC-7) es real y justificada; lo que falta es el segundo prefijo de ruta y las cuotas por credencial, que quedaron fuera del alcance mínimo exigido por 5.6 |

---

## 5. Sección 5.5 — Arquitectura del frontend

> No hay implementación (correcto: el enunciado no la exige en 5.6, solo el diseño). Las preguntas de sustentación aquí son puramente de diseño.

| Decisión | Por qué | Respaldo | Pregunta de sustentación | Cómo responder |
|---|---|---|---|---|
| **DEC-12** — Híbrido SSR (descubrimiento) + SPA (flujo de compra) | El tiempo que importa comercialmente es el del primer resultado visible, no el de la API; una SPA pura añade tres saltos en serie (HTML→JS→API) | RNF-P1, RNF-P2, RE-2 | *"¿Por qué no todo SSR, si igual sirve el primer resultado rápido?"* | Porque el flujo de compra tiene mucho estado (pasajeros, sillas, reloj de retención de 20 min) y SSR recargaría la página en cada paso, rompiendo justo el reloj que sostiene DEC-13. Usar un solo modo optimiza la mitad del recorrido |
| **DEC-13** — Notificación de pérdida de disponibilidad en **tres capas** (reloj de retención, revalidación, SSE solo en el mapa de sillas) | No hace falta WebSocket en toda la aplicación; el caso mayoritario (ya tengo la retención) no puede perder la silla por otro | RNF-C1, RNF-C3, RE-2 | *"¿Por qué SSE y no WebSocket?"* | El flujo es unidireccional servidor→cliente (el servidor avisa qué sillas se ocuparon, el cliente no necesita responder), y SSE reconecta solo sin código adicional — usar WebSocket sería infraestructura bidireccional para un caso que no la necesita |
| **DEC-15** — moneda **fija durante la reserva**, el frontend nunca reconvierte | Un precio que cambia al consultarlo es inaceptable contable y legalmente | SUP-12, RF-04 | *"¿Qué pasa si el usuario cambia de moneda a mitad de la búsqueda?"* | Se trataría como una nueva sesión de búsqueda: el precio ya cotizado en la moneda anterior no se recalcula, se descarta y se vuelve a buscar. Es consistente con SUP-12: el precio se ancla en el momento de la cotización, no se recalcula nunca |

---

## 6. Sección 5.6 — Implementación del backend

| Punto | Evidencia | Respaldo | Pregunta de sustentación | Cómo responder |
|---|---|---|---|---|
| La prueba de concurrencia usa **hilos reales del SO + `threading.Barrier` + 4 procesos uvicorn**, no `asyncio.gather` | `backend/scripts/concurrency_stress.py`, `backend/tests/test_concurrency.py` | RNF-C1 | *"¿Cómo sé que su prueba no habría pasado igual sin ningún bloqueo?"* | Es la pregunta correcta y la respuesta está documentada en la bitácora (P-06): la primera versión de la prueba usaba `asyncio.gather` con un solo worker — habría pasado con cualquier implementación porque el paralelismo cooperativo se serializa solo. Se corrigió a hilos reales + 4 procesos **precisamente** para que la contención se viera forzada a resolverse en PostgreSQL y no en la aplicación |
| Se verifica contra `v_oversell_check` (estado real de la base), no solo contra códigos HTTP | Resultado: 0 violaciones en 3 escenarios (2×1, 40×1, 60×5) | RNF-C1 | *"¿No basta con que la API devuelva 409 a los que pierden?"* | No: "la API respondió bien" ≠ "la base quedó consistente". Una implementación con una condición de carrera podría devolver 409 a *algunos* mientras igual sobrevende — por eso la aserción final es contra el inventario real, no contra el código de respuesta |
| Siete ajustes de diseño→código declarados (D-1 a D-7), incluido uno (D-7) donde **la prueba falló contra la propia garantía del sistema** (no se pudo `DELETE` el log de auditoría) | `docs/04-implementacion-backend.md` §5.6.4 | RNF-A2 | *"¿Por qué no simplemente corregir la prueba y seguir?"* | Se corrigió (con `TRUNCATE` en vez de `DELETE`), pero el punto que vale la pena decir en la sustentación es el inverso: que borrar el log de auditoría sea difícil **incluso desde una prueba propia** es la evidencia más fuerte de que RNF-A2 (inmutabilidad) está realmente implementado a nivel de permisos y trigger, no solo declarado en un documento |
| El código **no** implementa: API B2B separada, check-in/asignación de sillas, compra de auxiliares, notificaciones, materialización programada como job | `docs/DOCUMENTO-FINAL.md` §11.1 | — | *"¿Por qué faltan estas cosas si estaban en el diseño de 5.4/5.5?"* | Están declaradas explícitamente como fuera de alcance de 5.6, que exige una implementación "funcional aunque parcial" cuyo propósito es validar que el diseño es implementable, no construir el sistema completo. La lista de lo que falta está en el propio documento, no oculta — es justo la postura que el enunciado premia |

---

## 7. Sección 5.7 — AWS: ETL, catálogo y costos

| Decisión | Por qué | Respaldo | Pregunta de sustentación | Cómo responder |
|---|---|---|---|---|
| **Glue Python Shell (0,0625 DPU)**, no Glue Spark | El volumen (~15.000 tiquetes/mes) cabe en un proceso; Spark cobra un mínimo de 2 DPU: ~16× más caro y más lento por el arranque del clúster | RF-30, RNF-P4, pilar COS/SOS | *"¿Y si el volumen creciera 100×, seguiría siendo Python Shell?"* | No, y el documento lo admite: a partir de ahí la migración a Spark sería "un cambio de `--command` y de las funciones de carga, sin tocar el modelo" — la elección actual es correcta para el volumen actual, no una apuesta permanente |
| El ETL **transforma**, no copia tal cual: tres hechos (`fact_ticket_sale`, `fact_flight_occupancy`, `fact_reservation_lifecycle`), uno por pregunta de negocio del enunciado | Copiar `ticket` tal cual reproduciría en la analítica el mismo problema de 6 `JOIN` por consulta que tiene el OLTP | §13.2 | *"¿Por qué el grano es el tiquete y no la reserva?"* | Es el grano más fino con valor monetario: desde el tiquete se agrega a ruta, tarifa, vuelo o fecha; al revés no se puede. Con la reserva como grano sería imposible responder "ingresos por tarifa" en un itinerario multi-tramo con clases distintas por tramo |
| `reservation_event` (creado para RNF-A1, auditoría) es la **única fuente posible** de los patrones de cancelación | Una reserva cancelada que solo guarda su estado final no dice cuándo se canceló ni cuánto vivió | DEC-10, DEC-11 | *"¿Cómo conecta la arquitectura transaccional con el pipeline de Big Data?"* | Esta es la respuesta preparada y la más defendible de toda la sección: un requisito de **cumplimiento** (RNF-A1) terminó habilitando una capacidad **analítica** que de otra forma no existiría. No fue diseñado como fuente analítica desde el principio — se descubrió al construir el ETL |
| ETL diario a las 03:00 COT, no continuo | Las tres preguntas de negocio son tácticas, no operativas; nadie cambia una ruta por lo que pasó hace 10 minutos | §13.2, pilar COS | *"Si es tan barato correrlo más seguido (según su propia tabla de costos), ¿por qué no lo hacen?"* | Porque la restricción a diario **no es económica** (×10 de frecuencia sube el costo apenas 2%, según la propia proyección) — es de valor de negocio. Confundir ambas cosas sería negar una mejora casi gratis por una razón que no aplica; el documento lo dice explícitamente y es una buena señal de rigor tenerlo tan claro |
| **`LabRole`** compartido en vez de roles por componente | El Learner Lab no permite crear roles ni políticas IAM | A-2 (desviación declarada) | *"Eso viola el principio de mínimo privilegio que mencionan en el pilar de Seguridad"* | Correcto, y está declarado así explícitamente (no presentado como diseño intencional): "en producción correspondería un rol propio con privilegio mínimo... el Learner Lab no permite crear roles". Es una limitación del entorno de práctica, no una decisión de arquitectura — la diferencia importa y hay que mantenerla clara en la respuesta |
| **Todo lo anterior está diseñado y codificado, pero al día de hoy no hay evidencia de haberse desplegado en AWS** | — | Anexo D del documento final | *"Muéstrenme el catálogo de Glue con las tablas registradas"* | **No se puede, todavía.** Es el hueco #1 de esta guía (§0). Ver §8 más abajo para el plan de despliegue |

---

## 8. Supuestos (`SUP-n`) — vigencia frente al estado actual del código

| ID | Supuesto | ¿Sigue vigente? | Nota |
|---|---|---|---|
| **SUP-1** | 35 rutas, 18 aeropuertos | ✅ Vigente como supuesto de diseño | El *seed* de prueba usa solo **6 aeropuertos y 12 rutas** (`docs/DOCUMENTO-FINAL.md` §11.1). No es una desviación del supuesto — es una reducción deliberada del volumen de datos de demostración, razonable para un entorno de prueba. Aclarar esta diferencia si se pregunta por el número real de filas en la base de prueba |
| **SUP-2** | ~120 vuelos/día, 12 aeronaves, 3 configuraciones | ✅ Vigente | El *seed* materializa 854 instancias de vuelo desde 14 vuelos programados — consistente con una muestra reducida del mismo modelo, no con un supuesto distinto |
| **SUP-3** | Horizonte de venta de 360 días (~43.000 instancias vivas) | ⚠️ Vigente como supuesto, **no verificado en escala** | La base de prueba tiene 854 instancias, dos órdenes de magnitud por debajo del volumen de producción asumido. Ningún problema de rendimiento a esa escala reducida garantiza nada sobre la escala real — es un punto débil si el docente pregunta por rendimiento a volumen real |
| **SUP-4** | 30–120 búsquedas/s base, ×5 en temporada alta, relación 80:1 | ⚠️ Vigente como supuesto, **sin prueba de carga ejecutada** | RNF-P1 a P3 declaran k6 como método de verificación; no hay evidencia de que se haya corrido. Ver §2 de esta guía |
| **SUP-5** | 40 solicitudes concurrentes como pico de contención | ✅ Vigente y **verificado empíricamente** | Es el supuesto mejor evidenciado de todo el proyecto: probado en 3 escenarios (2×1, 40×1, 60×5), con 0 violaciones |
| **SUP-6** | Sin sobreventa deliberada; `oversell_factor` como columna, no constante | ✅ Vigente e implementado | `flight_inventory.oversell_factor` existe en el esquema y en el `CHECK` (`ck_inventory_no_oversell`); confirmado en `01_schema.sql` |
| **SUP-7** | Retención `HELD` de 20 minutos, pago no atómico | ✅ Vigente e implementado | `settings.hold_minutes` controla el valor; usado en `booking.py::create_reservation` |
| **SUP-8** | Cambios de fecha = cancelación + reemisión en el mismo PNR | ⚠️ Vigente como diseño, **implementación parcial** | RF-17 está marcado `◐` en el documento final: "reutiliza el mismo camino de retención de RF-10" pero no hay un endpoint dedicado de cambio de fecha. Si se pregunta por el flujo de cambio, hay que ser explícito en que es composición manual de cancelar + crear, no una operación atómica propia |
| **SUP-9** | Sin programa de viajero frecuente; `loyalty_id` reservado y nulo | ✅ Vigente | No contradicho por el código; campo no usado, como se planeó |
| **SUP-10** | Equipaje de mano incluido; bodega como `ancillary` | ⚠️ Vigente como diseño, **sin endpoint de compra** | Tabla `ancillary` existe y el ETL la consume (para ingresos), pero RF-19 está marcado `◐`: no hay ruta HTTP para comprarlo |
| **SUP-11** | Silla opcional/con costo en económica, obligatoria en check-in, no participa del control de sobreventa | ✅ Vigente en el modelo; check-in **no implementado** | `seat_assignment` existe separada de `flight_inventory`, tal como exige DEC-3. RF-06 y RF-27 marcados `◐`/`◐`: el mapa de sillas y el check-in no tienen endpoint |
| **SUP-12** | Monedas COP/USD, idiomas ES/EN, precio no se reconvierte | ✅ Vigente en el backend (campo `currency`); **i18n es solo diseño** | No hay frontend implementado, así que DEC-15 (rutas por idioma, `Intl`) no tiene código que lo verifique — es esperado, 5.6 no lo exige |
| **SUP-13** | Agencias: tarifa neta + comisión, cupo de crédito, credenciales propias | ✅ Vigente y **mayormente implementado** | `agency`, `credit_used`/`credit_limit`, descuento y comisión están todos en `booking.py`. Lo que falta es la superficie de API separada (DEC-7) y las credenciales OAuth2 propias (RF-20 `◐`) |
| **SUP-14** | Pago delegado a PSP externo, *hosted checkout* + webhook, sin PAN | ✅ Vigente e implementado **con un PSP simulado** | `payment.psp_provider = 'MOCK_PSP'` en el código — correcto para el alcance de 5.6, pero hay que ser explícito en que no hay integración real con un PSP si se pregunta |
| **SUP-15** | El sistema es fuente de verdad de la venta, no de la operación; recibe eventos de un sistema de operaciones externo | ⚠️ Vigente en el modelo, **sin endpoint de ingesta** | `flight_instance.status`/`aircraft_id` están preparados para recibir eventos (RF-28), pero no existe la API de ingesta (A6) — marcado `○` |
| **SUP-16 / PE-0** | Restricción confidencial de la sección 3 | 🔴 **No resuelto** | Ver §4 de esta guía |

---

## 4. Verificación de la restricción específica del equipo (sección 3 del enunciado)

**No se encontró ninguna restricción integrada en `docs/00` a `05` ni en `DOCUMENTO-FINAL.md`.** Los tres documentos de diseño contienen el mismo punto de extensión sin resolver:

> *"PE-0 — A la fecha de redacción de esta parte, el equipo aún no ha recibido la restricción específica de la sección 3."* (`01-contexto-y-requisitos.md`, `DOCUMENTO-FINAL.md` §2.3)

Y `DOCUMENTO-FINAL.md`, Anexo D, lo lista explícitamente como pendiente:

> `- [ ] Integrar la restricción confidencial de la sección 3 en los tres puntos preparados (PE-0, §2.3).`

Se confirmó en esta sesión que **no hay ninguna restricción asignada al equipo**. Dado que el enunciado (§3) dice que *"cada equipo recibirá... una restricción adicional"* y la trata como obligatoria para la sustentación (§7: *"todos los integrantes deben poder sustentar... incluida la restricción específica asignada en la sección 3"*), esto deja dos lecturas posibles y **conviene resolver cuál es la correcta antes de entregar, no durante la sustentación**:

1. **El equipo genuinamente no recibió restricción** (posible: no todos los cursos aplican la sección 3 de la misma forma, o quedó pendiente de asignar). En ese caso, el riesgo no es el contenido — es que el documento **hoy dice "aún no recibida"**, una frase que suena a pendiente, no a "confirmado que no aplica". Si el profesor pregunta por la restricción en la sustentación esperando una respuesta y el equipo dice "no nos llegó", sin poder mostrar que lo confirmó con antelación, la sustentación de esa parte queda mal parada por sorpresa, no por el contenido.
2. **Hay una restricción que no llegó a este repositorio** (por ejemplo, se comunicó por otro canal — correo, plataforma del curso — y no se trasladó a los documentos).

**Recomendación concreta, antes de la entrega:**

- Confirmar con el docente, por escrito (correo o el canal del curso), si existe o no una restricción asignada al equipo. Guardar esa confirmación.
- Si la respuesta es "no hay restricción": actualizar `PE-0` en los tres documentos (`01-contexto-y-requisitos.md`, `02-modelo-y-arquitectura.md` si aplica, `DOCUMENTO-FINAL.md`) para que diga explícitamente *"se confirmó con el docente el [fecha] que no se asignó restricción específica al equipo"*, en vez de "aún no recibida". Es una edición de una frase, no un rediseño — el punto de extensión PE-0 ya deja preparados los tres lugares donde entraría (supuestos, requisitos, decisiones) precisamente para este caso.
- Si aparece una restricción real: los tres documentos ya están estructurados para absorberla sin rediseño (ver la tabla de ejemplos en `PE-0`, que muestra cómo aterrizarían cuatro tipos distintos de restricción). No es trabajo perdido si llega tarde.

Este es el hueco #2 de esta guía. Es barato de cerrar (una confirmación + una frase), pero es el que más directamente amenaza la sustentación oral si se deja sin resolver, porque el enunciado lo trata como eliminatorio.

---

## 5. Checklist — rúbrica de evaluación (sección 8 del enunciado) vs. estado actual del repositorio

| Criterio | Peso | Estado hoy | Nivel actual | Por qué (razón concreta) |
|---|---|---|---|---|
| **Requisitos funcionales** | 10% | RF completos, preguntas guía resueltas, supuestos declarados con "qué se rompe si cambia" | **Sobresaliente, con una condición pendiente** | Cumple explícitamente "resuelven las preguntas guía" y "declaran supuestos". Lo único que falta para el texto literal de "Sobresaliente" es *"integran la restricción específica del equipo"* (§4 de esta guía) — hoy ese punto está abierto, no cerrado en falso: el documento admite que no se integró, no lo oculta |
| **Requisitos no funcionales** | 10% | 19 RNF con métrica, umbral, verificación y riesgo; concurrencia con evidencia real | **Sobresaliente** | La rúbrica pide en particular trazabilidad de la concurrencia a un riesgo de negocio — eso está cubierto de sobra (RNF-C1↔RE-1, con prueba real). El punto débil (RNF-P1-P4 sin prueba de carga ejecutada) no degrada el criterio: la métrica está declarada y es verificable, solo no se ha verificado todavía; eso es exactamente lo que separa "Aceptable" (sin métrica) de "Sobresaliente" (con métrica, aunque la ejecución sea un pendiente) |
| **Modelo entidad-relación** | 15% | DEC-1/2/3 resuelven escalas, recurrencia y sobreventa explícitamente, con alternativa descartada y justificación en cada una | **Sobresaliente** | Cumple literalmente el criterio de la rúbrica: "resuelve explícitamente escalas, vuelos recurrentes e inventario de sillas, con justificación" |
| **Arquitectura backend y frontend (diseño)** | 15% | 15 decisiones (DEC-1 a DEC-15), cada una con el RNF/RF que la origina y la alternativa descartada | **Sobresaliente** | El criterio pide conexión explícita a un RNF/RF "especialmente el manejo de concurrencia" — DEC-4 es probablemente la decisión mejor sustentada de todo el documento, con las tres alternativas comparadas contra el escenario real (40 solicitudes/1 silla) |
| **Primera implementación del backend** | 10% | API funcional, fiel al diseño con 7 desviaciones declaradas (D-1 a D-7) y evidencia concreta de la prueba de concurrencia (3 escenarios, JSON + MD) | **Sobresaliente** | Cumple el criterio literal: "implementación funcional, fiel al diseño (o con desviaciones justificadas), con evidencia concreta de la prueba de concurrencia". Las desviaciones están declaradas con su motivo, no ocultas |
| **Implementación ETL, catálogo Glue y costos** | **20%** | **Desplegado y verificado en AWS real.** Dos RDS PostgreSQL 16.15, ambas registradas en el Glue Data Catalog (30 + 13 objetos), job Python Shell `SUCCEEDED` en 112 s, tres hechos poblados (157 / 1.342 / 122 filas), tres vistas de negocio respondiendo. Servicios justificados con RF/RNF y pilares; costos con supuestos declarados y escenario ×10 separado en dos ejes | **Sobresaliente** | Cumple los tres elementos que la rúbrica exige para el nivel alto: implementación funcional, servicios justificados con requisito **y** pilar Well-Architected, y costos con supuestos y crecimiento. Además, las seis desviaciones de entorno (A-1 a A-6) están declaradas en vez de omitidas, que es lo que el enunciado pide explícitamente |
| **Claridad y trazabilidad del documento** | 10% | Matriz de trazabilidad completa en ambos sentidos (riesgo→requisito→decisión→código→prueba, y decisión→requisito que la origina), referencias cruzadas en cada sección | **Sobresaliente** | Cumple literalmente: "una decisión de arquitectura puede rastrearse hasta el requisito específico que la origina" — es, de hecho, el eje organizador de todo el documento |

**Lectura agregada:** con el despliegue de AWS ya ejecutado, **los siete criterios están en condición de "Sobresaliente"**, con una sola condición abierta: la restricción de la sección 3 (§4 de esta guía), que afecta al criterio de requisitos funcionales (10%) y se cierra con una confirmación por escrito del docente más una frase en el documento. Todo lo demás es sustentable tal como está.

---

## 6. Plan de acción priorizado antes de la entrega

| # | Acción | Esfuerzo | Bloquea |
|---|---|---|---|
| ~~1~~ | ~~Desplegar 5.7 en AWS y capturar la evidencia~~ ✅ **Hecho.** Evidencia en [`evidencia/aws-despliegue.md`](evidencia/aws-despliegue.md) | — | — |
| 1 | **Confirmar con el docente si existe restricción de sección 3** y actualizar `PE-0` con el resultado. **Ahora es el único hueco abierto** | Bajo (un correo + una edición de texto) | 10% de la rúbrica, condición de la sustentación oral (§7 del enunciado) |
| 2 | Tomar las **capturas de la consola de Glue** mostrando ambas bases del catálogo (punto 8 de la lista de §13.7, que pide captura además de los comandos) | Trivial | Evidencia visual del entregable |
| 3 | Completar portada de `DOCUMENTO-FINAL.md` (equipo, integrantes, fecha, enlace de repo) | Trivial | Entregable formal |
| 4 | Ampliar la bitácora de prompts (Anexo A) con los prompts propios de cada integrante, no solo los de esta sesión | Bajo | Requisito explícito de la sección 4 del enunciado |
| 5 | Verificar los precios de AWS de §13.4 contra la calculadora vigente antes de presentar | Bajo | Precisión de la proyección de costos |
| 6 | (Opcional, refuerza pero no bloquea) Ejecutar una prueba de carga k6 real para RNF-P1/P2/P3 | Medio | Cierra el único punto débil de la sección de RNF |

---

## 7. Preparación para variaciones del escenario en la sustentación oral

El enunciado (§7) advierte que el docente puede plantear variaciones no cubiertas en el documento. Estas son las que, según el propio contenido de los documentos, tienen más probabilidad de aparecer — porque son precisamente los puntos donde el equipo ya identificó qué se rompe:

| Variación hipotética | Qué se rompe (según los propios documentos) | Respuesta preparada |
|---|---|---|
| *"La aerolínea ahora permite overbooking del 5%"* | Nada estructural — es la extensión ya prevista (PE-1) | `UPDATE flight_inventory SET oversell_factor = 1.05` — cero cambios de esquema ni de código, porque `oversell_factor` es columna, no constante, desde el diseño original |
| *"Una agencia solo puede ver un cupo asignado, no la disponibilidad real"* | Requiere una entidad nueva (`agency_allotment`) colgando de `flight_inventory` | El bloqueo de DEC-4 pasaría a operar sobre dos filas relacionadas en la misma transacción — cambio de alcance moderado, no de mecanismo |
| *"El sistema debe operar sin conexión en aeropuertos remotos"* | Es el caso de mayor impacto, ya señalado en `PE-0`: rompe la garantía de serialización de DEC-4 | Admitir directamente que **no hay solución barata**: obligaría a inventario particionado con reconciliación posterior, lo cual reintroduce el riesgo de sobreventa que todo el diseño actual existe para evitar. Es la pregunta donde la respuesta correcta es reconocer el límite del diseño, no forzar una salida |
| *"¿Qué pasa si dos itinerarios comparten un tramo en sentido inverso?"* | Nada — es exactamente el caso que el orden canónico de DEC-4 previene | Explicar el mecanismo de `ORDER BY` antes de `FOR UPDATE` (§4 de esta guía) |
| *"¿Y si el volumen se multiplica por 100, no por 10?"* | La migración de Glue Python Shell a Glue Spark, y de bloqueo pesimista a la cola serializadora ya evaluada | Ambas respuestas ya están en los documentos (§13.2 para el ETL, DEC-4 para concurrencia) — es cuestión de tenerlas ensayadas, no de improvisar |

---

## 8. Continúa en el chat: despliegue de la sección 5.7 en AWS

Dado que el checklist de §5 confirma que 5.7 no está desplegada, la guía paso a paso para credenciales, carga de esquemas, `setup_aws.sh` y verificación con `aws glue get-tables` se hace de forma interactiva en la conversación (no aquí), porque depende de la sesión activa del AWS Academy Learner Lab y de depurar en vivo lo que vaya apareciendo.

---

## 9. Runbook: control de costos y cómo revivir el entorno para la sustentación

### 9.1 Estado en que quedó el laboratorio

Todas las instancias RDS quedaron **detenidas** para no consumir el presupuesto de $50 del Learner Lab. Los objetos que **no** cuestan nada y siguen en pie: el Glue Data Catalog (ambas bases registradas), las conexiones JDBC, los crawlers, el job de ETL, el bucket de S3 y los logs.

| Concepto | Costo mientras está detenido |
|---|---|
| Cómputo de las 5 instancias RDS | **$0** (es el ahorro: ~$2,45/día) |
| Almacenamiento (5 × 20 GB gp3) | ~$0,38/día — **el almacenamiento se sigue cobrando aunque la instancia esté apagada** |
| Glue Data Catalog, conexiones, crawlers en reposo | $0 (dentro de la capa gratuita) |
| S3 (script + wheels, ~4,8 MB) | ~$0,0001/día |

> ⚠️ **AWS reinicia automáticamente una instancia RDS detenida a los 7 días.** Si el entorno va a estar sin usarse más de una semana, hay que volver a detenerlas o borrarlas.

### 9.2 Revivir el entorno antes de la sustentación

Toma ~5-10 minutos. Con las credenciales del lab ya en `~/.aws/credentials`:

```bash
aws rds start-db-instance --region us-east-1 --db-instance-identifier airline-oltp
```

```bash
aws rds start-db-instance --region us-east-1 --db-instance-identifier airline-olap
```

Esperar a que queden disponibles:

```bash
aws rds wait db-instance-available --region us-east-1 --db-instance-identifier airline-oltp
```

Reactivar el trigger nocturno (se detuvo para que no acumulara corridas fallidas con las bases apagadas):

```bash
aws glue start-trigger --region us-east-1 --name airline-etl-nightly
```

### 9.3 Comandos de demostración en vivo

El catálogo de Glue responde **aunque las bases estén apagadas** (son metadatos), así que estos dos funcionan siempre y son los que prueban el requisito del enunciado:

```bash
aws glue get-tables --database-name airline_oltp_catalog --query 'TableList[].Name' --region us-east-1
```

```bash
aws glue get-tables --database-name airline_olap_catalog --query 'TableList[].Name' --region us-east-1
```

Ejecutar el ETL a demanda (requiere las bases encendidas):

```bash
aws glue start-job-run --job-name airline-etl-oltp-to-olap --region us-east-1
```

### 9.4 Si se quiere mostrar la API en vivo

La API corre en local contra la RDS, así que hace falta reabrir el acceso temporalmente (desviación **A-5**): poner la instancia en `--publicly-accessible`, añadir una regla de entrada al 5432 en `sg-00df9bd0b60e61951` desde la IP pública del momento, y revertir ambas cosas al terminar. No es necesario para defender la sección 5.7 — solo para demostrar 5.6 contra AWS en vez de contra Docker local.
