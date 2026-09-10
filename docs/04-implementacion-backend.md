# Sistema de Reservas — Aerolínea Regional
## Parte IV — Primera implementación del backend (sección 5.6)

> Código: [`backend/`](../backend/) · Guía de ejecución: [`backend/README.md`](../backend/README.md)

---

## 5.6.1 Qué se implementó

El enunciado exige una implementación **funcional aunque parcial**, cuyo propósito no es construir el sistema completo sino **validar que las decisiones de diseño son implementables y consistentes entre sí**. Se implementó el alcance mínimo exigido más lo estrictamente necesario para poder *demostrar* el mecanismo de concurrencia de extremo a extremo — porque una API que solo retiene pero nunca confirma ni cancela no permite verificar que los contadores de inventario cierran.

| Operación | Endpoint | Requisito | Estado |
|---|---|---|---|
| Buscar vuelos disponibles | `GET /api/v1/flights/search` | RF-01, RF-02 | ✅ Directos y con 1 escala, con MCT por aeropuerto |
| Crear reserva | `POST /api/v1/reservations` | RF-08, RF-09, RF-10 | ✅ Multi-tramo, multi-pasajero, **con bloqueo pesimista** |
| Consultar reserva | `GET /api/v1/reservations/{pnr}` | RF-15 | ✅ Con control PNR + apellido |
| Confirmar pago | `POST /api/v1/reservations/{pnr}/confirm` | RF-12 | ✅ Idempotente (webhook simulado) |
| Cancelar | `POST /api/v1/reservations/{pnr}/cancel` | RF-16 | ✅ Libera inventario de inmediato |
| Expirar retenciones | `POST /internal/jobs/expire-holds` | RF-11 | ✅ Con `SKIP LOCKED` |
| Verificar invariante | `GET /internal/oversell-check` | RNF-C1 | ✅ Aserción en vivo |

**Fuera de alcance en esta iteración**, declarado: API B2B separada (DEC-7 — el descuento de agencia y el cupo de crédito sí están implementados en el flujo, pero expuestos por la misma superficie), check-in y asignación de sillas (RF-27), productos auxiliares (RF-19), notificaciones (RF-29), materialización programada de instancias (RF-25 — se ejecuta en el *seed*).

**Base de datos:** PostgreSQL 16, esquema `airline`, **26 tablas** en 3FN con las tres denormalizaciones declaradas en 5.3.2, más 4 vistas de apoyo. DDL completo en [`backend/sql/01_schema.sql`](../backend/sql/01_schema.sql).

---

## 5.6.2 Cómo el código refleja la decisión de concurrencia

DEC-4 estableció **bloqueo pesimista sobre la fila de `flight_inventory`**. La sección crítica está en [`backend/app/services/booking.py`](../backend/app/services/booking.py), función `create_reservation`, y consta de tres piezas que trabajan juntas.

**1. La consulta que bloquea:**

```sql
SELECT flight_instance_id, cabin_code, capacity, seats_sold, seats_held,
       oversell_factor, version,
       FLOOR(capacity * oversell_factor) - seats_sold - seats_held AS seats_available
FROM flight_inventory
WHERE (flight_instance_id, cabin_code)
      IN (SELECT * FROM unnest(CAST(:ids AS bigint[]), CAST(:cabins AS varchar[])))
ORDER BY flight_instance_id, cabin_code
FOR UPDATE
```

El `ORDER BY` **antes** del `FOR UPDATE` no es cosmético. PostgreSQL coloca el nodo `LockRows` por encima del `Sort`, de modo que las filas se bloquean en orden ordenado y no en orden de llegada. Sin eso, dos itinerarios que comparten tramos en sentido inverso (BOG→MDE→CTG y CTG→MDE→BOG) se bloquearían mutuamente: cada uno tendría la fila que el otro necesita. Con orden canónico el abrazo mortal es **imposible por construcción**, no improbable.

**2. Verificar todo antes de escribir nada.** RNF-C2 exige que la retención multi-tramo sea atómica. El código recorre *todos* los tramos comprobando disponibilidad y solo después ejecuta los `UPDATE`. Verificar y escribir tramo por tramo dejaría, ante un fallo en el tercer tramo, los dos primeros retenidos hasta el `ROLLBACK` — correcto, pero con una ventana de inconsistencia visible más ancha de lo necesario.

**3. La restricción de la base como última línea de defensa:**

```sql
CONSTRAINT ck_inventory_no_oversell
  CHECK (seats_sold + seats_held <= FLOOR(capacity * oversell_factor))
```

