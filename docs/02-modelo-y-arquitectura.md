# Sistema de Reservas — Aerolínea Regional
## Parte II — Modelo de datos y arquitectura (secciones 5.3, 5.4 y 5.5)

> Continúa la Parte I. Toda referencia `RF-xx` / `RNF-Xn` / `SUP-n` apunta a ese documento.

---

## 5.3 Modelo entidad-relación

### 5.3.1 Las cinco decisiones estructurales

El enunciado sugiere una lista de entidades (Vuelo, Aeronave, Ruta, Aeropuerto, Pasajero, Reserva, Tarifa, Silla, Pago) y advierte que **no hay que asumir que sea correcta ni completa**. No lo es: le faltan cuatro entidades imprescindibles y una de las que nombra —"Vuelo"— es ambigua y hay que partirla en dos. Estas son las cinco decisiones que definen el modelo.

---

#### DEC-1 — "Vuelo" son dos entidades: `scheduled_flight` e `flight_instance`

> *Pregunta guía: ¿cómo se representa que el mismo vuelo se repite todos los días?*

| | |
|---|---|
| **Decisión** | Separar el **vuelo programado** (`scheduled_flight`: el AV-8320 sale a las 07:15 de BOG a MDE, lunes a viernes, del 1-abr al 30-oct) de la **instancia de vuelo** (`flight_instance`: el AV-8320 del 14 de junio de 2026, con la aeronave HK-4812 asignada y estado `SCHEDULED`). |
| **Alternativa descartada** | Una sola tabla `flight` con una fila por fecha y las reglas de recurrencia repetidas en cada fila. |
| **Por qué** | (1) Sin la separación, cambiar el horario de un vuelo recurrente obliga a un `UPDATE` masivo sobre ~250 filas, con riesgo de tocar fechas ya vendidas. (2) La **capacidad no es del vuelo programado sino de la instancia**: si el 14 de junio se cambia el A320 (180 sillas) por un ATR-72 (68), esa instancia y solo esa cambia de capacidad. Con una sola tabla no hay dónde poner esa realidad. (3) El pipeline analítico de 5.7 necesita el programado como **dimensión** y la instancia como **hecho**; si están mezclados, el modelo estrella se vuelve un problema. |
| **Costo aceptado** | Requiere un proceso de **materialización** (RF-25) que genera instancias dentro del horizonte de venta de 360 días (SUP-3). Es un *job* diario, no una complicación estructural. |
| **Traza** | → RF-24, RF-25, RF-28, SUP-2, SUP-15 |

---

#### DEC-2 — El itinerario es una entidad propia entre la reserva y el vuelo

> *Pregunta guía: ¿una reserva tiene muchos vuelos asociados, o el itinerario es una entidad propia?*

| | |
|---|---|
| **Decisión** | Cadena de tres niveles: `reservation` (1) → `itinerary` (N) → `itinerary_segment` (N) → `flight_instance` (1). Una reserva agrupa itinerarios (ida, regreso); cada itinerario agrupa **tramos ordenados** (BOG→MDE→CTG son dos tramos de **un** itinerario). |
| **Alternativa descartada** | Relación directa `reservation N—N flight_instance` con una tabla puente plana. |
| **Por qué** | Con la tabla puente plana **se pierde la agrupación de viaje**. Tres preguntas que el modelo debe poder responder y que el puente plano no responde: ¿estos dos tramos son una conexión o dos vuelos independientes que el pasajero compró el mismo día? ¿Si se cancela el regreso, la ida sobrevive? ¿Cuál es el "origen y destino" del viaje, para reportar ocupación por ruta comercial? El itinerario es **la unidad de cancelación y de tarifación**; los tramos son la unidad de **inventario**. Son granos distintos y necesitan entidades distintas. |
| **Costo aceptado** | Un nivel más de anidamiento en las consultas y en la API. |
| **Traza** | → RF-08, RF-09, RF-16, RF-17, RNF-C2 |

---

#### DEC-3 — El inventario se controla por **conteo por cabina**, no por silla individual

> *Preguntas guía: ¿dónde vive el control de inventario? ¿la silla es parte de la reserva o del check-in?*

| | |
|---|---|
| **Decisión** | Existe `flight_inventory`, con **una fila por (instancia de vuelo × cabina)**, que guarda `capacity`, `seats_sold` y `seats_held`. Ahí y solo ahí vive el control de sobreventa. La **silla física** (`seat_assignment`) es una entidad **separada y opcional**, que no participa en el control de capacidad. |
| **Alternativa descartada** | Controlar el inventario marcando sillas individuales como ocupadas en un mapa de sillas por instancia. |
| **Por qué** | Tres razones, en orden de peso: **(1) Es el modelo real del negocio.** Se vende "un asiento en económica", no "el 14C". El pasajero que no elige silla igual consume inventario. **(2) Concurrencia.** El punto de contención pasa a ser **una sola fila** por cabina, que se puede bloquear de forma corta y determinista (DEC-4). Con sillas individuales, dos compradores del mismo vuelo compiten por filas distintas: nadie bloquea a nadie, pero **nadie controla el total** — habría que contar sillas libres en cada compra, y ese `COUNT` es exactamente la condición de carrera que hay que evitar. **(3) Cambio de aeronave.** Si el A320 se vuelve ATR-72 (SUP-2, RF-28), con conteos se compara `seats_sold` contra la nueva capacidad y se sabe al instante a cuántos hay que reacomodar. Con sillas asignadas hay que reasignar 180 filas y resolver colisiones. |
| **Consecuencia coherente** | Por eso la silla se asigna en el check-in (SUP-11): **desacoplar la silla de la venta es lo que permite que el control de capacidad sea barato.** La unicidad de la silla se protege aparte, con una restricción `UNIQUE (flight_instance_id, seat_number)` — que es un problema de integridad, no de capacidad. |
| **Traza** | → RF-10, RF-27, RNF-C1, SUP-11, RE-1 |

---

#### DEC-4 — Bloqueo pesimista sobre la fila de inventario

> *(La justificación completa, con el análisis de las tres alternativas, está en 5.4.2. Aquí queda el efecto sobre el modelo.)*

El modelo soporta esta decisión con tres elementos: la **granularidad** de `flight_inventory` (una fila por cabina, DEC-3), una columna `version` para permitir migrar a bloqueo optimista sin cambiar el esquema, y una **restricción `CHECK` a nivel de base de datos**:

```sql
CONSTRAINT ck_inventory_no_oversell
  CHECK (seats_sold + seats_held <= FLOOR(capacity * oversell_factor))
```