Deliberadamente redundante con la lógica de Python. Si un error de programación, una migración o una consulta manual intentaran sobrevender, la transacción falla en el motor. **La regla de negocio más importante del sistema no vive solo en el código de aplicación.** El manejador `_translate_db_error` convierte esa violación en un `409 SEAT_UNAVAILABLE` con `guard: "db_check_constraint"`, de modo que si alguna vez se dispara, queda registrado que el que salvó la situación fue el motor y no la aplicación.

**Además:**

- `SET LOCAL lock_timeout = '3s'` (DEC-4): antes que dejar crecer una espera sin límite, se falla rápido con `503 INVENTORY_BUSY`. `SET LOCAL` alcanza solo a la transacción en curso, así que no contamina la conexión al volver al pool.
- **Cero E/S externa dentro de la transacción.** Las tarifas se cotizan *antes* de tomar el bloqueo; el pago vive completamente fuera (DEC-5). Una llamada al PSP con la fila bloqueada detendría el vuelo entero mientras responde un tercero.
- El pool de conexiones (20 + 40 de desborde) está dimensionado **por encima** del número de solicitudes concurrentes de la prueba. Si el pool fuera más pequeño, la prueba mediría la cola del pool en vez del mecanismo de bloqueo, y no probaría nada sobre RNF-C1.

---

## 5.6.3 Evidencia de la prueba de concurrencia

> *Pregunta del enunciado: ¿cómo se probó específicamente el escenario de dos solicitudes simultáneas por la última silla? ¿Qué evidencia respalda que el mecanismo previene la sobreventa?*

Se probó de **dos formas independientes**, porque una sola no bastaría: una prueba que solo mira códigos HTTP podría pasar con una base sobrevendida.

### a) Script de estrés — [`backend/scripts/concurrency_stress.py`](../backend/scripts/concurrency_stress.py)

```bash
python scripts/concurrency_stress.py --workers 40 --seats 1
```

Prepara un vuelo con exactamente K sillas libres, lanza N solicitudes y produce un reporte en `docs/evidencia/concurrencia.md` (+ `.json`). Sale con código distinto de cero si hay sobreventa, así que funciona tal cual en CI.

### b) Suite automatizada — [`backend/tests/test_concurrency.py`](../backend/tests/test_concurrency.py)

```bash
pytest tests/ -v
```

Cuatro escenarios parametrizados de concurrencia, incluido **el literal del enunciado**:

| Caso | Solicitudes | Sillas | Qué demuestra |
|---|---|---|---|
| `dos-por-la-ultima` | 2 | 1 | El escenario textual del enunciado |
| `diez-por-la-ultima` | 10 | 1 | Contención moderada |
| `cuarenta-por-la-ultima` | 40 | 1 | SUP-5, el pico real de contención |
| `cuarenta-por-cinco` | 40 | 5 | K > 1: no es un caso especial de K=1 |

Más: atomicidad multi-tramo (RNF-C2), idempotencia del webhook (RNF-C4), expiración de retenciones (RF-11/RNF-C3), validación de itinerario (RF-09) y control de acceso por PNR + apellido (RNF-S2).

### Por qué la prueba es honesta y no un montaje

Este es el punto que hay que poder defender, porque una prueba de concurrencia mal construida da un falso positivo con toda facilidad:

| Decisión de la prueba | Qué evita |
|---|---|
| **Hilos reales del SO**, no corrutinas | El paralelismo cooperativo serializaría las solicitudes por sí solo. |
| **`threading.Barrier`** antes de cada POST | Sin ella, la primera solicitud terminaría antes de que la segunda empezara: no habría contención que medir. |
| **HTTP contra la API real**, con **4 procesos uvicorn** | Un solo worker podría serializar en la capa de aplicación y ocultar un fallo de la capa de datos. Con 4 procesos, la contención está obligada a resolverse en PostgreSQL. |
| **Aserción contra `v_oversell_check`**, no contra los códigos HTTP | Es la diferencia entre "la API respondió bien" y "la base quedó consistente". |
| **Documento único por worker** | Documentos repetidos introducirían contención en el `ON CONFLICT` de `passenger`, ajena a la que se quiere medir. |
| **Verificación de conciliación** contra `v_inventory_reconciliation` | Detecta que el contador materializado (5.3.2) coincida con los tramos realmente vendidos. |

### Resultado — ejecución real del 2026-09-02

Stack levantado con `docker compose up -d` (PostgreSQL 16 + 4 procesos uvicorn). Reportes completos con el detalle solicitud por solicitud en [`docs/evidencia/`](evidencia/).

| Escenario | Solicitudes | Sillas | Creadas | Rechazos `409` | Errores | **Sobreventa** | Inventario final | Reporte |
|---|---|---|---|---|---|---|---|---|
| Literal del enunciado | 2 | 1 | **1** ✅ | **1** ✅ | 0 ✅ | **0** ✅ | 11 + 1 = 12/12 | [`concurrencia-2x1.md`](evidencia/concurrencia-2x1.md) |
| SUP-5, pico de contención | 40 | 1 | **1** ✅ | **39** ✅ | 0 ✅ | **0** ✅ | 11 + 1 = 12/12 | [`concurrencia.md`](evidencia/concurrencia.md) |
| K > 1 | 60 | 5 | **5** ✅ | **55** ✅ | 0 ✅ | **0** ✅ | 145 + 5 = 150/150 | [`concurrencia-60x5.md`](evidencia/concurrencia-60x5.md) |

En los tres casos la cabina queda **exactamente llena** (`seats_sold + seats_held = capacity`), la vista `v_oversell_check` devuelve **cero filas**, y la conciliación entre el contador materializado y los tramos reales cuadra.

**Suite completa: 11/11 pruebas pasan** (`pytest tests/ -v`, 25,4 s), cubriendo búsqueda directa y con escalas, ciclo de vida completo, idempotencia del webhook, validación de itinerario, atomicidad multi-tramo, expiración de retenciones y los cuatro escenarios de concurrencia.

**Latencias observadas:**

| Escenario | p50 | p95 | máx | `lock_timeout` |
|---|---|---|---|---|
| 40 solicitudes / 1 silla | 119 ms | 139 ms | 140 ms | 3.000 ms |
| 60 solicitudes / 5 sillas | 255 ms | 294 ms | 303 ms | 3.000 ms |

El bloqueo serializa las solicitudes sobre una sola fila, así que la última en ser atendida espera a todas las anteriores: la latencia crece de forma lineal con el número de solicitudes en cola. Con 60 solicitudes, la cola completa se drena en **~300 ms, un orden de magnitud por debajo del `lock_timeout`**. Extrapolando esa pendiente (~5 ms por solicitud en cola), harían falta del orden de **600 solicitudes simultáneas sobre el mismo vuelo y cabina** para empezar a agotar el tiempo de espera — quince veces el pico asumido en SUP-5.

**Esto es una propiedad del diseño, no un defecto**: es exactamente el compromiso que se aceptó en DEC-4 al preferir rechazar rápido antes que confirmar y revertir. Si la aerolínea creciera hasta niveles de contención de venta de entradas de concierto, la respuesta sería migrar a la cola serializadora que ya se evaluó y descartó — y **el modelo de datos no cambiaría**, porque el punto de contención seguiría siendo la misma fila de `flight_inventory`.

### Verificación del ETL de 5.7

El mismo stack incluye la base OLAP. Tras generar 122 reservas (95 confirmadas, 20 canceladas) y ejecutar el job:

| Tabla del modelo estrella | Filas cargadas |
|---|---|
| `dim_date` / `dim_route` / `dim_fare` / `dim_flight` / `dim_channel` | 1.095 / 12 / 48 / 854 / 3 |
| `fact_ticket_sale` | 161 |
| `fact_flight_occupancy` | 1.342 |
| `fact_reservation_lifecycle` | 127 |

Las tres vistas de negocio responden con datos reales. Un resultado que vale la pena señalar porque valida la modelación de la política tarifaria: en `v_revenue_by_fare`, la clase `P` ("Promo anticipada 21d") aparece con una **anticipación media de compra de 22,5 días**, mientras que la clase `B` ("Flexible", sin restricción) aparece con **5,0 días**. La regla `fare_rule.min_advance_days` no solo está en el modelo: se comporta como debe en los datos.

---

## 5.6.4 Ajustes que aparecieron durante la implementación

> *El enunciado es explícito: "un ajuste no es un error: es información valiosa que debe documentarse".*

### D-1 — El precio hay que congelarlo al **retener**, no al emitir