Esta restricción es deliberadamente **redundante** con la lógica de la aplicación. Es la última línea de defensa de RNF-C1: si un error de programación, una migración o una consulta manual intentaran sobrevender, la transacción falla en el motor. La regla de negocio más importante del sistema no debe vivir únicamente en el código de aplicación.

`oversell_factor` está en `1.00` por SUP-6 (sin sobreventa deliberada). Existe como columna, y no como constante, para que la restricción hipotética de la sección 3 —"permitir *overbooking* del 5%"— sea un `UPDATE` y no un rediseño (→ PE-0).

---

#### DEC-5 — El precio y las condiciones se **congelan** en el tiquete

| | |
|---|---|
| **Decisión** | `ticket` almacena `fare_amount`, `tax_amount`, `surcharge_amount`, `discount_amount`, `commission_amount`, `currency` y la referencia a la `fare_rule` **vigente en el momento de la emisión**, en vez de calcular el precio al consultar. |
| **Alternativa descartada** | Guardar solo `fare_id` y recomponer el precio con un `JOIN` a la tarifa vigente. |
| **Por qué** | Un tiquete es un **contrato**. Si mañana cambia la tarifa o la regla de penalidad, el tiquete vendido ayer no puede cambiar de precio ni de condiciones al consultarlo. Es una **denormalización deliberada de naturaleza temporal**, no un descuido de diseño: el dato no es "el precio de la tarifa", es "el precio que este pasajero pagó". Además hace que el análisis de *ingresos por tarifa* de 5.7 sea una lectura directa, sin reconstruir el estado histórico del catálogo. |
| **Traza** | → RF-04, RF-14, RF-21, SUP-12, RNF-A1 |

---

### 5.3.2 Nivel de normalización

> *Pregunta guía: ¿qué normalización se justifica, dado que el modelo alimenta el OLTP y el pipeline de Big Data?*

**Respuesta: 3FN estricta en el OLTP, con tres excepciones nombradas; el modelo analítico es otro y se construye en el ETL, no aquí.** El error que hay que evitar es diseñar una sola base "que sirva para todo": queda mal normalizada para transaccionar y mal desnormalizada para analizar.

| Excepción a 3FN | Qué se duplica | Por qué se acepta |
|---|---|---|
| `flight_inventory.seats_sold` / `seats_held` | Agregados derivables de `ticket` e `itinerary_segment`. | Calcularlos con `COUNT` en cada reserva sería el peor punto de contención posible: un agregado sobre miles de filas dentro de la transacción crítica. Materializarlos convierte el control de RNF-C1 en la lectura y escritura de **una fila**. La consistencia se garantiza porque **solo** el servicio de inventario los modifica, siempre dentro de la misma transacción que crea o cancela el tramo. |
| Montos en `ticket` (DEC-5) | Precio y condiciones del catálogo. | Denormalización temporal: es un valor histórico, no una copia. |
| `flight_instance.dep_utc` / `arr_utc` | Derivables de `scheduled_flight` + fecha + zona horaria del aeropuerto. | La aritmética de zonas horarias con horario de verano dentro de una consulta de búsqueda es cara y propensa a error. Se calcula una vez al materializar (RF-25) y se indexa. Habilita el índice que sostiene RNF-P1. |

**Por qué el OLTP no se desnormaliza para analítica:** las consultas de gerencia comercial (ocupación por ruta, ingresos por tarifa, patrones de cancelación) son escaneos agregados sobre millones de filas. Ejecutarlas contra el transaccional competiría por CPU y buffers con la ruta crítica de reserva y **violaría RNF-P4**. La separación de cargas es el motivo de existir de la sección 5.7: allí el modelo pasa a **esquema en estrella** (desnormalizado, orientado a consulta), y la traducción entre ambos es precisamente el trabajo del ETL.

---

### 5.3.3 Diagrama entidad-relación