| | |
|---|---|
| **Qué decía el diseño** | DEC-5 (5.3): el precio se congela en `ticket`, que se emite al confirmar el pago. `fare` se relaciona solo con `ticket`. |
| **Qué pasó** | Al implementar el flujo real apareció una ventana de 20 minutos entre la retención (`HELD`) y la confirmación. Si una tarifa cambia en esa ventana —y las tarifas de aerolínea cambian varias veces al día— el pasajero vería un precio al reservar y se le cobraría otro al pagar. |
| **Ajuste** | Se agregaron `quoted_fare_id`, `quoted_fare_amount` y `quoted_tax_amount` a `itinerary_segment`. El precio se congela **al retener**; `ticket` lo copia al emitir. |
| **Por qué es lo correcto** | La cotización se vuelve parte de la retención: se retiene *la silla y su precio*. Es la lectura fiel de SUP-7 ("reservar sin pagar"), que implica que lo reservado incluye el precio acordado. |
| **Documento actualizado** | El DDL de 5.3 y el diccionario de datos de `itinerary_segment`. La decisión DEC-5 no cambia de sentido: se precisa **cuándo** ocurre el congelamiento. |

### D-2 — Cotizar **antes** de tomar el bloqueo

| | |
|---|---|
| **Qué decía el diseño** | El protocolo de DEC-4 mostraba bloquear, verificar, retener y crear la reserva, sin ubicar la resolución de tarifas. |
| **Qué pasó** | Resolver la tarifa elegible requiere consultar `fare` ⋈ `fare_class` ⋈ `fare_rule` por cada tramo. Dentro del bloqueo, eso alargaba la sección crítica varias veces sin ninguna necesidad. |
| **Ajuste** | La cotización se ejecuta **antes** de `SELECT … FOR UPDATE`. Es lectura pura y no participa del invariante de inventario. |
| **Impacto** | La sección crítica queda mínima. Refuerza el principio de DEC-4 de no meter nada en la transacción que no lo necesite. |

### D-3 — Los infantes no consumen inventario

| | |
|---|---|
| **Qué decía el diseño** | El diccionario definía `passenger_type ∈ {ADT, CHD, INF}` sin decir cómo afecta el conteo. |
| **Qué pasó** | Al escribir `demand[(vuelo, cabina)] += n_pasajeros` hubo que decidir qué es `n`. Un infante viaja en brazos: **no ocupa silla**. |
| **Ajuste** | `seats_needed = COUNT(*) WHERE passenger_type <> 'INF'`, consistente en creación, confirmación, cancelación y expiración — los cuatro caminos que tocan contadores. Se añadió validación: una reserva no puede ser solo de infantes. |
| **Impacto** | Es una regla de negocio que estaba implícita en el modelo y ahora es explícita. Se corrigió también `v_inventory_reconciliation`, que en su primera versión contaba tramos en vez de sillas. |

### D-4 — Los holds se expiran con `SKIP LOCKED`

| | |
|---|---|
| **Qué decía el diseño** | RF-11 pedía liberar retenciones vencidas; RNF-C3 fijaba ≤ 60 s. |
| **Qué pasó** | Con una sola instancia del job, 500 reservas vencidas se procesan en serie. Con varias instancias en paralelo, se bloquean entre sí sobre las mismas filas. |
| **Ajuste** | `SELECT … FOR UPDATE SKIP LOCKED`: cada instancia toma las filas que nadie más tiene y **omite** las ocupadas en vez de esperarlas. |
| **Impacto** | El job se vuelve horizontalmente escalable sin coordinación externa. Es el mismo motivo por el que se descartó una cola en DEC-4: PostgreSQL ya da la primitiva. |

### D-5 — Un error genérico de disponibilidad no sirve al frontend

| | |
|---|---|
| **Qué pasó** | La primera versión devolvía `409 Conflict` con un mensaje de texto. DEC-13 exige que la interfaz **no muestre un error genérico** sino que recargue disponibilidad y proponga alternativas — y para eso necesita distinguir este 409 de cualquier otro. |
| **Ajuste** | Errores de dominio con **código estable** (`SEAT_UNAVAILABLE`, `INVENTORY_BUSY`, `INVALID_ITINERARY`, `CREDIT_LIMIT_EXCEEDED`) más un objeto `details` con el `flight_instance_id`, la cabina, lo pedido y lo disponible. |
| **Impacto** | El frontend puede implementar DEC-13 sin analizar cadenas de texto. Un rechazo bien manejado recupera la venta; un `Error 409` en rojo la pierde. |

### D-6 — Tres fallos de tipado de parámetros que solo aparecen al ejecutar

Los tres se manifestaron al levantar el stack por primera vez y ninguno es visible leyendo el código. Se documentan porque son exactamente el tipo de cosa que el enunciado busca al pedir una implementación real y no solo un diseño.

| Síntoma | Causa | Corrección |
|---|---|---|
| `syntax error at or near ":"` | SQLAlchemy no reconoce `:param::tipo` como parámetro: su expresión regular de vinculación descarta un `:nombre` seguido de otro `:`, así que el `::` de PostgreSQL rompe el reconocimiento y el literal llega crudo al motor. | `CAST(:param AS tipo)` en lugar de `:param::tipo`. |
| `AmbiguousParameter: could not determine data type of parameter $5` | En `(:fare_class IS NULL OR …)`, con el parámetro en `NULL` PostgreSQL no tiene de dónde inferir el tipo. Mismo caso con `:iid` en la cancelación parcial. | `CAST(:param AS varchar)` / `CAST(:iid AS bigint)`. |
| El filtro opcional funcionaba en las pruebas manuales | Porque siempre se le pasaba un valor. El fallo solo aparece con el parámetro nulo, que es justamente el camino por defecto. | Añadido a la suite el caso sin `fare_class_code`. |

**Por qué importa declararlo:** las tres son fallas del camino *por defecto* —el de un usuario que no filtra por clase tarifaria y no cancela un itinerario específico—, no de casos límite exóticos. Un diseño revisado en papel las habría dejado pasar enteras.

### D-7 — Las pruebas necesitaban aislamiento explícito

| | |
|---|---|
| **Qué pasó** | La primera versión de la suite pasaba y fallaba de forma alterna. `test_multi_segment_hold_is_atomic` deja deliberadamente un vuelo lleno, y esa marca **sobrevivía a la corrida**: la siguiente ejecución encontraba sin cupo un vuelo que otra prueba necesitaba y fallaba por un motivo ajeno a lo que verificaba. |
| **Ajuste** | Función `reset_bookings()` que vacía las tablas transaccionales y pone el inventario en cero, invocada por una *fixture* de sesión y por `scarce_flight()`. |
| **Detalle que resultó revelador** | El `DELETE FROM reservation` **falló**, por dos motivos que son ambos correctos: `payment`, `ticket` y `reservation_event` referencian la reserva **sin `ON DELETE CASCADE`** (en producción una reserva no se borra, se cancela), y `reservation_event` tiene el trigger de inmutabilidad de **RNF-A2** que prohíbe `DELETE`. Hubo que usar `TRUNCATE … CASCADE`, que no dispara triggers de fila. |
| **Lectura** | **La prueba chocó contra las garantías del propio diseño y las garantías ganaron.** Que borrar el historial de auditoría sea difícil incluso desde una prueba es la señal de que RNF-A2 está realmente implementado y no solo declarado. |

### Lo que **no** hubo que ajustar

Vale la pena decirlo porque es la validación real del diseño: **el mecanismo de concurrencia funcionó tal como estaba documentado.** El bloqueo pesimista sobre la fila de `flight_inventory` con orden canónico se implementó sin sorpresas, sin abrazos mortales y sin reintentos. La decisión de DEC-3 de controlar el inventario **por conteo por cabina** en vez de por silla individual fue lo que hizo que la sección crítica cupiera en una sola consulta de bloqueo — si el inventario hubiera vivido en el mapa de sillas, esta parte habría sido considerablemente más compleja.

---

## 5.6.5 Trazabilidad diseño ↔ código

| Decisión | Dónde se ve en el código |
|---|---|
| **DEC-1** programado vs. instancia | `sql/01_schema.sql`: tablas `scheduled_flight` y `flight_instance`; materialización en `sql/02_seed.sql` |
| **DEC-2** itinerario como entidad | `reservation` → `itinerary` → `itinerary_segment`; `create_reservation` los crea anidados |
| **DEC-3** inventario por cabina | `flight_inventory` con PK `(flight_instance_id, cabin_code)`; `seat_assignment` aparte, sin tocar capacidad |
| **DEC-4** bloqueo pesimista | `booking._LOCK_INVENTORY_SQL` + `apply_lock_timeout` + `ck_inventory_no_oversell` |
| **DEC-5** precio congelado, pago delegado | `itinerary_segment.quoted_*` (D-1), `confirm_reservation`, `payment_event.psp_event_id` único |
| **DEC-8** caché/réplicas | `search.py` documenta que va a réplicas; el camino de reserva **nunca** lee de caché |
| **DEC-10** auditoría transaccional | `booking._audit` en la misma transacción; trigger `trg_reservation_event_immutable` |
| **DEC-13** error accionable | `errors.py` con códigos estables y `details` |
| **RNF-S2** control de acceso | `get_reservation`: apellido incorrecto devuelve **404**, no 403, para no confirmar que el PNR existe |