Notación **Pata de Gallo (Crow's Foot)**, consistente en todo el diagrama.

Lectura de la cardinalidad: `||` = exactamente uno · `o|` = cero o uno · `}|` = uno o muchos · `}o` = cero o muchos.

```mermaid
erDiagram
    AIRPORT      ||--o{ ROUTE               : "origen de"
    AIRPORT      ||--o{ ROUTE               : "destino de"
    ROUTE        ||--o{ SCHEDULED_FLIGHT    : "es operada por"
    ROUTE        ||--o{ FARE                : "tiene tarifas en"

    AIRCRAFT_MODEL ||--o{ AIRCRAFT          : "clasifica"
    AIRCRAFT_MODEL ||--o{ SEAT_MAP          : "admite config."
    SEAT_MAP     ||--|{ SEAT_MAP_SEAT       : "se compone de"
    SEAT_MAP     ||--o{ AIRCRAFT            : "configura"
    CABIN        ||--o{ SEAT_MAP_SEAT       : "clasifica"

    SCHEDULED_FLIGHT ||--o{ FLIGHT_INSTANCE : "se materializa en"
    AIRCRAFT     |o--o{ FLIGHT_INSTANCE     : "opera"
    FLIGHT_INSTANCE ||--|{ FLIGHT_INVENTORY : "controla capacidad en"
    CABIN        ||--o{ FLIGHT_INVENTORY    : "particiona"

    CABIN        ||--o{ FARE_CLASS          : "agrupa"
    FARE_CLASS   ||--o{ FARE                : "se cotiza como"
    FARE_RULE    ||--o{ FARE                : "condiciona"

    RESERVATION  ||--|{ ITINERARY           : "agrupa"
    ITINERARY    ||--|{ ITINERARY_SEGMENT   : "ordena"
    FLIGHT_INSTANCE ||--o{ ITINERARY_SEGMENT : "es reservada en"

    RESERVATION  ||--|{ RESERVATION_PASSENGER : "incluye"
    PASSENGER    ||--o{ RESERVATION_PASSENGER : "viaja en"
    AGENCY       |o--o{ RESERVATION         : "origina"

    RESERVATION_PASSENGER ||--o{ TICKET     : "genera"
    ITINERARY_SEGMENT ||--o{ TICKET         : "se documenta en"
    FARE         ||--o{ TICKET              : "valoriza"
    TICKET       |o--o| SEAT_ASSIGNMENT     : "asigna silla"
    TICKET       ||--o{ ANCILLARY           : "adiciona"
    FLIGHT_INSTANCE ||--o{ SEAT_ASSIGNMENT  : "ubica en"

    RESERVATION  ||--o{ PAYMENT             : "se paga con"
    PAYMENT      ||--o{ PAYMENT_EVENT       : "recibe"
    PAYMENT      ||--o{ REFUND              : "origina"
    RESERVATION  ||--o{ RESERVATION_EVENT   : "audita"

    AIRPORT {
        char3    iata_code           PK
        varchar  name
        varchar  city
        char2    country_code
        varchar  timezone
        smallint min_connection_min
    }
    ROUTE {
        int     route_id             PK
        char3   origin_iata          FK
        char3   destination_iata     FK
        int     distance_km
        boolean is_active
    }
    AIRCRAFT_MODEL {
        varchar model_code           PK
        varchar manufacturer
        varchar name
    }
    SEAT_MAP {
        int     seat_map_id          PK
        varchar model_code           FK
        varchar name
    }
    SEAT_MAP_SEAT {
        int     seat_map_id          PK_FK
        varchar seat_number          PK
        varchar cabin_code           FK
        boolean is_window
        boolean is_exit_row
    }
    AIRCRAFT {
        int     aircraft_id          PK
        varchar registration         UK
        varchar model_code           FK
        int     seat_map_id          FK
        varchar status
    }
    CABIN {
        varchar cabin_code           PK
        varchar name
        smallint boarding_priority
    }
    SCHEDULED_FLIGHT {
        int     scheduled_flight_id  PK
        varchar flight_number
        int     route_id             FK
        time    departure_time_local
        time    arrival_time_local
        smallint arrival_day_offset
        varchar days_of_week
        date    valid_from
        date    valid_to
        int     seat_map_id          FK
    }
    FLIGHT_INSTANCE {
        bigint  flight_instance_id   PK
        int     scheduled_flight_id  FK
        date    flight_date
        timestamptz departure_utc
        timestamptz arrival_utc
        int     aircraft_id          FK
        varchar status
    }
    FLIGHT_INVENTORY {
        bigint  flight_instance_id   PK_FK
        varchar cabin_code           PK_FK
        smallint capacity
        smallint seats_sold
        smallint seats_held
        numeric oversell_factor
        int     version
    }
    FARE_CLASS {
        char1   fare_class_code      PK
        varchar cabin_code           FK
        varchar name
    }
    FARE_RULE {
        int     fare_rule_id         PK
        smallint min_advance_days
        boolean is_refundable
        numeric refund_penalty
        boolean is_changeable
        numeric change_penalty
        smallint baggage_pieces
    }
    FARE {
        int     fare_id              PK
        int     route_id             FK
        char1   fare_class_code      FK
        int     fare_rule_id         FK
        char3   currency
        numeric base_amount
        date    valid_from
        date    valid_to
    }
    PASSENGER {
        bigint  passenger_id         PK
        varchar first_name
        varchar last_name
        date    birth_date
        varchar document_type
        varchar document_number
        varchar email
        varchar loyalty_id
    }
    AGENCY {
        int     agency_id            PK
        varchar legal_name
        varchar tax_id               UK
        numeric net_fare_discount_pct
        numeric commission_pct
        numeric credit_limit
        numeric credit_used
    }
    RESERVATION {
        bigint  reservation_id       PK
        char6   pnr                  UK
        varchar channel
        int     agency_id            FK
        varchar status
        char3   currency
        numeric total_amount
        timestamptz hold_expires_at
        timestamptz created_at
    }
    RESERVATION_PASSENGER {
        bigint  reservation_id       PK_FK
        smallint passenger_ref       PK
        bigint  passenger_id         FK
        varchar passenger_type
    }
    ITINERARY {
        bigint  itinerary_id         PK
        bigint  reservation_id       FK
        smallint sequence_no
        char3   origin_iata
        char3   destination_iata
        varchar status
    }
    ITINERARY_SEGMENT {
        bigint  segment_id           PK
        bigint  itinerary_id         FK
        smallint sequence_no
        bigint  flight_instance_id   FK
        varchar cabin_code           FK
        char1   fare_class_code      FK
        varchar status
    }
    TICKET {
        bigint  ticket_id            PK
        varchar ticket_number        UK
        bigint  reservation_id       FK
        smallint passenger_ref       FK
        bigint  segment_id           FK
        int     fare_id              FK
        numeric fare_amount
        numeric tax_amount
        numeric discount_amount
        numeric commission_amount
        varchar status
    }
    SEAT_ASSIGNMENT {
        bigint  ticket_id            PK_FK
        bigint  flight_instance_id   FK
        varchar seat_number
        timestamptz assigned_at
    }
    ANCILLARY {
        bigint  ancillary_id         PK
        bigint  ticket_id            FK
        varchar ancillary_type
        numeric amount
    }
    PAYMENT {
        bigint  payment_id           PK
        bigint  reservation_id       FK
        varchar method
        varchar psp_intent_id        UK
        numeric amount
        varchar status
        varchar idempotency_key      UK
    }
    PAYMENT_EVENT {
        bigint  payment_event_id     PK
        bigint  payment_id           FK
        varchar psp_event_id         UK
        varchar event_type
        jsonb   payload
    }
    REFUND {
        bigint  refund_id            PK
        bigint  payment_id           FK
        numeric amount
        varchar status
    }
    RESERVATION_EVENT {
        bigint  event_id             PK
        bigint  reservation_id       FK
        timestamptz occurred_at
        varchar actor_type
        varchar event_type
        varchar from_status
        varchar to_status
        jsonb   payload
    }
```

> **Nota sobre `AIRPORT ||--o{ ROUTE`:** aparece dos veces porque `route` tiene dos claves foráneas hacia `airport` (origen y destino). Es una relación **recursiva a través de una entidad asociativa**, no un error del diagrama.

---

### 5.3.4 Diccionario de datos

Se documentan las entidades relevantes para las decisiones del parcial. El DDL completo, con tipos, restricciones e índices, está en [`backend/sql/01_schema.sql`](../backend/sql/01_schema.sql).

#### Catálogo de red y flota

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| **`airport`** | Aeropuertos de la red. | `iata_code` (PK), `timezone`, `min_connection_min` | `min_connection_min` es el tiempo mínimo de conexión (MCT) y lo usa la validación de RF-09. Dato por aeropuerto, no global: conectar en un aeropuerto grande toma más que en uno pequeño. |
| **`route`** | Par origen-destino comercializable. | `route_id` (PK), `origin_iata`, `destination_iata` | `UNIQUE (origin, destination)`. Dirigida: BOG→MDE y MDE→BOG son dos rutas. |
| **`aircraft_model`** | Modelo de aeronave. | `model_code` (PK) | Separado de `aircraft` porque hay 12 aeronaves y 3 modelos (SUP-2). |
| **`seat_map`** / **`seat_map_seat`** | Configuración de cabina de un modelo. | `seat_map_id`, `seat_number`, `cabin_code` | La **capacidad por cabina se deriva contando** aquí; se copia a `flight_inventory` al materializar. Un mismo modelo puede tener varias configuraciones. |
| **`aircraft`** | Aeronave física. | `aircraft_id` (PK), `registration` (UK), `seat_map_id` | La matrícula es la identidad real ante la autoridad aeronáutica. |
| **`cabin`** | Catálogo: `ECO`, `BUS`. | `cabin_code` (PK) | Entidad, no `ENUM`: agregar `PREMIUM_ECO` debe ser un `INSERT`, no una migración de tipo. |

#### Programación y operación

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| **`scheduled_flight`** | Plantilla recurrente (DEC-1). | `flight_number`, `route_id`, `departure_time_local`, `days_of_week`, `valid_from`/`valid_to` | `days_of_week` es una cadena de 7 posiciones (`1234500` = lun-vie). `arrival_day_offset` ∈ {0,1} para vuelos que cruzan medianoche. |
| **`flight_instance`** | Vuelo en una fecha concreta (DEC-1). | `flight_instance_id`, `flight_date`, `departure_utc`, `aircraft_id`, `status` | `UNIQUE (scheduled_flight_id, flight_date)` impide materializar dos veces. `departure_utc` está precalculado (ver 5.3.2). `status` ∈ `SCHEDULED / DELAYED / DEPARTED / ARRIVED / CANCELLED`. `aircraft_id` es nulo hasta que operaciones lo asigna. |
| **`flight_inventory`** | **Control de sobreventa (DEC-3, DEC-4).** | PK `(flight_instance_id, cabin_code)`, `capacity`, `seats_sold`, `seats_held`, `oversell_factor`, `version` | `CHECK (seats_sold + seats_held <= FLOOR(capacity * oversell_factor))` y `CHECK (seats_sold >= 0 AND seats_held >= 0)`. **Es la fila que se bloquea con `SELECT … FOR UPDATE`.** La tabla más importante del sistema. |

#### Tarifas

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| **`fare_class`** | Clase tarifaria (`Y`,`B`,`M` en económica; `J`,`C` en ejecutiva). | `fare_class_code` (PK), `cabin_code` | Es la que conecta "tipo de silla" con "precio". |
| **`fare_rule`** | Condiciones comerciales. | `min_advance_days`, `is_refundable`, `refund_penalty`, `is_changeable`, `change_penalty`, `baggage_pieces` | `min_advance_days` implementa la **tarifa por anticipación** del enunciado. Alimenta el cálculo de reembolso de RF-16 y la penalidad de RF-17. |
| **`fare`** | Precio de una clase en una ruta, vigente en un rango de fechas. | `route_id`, `fare_class_code`, `fare_rule_id`, `currency`, `base_amount`, `valid_from`/`valid_to` | Las vigencias no se solapan para el mismo `(route, fare_class, currency)`. |

#### Reserva

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| **`reservation`** | El PNR: unidad de compra y de pago (DEC-2). | `pnr` (UK, 6 caracteres), `channel`, `agency_id`, `status`, `hold_expires_at`, `total_amount` | `status` ∈ `HELD / CONFIRMED / CANCELLED / EXPIRED`. `hold_expires_at` no nulo solo en `HELD`. PNR generado con alfabeto sin caracteres ambiguos (sin `I`,`O`,`0`,`1`) y aleatorio, no secuencial (RF-13, RNF-S2). |
| **`itinerary`** | Un viaje dentro del PNR (ida / regreso). | `reservation_id`, `sequence_no`, `origin_iata`, `destination_iata` | Es la **unidad de cancelación y de tarifación** (DEC-2). Origen y destino son los del viaje completo, no los del primer tramo. |
| **`itinerary_segment`** | Un tramo volado (DEC-2). | `itinerary_id`, `sequence_no`, `flight_instance_id`, `cabin_code`, `fare_class_code`, `status` | `UNIQUE (itinerary_id, sequence_no)`. Es la **unidad de inventario**: cada tramo consume una silla en su `flight_inventory`. |
| **`reservation_passenger`** | Pasajeros del PNR. | PK `(reservation_id, passenger_ref)` | `passenger_ref` (1,2,3…) es el identificador **local** del pasajero dentro del PNR. Permite emitir tiquetes sin exponer el `passenger_id` global. `passenger_type` ∈ `ADT / CHD / INF` (el infante no consume inventario). |
| **`passenger`** | Datos personales. | `document_type` + `document_number`, `email`, `loyalty_id` | Tabla con **PII**: sujeta a RNF-S3 (cifrado y anonimización a 5 años). `loyalty_id` reservado y nulo (SUP-9). |
| **`agency`** | Agencia aliada (SUP-13). | `net_fare_discount_pct`, `commission_pct`, `credit_limit`, `credit_used` | `CHECK (credit_used <= credit_limit)` respalda RF-22. |

#### Emisión y pago

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| **`ticket`** | Documento de valor: **un tiquete por pasajero × tramo** (RF-14). | `ticket_number` (UK), `segment_id`, `passenger_ref`, `fare_id`, `fare_amount`, `tax_amount`, `discount_amount`, `commission_amount`, `status` | `UNIQUE (segment_id, reservation_id, passenger_ref)`. Montos **congelados** (DEC-5). Es el **grano del hecho analítico** de 5.7. |
| **`seat_assignment`** | Silla física (SUP-11, DEC-3). | PK `ticket_id`, `flight_instance_id`, `seat_number` | `UNIQUE (flight_instance_id, seat_number)`: impide dos pasajeros en la misma silla. **No participa en el control de capacidad.** |
| **`ancillary`** | Producto auxiliar: equipaje, selección de silla (SUP-10). | `ticket_id`, `ancillary_type`, `amount` | Su ingreso entra en el análisis de 5.7. |
| **`payment`** | Intento de cobro. | `psp_intent_id` (UK), `idempotency_key` (UK), `method`, `status` | `method` ∈ `CARD / AGENCY_CREDIT`. **Sin datos de tarjeta** (SUP-14, RNF-S1): solo la referencia del PSP. |
| **`payment_event`** | Webhooks recibidos del PSP. | `psp_event_id` (UK), `payload` (jsonb) | La unicidad de `psp_event_id` es lo que hace **idempotente** la confirmación (RNF-C4): un evento reentregado choca contra el índice y se descarta. |
| **`reservation_event`** | **Log de auditoría, solo inserción** (RNF-A1). | `occurred_at`, `actor_type`, `actor_id`, `channel`, `event_type`, `from_status`, `to_status`, `payload` | Sin `UPDATE` ni `DELETE`, revocado a nivel de permisos (RNF-A2). Es **fuente del pipeline analítico**: los patrones de cancelación se calculan aquí (ver 5.7). |

---

## 5.4 Arquitectura del backend

### DEC-6 — Monolito modular con lectura separada, no microservicios

> *Pregunta guía: ¿monolito o servicios separados? ¿Qué RNF respalda la elección, en particular el de concurrencia?*

**Decisión: un solo despliegue con cuatro módulos de frontera explícita** — `search`, `booking` (que contiene inventario), `payments` y `catalog` — sobre una base de datos PostgreSQL única, más **réplicas de lectura** para la búsqueda.

**El RNF que decide es RNF-C1.** El razonamiento, que es el que hay que sostener en la sustentación:

> Retener un itinerario multi-tramo exige verificar y actualizar **N filas de `flight_inventory`** (una por tramo) de forma **atómica** (RNF-C2). Con una base de datos única, eso es **una transacción ACID** y el problema está resuelto por el motor. Si `search`, `booking` e `inventory` fueran servicios con bases separadas, la misma garantía exigiría una **saga con compensaciones**: retener el tramo 1, retener el tramo 2, fallar en el 3, y compensar los dos primeros. Durante la ventana de compensación el sistema está **temporalmente inconsistente** — es decir, temporalmente sobrevendido. Se puede construir, pero se estaría **pagando complejidad distribuida para debilitar la garantía que el enunciado señala como innegociable.**

Complementos de la decisión:

| Aspecto | Decisión | Justificación |
|---|---|---|
| **Separación de lectura** | `search` consulta **réplicas de lectura**; `booking` va siempre al primario. | Es el 98,7% del tráfico (relación 80:1, SUP-4) y tolera unos segundos de desfase: mostrar disponibilidad ligeramente vieja es aceptable porque **la verdad se verifica al retener**, no al buscar. → RNF-P1, RNF-P3 |
| **Sin estado en la aplicación** | La sesión vive en el cliente (JWT) y la caché en Redis, no en memoria del proceso. | Habilita el escalado horizontal de RNF-E3. |
| **Fronteras internas reales** | Cada módulo expone una interfaz de aplicación; nada de consultas cruzadas a las tablas de otro módulo. | Permite extraer `search` o `payments` como servicio propio **cuando** haya evidencia de que hace falta, sin reescribir el dominio. |
| **Cuándo dividir** | Umbral declarado: si la búsqueda exige más de 8 instancias en pico sostenido, o si el equipo supera ~15 personas. | Un criterio explícito evita la discusión ideológica monolito-vs-microservicios. |

**Lo que se paga:** escalado menos granular (escala toda la aplicación, aunque solo la búsqueda lo necesite) y un despliegue acoplado. A la escala de SUP-1/SUP-4, ambos costos son menores que el de operar una saga distribuida sobre la ruta crítica.

---

### DEC-4 (completa) — Control de sobreventa: bloqueo pesimista

> *Pregunta guía: ¿bloqueo pesimista, optimista con reintentos, o cola?*

Las tres alternativas se evaluaron contra el escenario real de contención: **40 solicitudes concurrentes sobre la última silla** (SUP-5).

| Mecanismo | Cómo funciona | Bajo alta contención | Veredicto |
|---|---|---|---|
| **Optimista con reintentos** | Lee `version`, calcula, escribe con `WHERE version = leída`. Si falla, reintenta. | Con 40 solicitudes por 1 silla, **39 fallan y reintentan**; en el reintento vuelven a chocar. Tormenta de reintentos justo en el momento de máximo valor comercial. Latencia p99 impredecible. | ✗ Óptimo cuando los conflictos son raros. Aquí el conflicto **es** el caso de uso. |
| **Cola serializadora por vuelo** | Una cola por `flight_instance`; un consumidor procesa en orden. | Serialización perfecta y sin bloqueos. Pero la reserva pasa a ser **asíncrona**: el usuario recibe "procesando" y hay que sondear o notificar. Añade broker, consumidores, orden y reintentos. | ✗ Sobre-ingeniería a esta escala. Es la respuesta correcta a escala de 10.000 solicitudes/s por vuelo (venta de entradas de concierto), no a 40. |
| **Pesimista con `SELECT … FOR UPDATE`** ✅ | La transacción bloquea la fila de `flight_inventory` de la cabina; las demás **esperan en el motor** y se atienden en orden. | Las 40 se serializan sobre **una fila**, con transacción de ~5 ms. La primera gana, las 39 siguientes leen el estado ya actualizado y reciben un `409` limpio. **Determinista, síncrono y sin reintentos.** | ✅ **Elegido.** |

**Por qué es el correcto aquí, en una frase defendible:** la contención está concentrada en **una sola fila corta** (DEC-3), la transacción es **breve y no incluye E/S externa** (el pago está fuera, DEC-5), y el RNF prefiere **rechazar rápido a confirmar y revertir** (RNF-C1). Bajo esas tres condiciones, el bloqueo pesimista da la garantía más fuerte al menor costo de complejidad.

**Protocolo de la transacción crítica** (implementado en 5.6):

```
BEGIN;
  -- 1. Bloquear TODOS los tramos en orden canónico de flight_instance_id.
  --    El orden fijo es lo que previene el deadlock entre itinerarios
  --    que comparten tramos en distinta secuencia (A→B→C vs C→B→A).
  SELECT ... FROM flight_inventory
   WHERE (flight_instance_id, cabin_code) IN (...)
   ORDER BY flight_instance_id, cabin_code
     FOR UPDATE;

  -- 2. Verificar disponibilidad de TODOS los tramos (atomicidad, RNF-C2)
  -- 3. UPDATE flight_inventory SET seats_held = seats_held + n, version = version + 1
  --    -> la CHECK constraint es la última línea de defensa
  -- 4. INSERT reservation (HELD, hold_expires_at = now() + 20 min)
  -- 5. INSERT itinerary, itinerary_segment, reservation_passenger
  -- 6. INSERT reservation_event (auditoría, RNF-A1)
COMMIT;
```

Tres detalles que hacen la diferencia y que conviene sustentar:

1. **Orden canónico de bloqueo.** Sin `ORDER BY flight_instance_id`, dos itinerarios que comparten tramos en sentido inverso producen un abrazo mortal. Con orden fijo, es imposible por construcción.
2. **Ninguna llamada externa dentro de la transacción.** Si el PSP se demorara 3 s con la fila bloqueada, se bloquearía el vuelo entero durante 3 s. Por eso el pago está fuera (DEC-5).
3. **`lock_timeout` explícito (3 s).** Antes que dejar que una espera se acumule sin límite, se falla rápido con un mensaje claro. Un fallo limpio es mejor que una degradación silenciosa.

---

### DEC-5 (completa) — Flujo de pago: delegado, con máquina de estados propia

> *Pregunta guía: ¿se delega al proveedor o el sistema maneja parte de la lógica?*

**Decisión: se delega el *manejo del instrumento de pago*, pero el sistema conserva la *máquina de estados de la venta*.** No es lo mismo, y confundirlo es un error común.

```
  Retención (síncrona, transaccional)          Pago (asíncrono, externo)
┌───────────────────────────────────┐   ┌────────────────────────────────────┐
│ POST /reservations                │   │ El usuario paga en el checkout     │
│  → BEGIN … FOR UPDATE … COMMIT    │   │  alojado por el PSP                │
│  → reservation = HELD (20 min)    │──▶│  (el sistema NUNCA ve el PAN)      │
│  → devuelve PNR + payment_intent  │   │                                    │
└───────────────────────────────────┘   └──────────────┬─────────────────────┘
                                                       │ webhook firmado
                                        ┌──────────────▼─────────────────────┐
                                        │ POST /webhooks/psp                 │
                                        │  → verifica firma HMAC             │
                                        │  → INSERT payment_event            │
                                        │    (UNIQUE psp_event_id ⇒ idempot.)│
                                        │  → HELD → CONFIRMED                │
                                        │  → seats_held−n, seats_sold+n      │
                                        │  → emite tickets + audita          │
                                        └────────────────────────────────────┘
```

| Qué delega | Qué conserva |
|---|---|
| Captura y almacenamiento del PAN, 3-D Secure, antifraude del instrumento, tokenización, cumplimiento PCI del dato. | Estado de la reserva, retención de inventario, reglas de reembolso, **idempotencia**, conciliación diaria y decisión final de confirmar. |

**Por qué así:** delegar el instrumento reduce el alcance PCI a SAQ-A (RNF-S1, SUP-14) — la decisión de seguridad de mayor impacto del proyecto. Pero **no se puede delegar la máquina de estados**: el PSP no sabe nada de inventario ni de retenciones. Si el webhook llega después de que expiró la retención, el sistema debe reintentar la retención y, si el vuelo ya se llenó, **reembolsar automáticamente**. Esa lógica de reconciliación es propia y es la parte difícil.

**Casos límite resueltos explícitamente:**

| Caso | Comportamiento |
|---|---|
| Webhook duplicado | `UNIQUE (psp_event_id)` lo descarta. → RNF-C4 |
| Webhook tras expirar la retención | Se reintenta retener. Si hay cupo → confirma. Si no → reembolso automático y notificación. |
| Pago exitoso, webhook nunca llega | *Job* de conciliación cada 15 min contra la API del PSP sobre reservas `HELD` vencidas con intento de pago. |
| Usuario paga dos veces | Segundo intento rechazado por `idempotency_key` de la reserva. |
| Agencia (SUP-13) | No hay PSP: se debita `credit_used` **dentro de la misma transacción** que la retención. La reserva nace `CONFIRMED` directamente. Es la única diferencia real de flujo entre canales (RF-22). |

---

### DEC-7 — API B2B separada, dominio compartido

> *Pregunta guía: ¿la misma API del sitio público o una API B2B separada?*

**Decisión: dos superficies de API (`/api/v1/**` pública y `/b2b/v1/**` para agencias) sobre los mismos servicios de dominio.**

| | API pública | API B2B |
|---|---|---|
| Autenticación | Sesión de usuario / invitado | OAuth2 *client credentials* por agencia + usuario nominal |
| Tarifas | Públicas | **Netas** con descuento contractual (RF-21) |
| Pago | PSP con tarjeta | **Cupo de crédito** (RF-22) |
| Límite de tasa | Por IP | **Cuota por agencia** (RNF-E2) |
| Versionado | Cambia con el sitio web | **Contrato estable**: una agencia no puede desplegar cuando le convenga a la aerolínea |
| Datos visibles | Solo la reserva del propio usuario | Solo las reservas de **esa** agencia (RNF-S5) |

**Por qué separadas:** son contratos con ciclos de vida distintos. La web pública se puede cambiar el martes; un integrador B2B necesita meses de aviso. Mezclarlas obligaría a congelar la evolución de la web al ritmo del socio más lento.

**Por qué el dominio es compartido:** si el canal B2B tuviera su propia lógica de inventario, **la garantía de no-sobreventa de RNF-C1 habría que probarla dos veces y se rompería la primera vez que los canales divergieran**. El módulo `booking` es uno solo, y ambas superficies lo llaman. Esto es lo que responde de verdad la pregunta "¿tienen un flujo diferente?": **flujo de dominio idéntico, contrato y condiciones comerciales distintos.**

---

### DEC-8 — Estrategia de rendimiento y escalado

| Capa | Mecanismo | RNF |
|---|---|---|
| Catálogo (aeropuertos, rutas) | Caché en Redis, TTL 24 h; invalidación por evento al editar. | RNF-P1 |
| Resultados de búsqueda | Caché por `(origen, destino, fecha, cabina, #pax)`, **TTL 60 s**. Acierto esperado > 70% en rutas de alta demanda. | RNF-P1, RNF-P3 |
| Búsqueda | Réplicas de lectura, autoescalado por CPU y latencia. | RNF-P3, RNF-E3 |
| Reserva | Solo primario, sin caché. **Nunca se cachea disponibilidad para decidir una retención.** | RNF-C1 |
| Índices críticos | `(route_id, flight_date, status)` sobre `flight_instance`; `(departure_utc)`; `flight_inventory` por PK. | RNF-P1, RNF-E1 |
| Temporada alta | Capacidad preaprovisionada por calendario (no reactiva) + **congelamiento de despliegues**. | RNF-D1, RNF-P3 |

> **Por qué TTL de 60 s y no más:** el desfase de la caché solo puede producir un **falso positivo de disponibilidad** (mostrar un vuelo que acaba de llenarse). Es aceptable porque la verdad se verifica al retener y el usuario recibe un error claro. Lo inverso — cachear la decisión de retención — sí violaría RNF-C1 y por eso está prohibido explícitamente.

---

### DEC-9 — Disponibilidad y resiliencia

| Elemento | Decisión | RNF |
|---|---|---|
| Base de datos | RDS PostgreSQL **Multi-AZ**, conmutación automática (típicamente 60-120 s). | RNF-D1 |
| Aplicación | ≥ 2 instancias en zonas distintas tras balanceador, con verificación de salud. | RNF-D1, RNF-E3 |
| Respaldos | Automáticos + recuperación a un punto en el tiempo (PITR). | RNF-D3 (RPO 5 min) |
| Degradación | Si el PSP cae: la búsqueda y la retención siguen operando; solo se bloquea la confirmación. Se avisa al usuario y se extiende la retención. | RNF-D1 |
| Despliegue | Azul-verde, con migraciones compatibles hacia atrás. | RNF-D1 |
| Cortacircuitos | Ante fallo del PSP, se abre el circuito y se deja de intentar; reintento con retroceso exponencial. | RNF-D1 |

---

### DEC-10 — Auditoría por eventos

Toda transición de estado escribe en `reservation_event` **dentro de la misma transacción** que la produce. Esto es deliberado: si la auditoría fuera asíncrona, un fallo entre la operación y su registro dejaría una transición sin rastro, y RNF-A1 exige el **100%**.

Los permisos de la base revocan `UPDATE` y `DELETE` sobre esa tabla incluso para el rol de aplicación (RNF-A2). Solo se inserta.

**Y este log es una fuente analítica, no solo un artefacto de cumplimiento**: es lo que hace calculables los *patrones de cancelación* que pide la gerencia comercial. Una reserva cancelada que solo guarda su estado final no dice cuándo se canceló ni cuánto vivió. Enlace directo con 5.7.

---

### DEC-11 — Relación con el pipeline de Big Data

> *Pregunta guía: ¿cómo se relaciona esta arquitectura transaccional con el pipeline de Big Data del curso?*

**Regla dura: el pipeline analítico nunca consulta el primario transaccional.** Esa es la traducción arquitectónica de RNF-P4.

```
  OLTP (PostgreSQL, RDS)                        OLAP (PostgreSQL, RDS)
 ┌────────────────────────┐    ETL Glue        ┌────────────────────────┐
 │ reservation            │   (ventana         │ dim_date               │
 │ itinerary_segment      │    nocturna,       │ dim_route              │
 │ ticket                 │──▶ desde la ──────▶│ dim_fare_class         │
 │ flight_instance        │    réplica         │ dim_flight             │
 │ flight_inventory       │    de lectura)     │ fact_ticket_sale       │
 │ reservation_event      │                    │ fact_flight_occupancy  │
 └────────────────────────┘                    └────────────────────────┘
            │                                              │
            └──────────▶ AWS Glue Data Catalog ◀───────────┘
                        (metadatos de ambas)
```

| Decisión | Justificación |
|---|---|
| El ETL **lee de la réplica**, no del primario. | Garantiza RNF-P4: el escaneo analítico no compite con la ruta crítica de reserva. |
| Ventana **nocturna** (03:00), no continua. | Las preguntas de la gerencia (ocupación, ingresos, cancelaciones) son tácticas, no de tiempo real. Frescura de 24 h es suficiente y cuesta una fracción. Se detalla en 5.7. |
| Extracción **incremental** por `updated_at`. | Un escaneo completo diario de `ticket` crecería sin límite y encarecería el ETL de forma lineal con la historia. |
| El **grano del hecho es el tiquete** (pasajero × tramo). | Es el grano más fino con valor monetario. Desde ahí se agrega a ruta, tarifa, vuelo o fecha; al revés no se puede. |
| `reservation_event` **es una fuente**, no solo auditoría. | Sin él no hay patrones de cancelación (DEC-10). |

---

## 5.5 Arquitectura del frontend

### DEC-12 — Híbrido: SSR para descubrimiento, SPA para el flujo de reserva

> *Pregunta guía: ¿SPA o renderizado en servidor? ¿Qué RNF de rendimiento pesa?*

**Decisión: Next.js (React) con renderizado en servidor para las páginas de entrada y aplicación cliente para el flujo de compra.**

| Superficie | Modo | Por qué |
|---|---|---|
| Inicio, páginas de ruta ("vuelos Bogotá-Medellín"), contenido | **SSR / estático** | SEO — estas páginas capturan tráfico de búsqueda orgánica, que es adquisición gratuita. Una SPA pura las hace invisibles. Primer pintado rápido en redes móviles lentas. |
| Resultados de búsqueda | **SSR inicial + hidratación** | El primer resultado debe verse rápido (RE-2). Filtros y orden ya son cliente, sin ida y vuelta al servidor (RF-03). |
| Selección → pasajeros → sillas → pago | **SPA** | Flujo con mucho estado y un reloj de retención corriendo. Recargar la página en cada paso rompería la experiencia y añadiría latencia justo donde el usuario está a punto de pagar. |
| Gestión de reserva y check-in | **SPA** | Aplicación autenticada, sin valor SEO. |

**El RNF que pesa es RNF-P1/P2 más RE-2:** el tiempo que importa comercialmente no es el de la API, es el **tiempo hasta que el usuario ve el primer resultado**. Con SPA pura hay tres saltos en serie (HTML → JS → API). Con SSR en la primera carga, el usuario ve resultados en el primer viaje de red. Después de eso, la SPA es más rápida para todo lo demás. Elegir uno solo de los dos modos sería optimizar la mitad del recorrido.

---

### DEC-13 — Notificación en tiempo real de pérdida de disponibilidad

> *Pregunta guía: ¿cómo se notifica al usuario, en tiempo real, si la silla que seleccionaba ya no está disponible?*

**Decisión: tres capas, de menor a mayor costo.** No hace falta websockets para todo.

| Capa | Mecanismo | Qué cubre |
|---|---|---|
| **1. Reloj de retención** | Una vez creada la reserva `HELD`, la SPA muestra una cuenta regresiva de 20 minutos alimentada por `hold_expires_at` (autoridad del servidor, no del reloj del navegador). | El caso mayoritario: el usuario **ya tiene** el inventario retenido, así que **no puede perderlo** por culpa de otro. La mejor forma de no notificar una mala noticia es hacer que no ocurra. |
| **2. Verificación antes de retener** | Al pulsar "continuar" desde los resultados, se revalida disponibilidad contra el servidor antes de mostrar el formulario de pasajeros. | Cubre el desfase de la caché de 60 s (DEC-8). Falla temprano, cuando el usuario aún no ha invertido esfuerzo. |
| **3. SSE en el mapa de sillas** | *Server-Sent Events* sobre `flight_instance`, solo mientras el mapa de sillas está abierto. Marca en gris las sillas que se ocupan en vivo. | La única pantalla donde de verdad hay competencia visible por un recurso concreto. SSE y no WebSocket: el flujo es unidireccional servidor→cliente y SSE reconecta solo. |

**Manejo del error cuando ocurre igual:** si la retención falla con `409 SEAT_UNAVAILABLE`, la interfaz **no muestra un error genérico**: recarga la disponibilidad de ese tramo y propone las alternativas más cercanas (siguiente vuelo, otra cabina, otra fecha) conservando el resto del itinerario ya elegido. Un rechazo bien manejado recupera la venta; un `Error 409` en rojo la pierde. → RNF-C1, RE-2

---

### DEC-14 — Consola B2B separada

> *Pregunta guía: ¿se necesita una interfaz separada para los agentes de agencias?*

**Sí, aplicación separada** (`agents.aerolinea.com`), consumiendo la API B2B de DEC-7.

**Por qué no reutilizar la web pública con un rol distinto:** el agente no es un pasajero con permisos extra, es **otro oficio**. Vende 40 tiquetes al día para clientes que no son él: necesita búsqueda multi-pasajero, comparación lado a lado, cupo de crédito visible, liquidación de comisiones (RF-23), reservas en nombre de terceros y atajos de teclado. Meter todo eso en la web pública tras condicionales de rol produce una interfaz peor para ambos y una superficie de riesgo mayor (un error de rol expondría datos de agencia al público — RNF-S5).

Se comparte: sistema de diseño, cliente de API generado del mismo OpenAPI y componentes de calendario y mapa de sillas. Se separa: navegación, permisos, despliegue y ritmo de cambio.

---

### DEC-15 — Internacionalización y multi-moneda

> *Pregunta guía: ¿qué implicación tiene manejar múltiples monedas o idiomas sobre la estructura del frontend?*

Implicaciones concretas, más allá de "traducir textos":

| Dimensión | Decisión | Implicación estructural |
|---|---|---|
| **Idioma** | Rutas con prefijo (`/es/…`, `/en/…`), no detección por navegador. | Cada idioma es una URL indexable — coherente con el SSR de DEC-12. La detección automática rompe el SEO y confunde a quien comparte un enlace. |
| **Moneda** | **La moneda se fija al inicio de la reserva y no cambia.** El precio se guarda en la moneda de venta más la tasa aplicada (SUP-12). | Impide el "precio que se mueve": el frontend **nunca** convierte para mostrar. Todos los montos llegan del servidor ya en la moneda de la sesión. |
| **Formato** | `Intl.NumberFormat` / `Intl.DateTimeFormat` con la configuración regional activa. | El separador decimal cambia (COP `1.250.000` vs USD `1,250.00`); un formateo manual produciría errores de tres órdenes de magnitud. |
| **Zonas horarias** | Los horarios de vuelo se muestran **siempre en hora local del aeropuerto**, con la etiqueta explícita. | Es una convención del sector, no un capricho: mostrar la salida en la hora local del usuario hace perder vuelos. Requiere `airport.timezone` en el modelo — por eso está en el diccionario de datos. |
| **Estructura** | Textos externos en catálogos por idioma, cargados por ruta (no un paquete único). | Evita enviar el catálogo inglés a usuarios hispanohablantes; sostiene el presupuesto de rendimiento de RNF-P1. |
| **Contenido de negocio** | Los nombres de aeropuertos y ciudades **se traducen en la base de datos**, no en el frontend. | Un catálogo de datos no puede vivir en archivos de traducción del cliente: cambiaría con cada despliegue en vez de con cada edición del catálogo. |

---

## Anexo B (actualizado) — Matriz de trazabilidad

| Decisión | Qué resuelve | Requisitos que la originan | Riesgo mitigado |
|---|---|---|---|
| **DEC-1** Programado vs instancia | Vuelos recurrentes, cambio de aeronave | RF-24, RF-25, RF-28 | RE-6 |
| **DEC-2** Itinerario como entidad | Escalas y multi-tramo | RF-08, RF-09, RF-16, RNF-C2 | RE-1, RE-5 |
| **DEC-3** Inventario por cabina | Dónde vive el control de sobreventa | RF-10, RNF-C1 | **RE-1** |
| **DEC-4** Bloqueo pesimista | Concurrencia sobre la última silla | RNF-C1, RNF-C2 | **RE-1** |
| **DEC-5** Precio congelado + pago delegado | Contrato inmutable, alcance PCI | RF-04, RF-12, RF-14, RNF-S1 | RE-4, RE-5 |
| **DEC-6** Monolito modular | Transacción única para el inventario | RNF-C1, RNF-P1, RNF-E3 | RE-1, RE-2 |
| **DEC-7** API B2B separada | Canal de agencias | RF-20 a RF-22, RNF-E2, RNF-S5 | RE-2, RE-4 |
| **DEC-8** Caché y réplicas | Rendimiento de búsqueda | RNF-P1, RNF-P3, RNF-E1 | RE-2, RE-6 |
| **DEC-9** Multi-AZ y degradación | 99,9% | RNF-D1, RNF-D3 | RE-3 |
| **DEC-10** Auditoría transaccional | Historial reconstruible | RF-18, RNF-A1, RNF-A2 | RE-5 |
| **DEC-11** ETL desde réplica | Aislar analítica de transaccional | RF-30, RNF-P4 | RE-2 |
| **DEC-12** Híbrido SSR/SPA | Primer resultado rápido | RNF-P1, RNF-P2 | RE-2 |
| **DEC-13** Reloj + revalidación + SSE | Disponibilidad en tiempo real | RNF-C1, RNF-C3 | RE-1, RE-2 |
| **DEC-14** Consola B2B | Oficio distinto, riesgo distinto | RF-20, RF-23, RNF-S5 | RE-4 |
| **DEC-15** i18n y multi-moneda | Precio estable, hora local | RF-04, SUP-12 | RE-5 |
