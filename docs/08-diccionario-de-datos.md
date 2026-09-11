# Diccionario de datos — modelo transaccional

> Documentación tabla por tabla del esquema `airline`: **qué contiene cada una, por qué existe y cómo se relaciona con las demás.**
>
> Fuente: [`backend/sql/01_schema.sql`](../backend/sql/01_schema.sql). Todo lo que aparece aquí está tomado del DDL real, no de un diseño en papel.
>
> **Cómo leerlo:** cada tabla lleva una explicación de *por qué existe* (que es lo que se sustenta en la defensa), la lista de columnas con sus restricciones, y sus relaciones en ambos sentidos: hacia qué apunta y quién le apunta.
>
> Notación: `PK` clave primaria · `FK` clave foránea · `UK` restricción de unicidad · `NN` obligatorio (`NOT NULL`)

---

## Panorama general

**26 tablas y 4 vistas**, organizadas en ocho bloques funcionales. El orden no es arbitrario: las claves foráneas lo imponen (no se puede crear una tabla que referencie a otra inexistente), y además va de lo más estable a lo más volátil.

```
CATÁLOGO (estable, cambia pocas veces al año)
  cabin · airport · route · aircraft_model · seat_map · seat_map_seat · aircraft
        │
        ▼
PROGRAMACIÓN (cambia por temporada / a diario)
  scheduled_flight ──▶ flight_instance ──▶ flight_inventory  ◀── LA TABLA CRÍTICA
        │
        ▼
TARIFAS (cambia varias veces al día)
  fare_rule · fare_class · fare
        │
        ▼
PERSONAS                     RESERVA (cambia miles de veces al día)
  passenger · agency ──▶ reservation ──▶ itinerary ──▶ itinerary_segment
                              │
                              ▼
EMISIÓN                  PAGOS                    AUDITORÍA
  ticket                   payment                  reservation_event
  seat_assignment          payment_event            (solo inserción)
  ancillary                refund
```

---

## Bloque 1 — Catálogo de red y flota

### `cabin`

**Por qué existe:** es el catálogo de cabinas físicas del avión (`ECO`, `BUS`). Se modeló como **entidad y no como tipo `ENUM`** por una razón práctica: agregar una cabina `PREMIUM_ECO` debe ser un `INSERT`, no una migración de tipo de dato que obligue a reescribir todas las tablas que la referencian.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `cabin_code` | `VARCHAR(10)` | **PK** | Código corto: `ECO`, `BUS` |
| `name` | `VARCHAR(40)` | NN | Nombre comercial: "Económica", "Ejecutiva" |
| `boarding_priority` | `SMALLINT` | NN | Orden de abordaje (1 = primero) |

**Le apuntan:** `seat_map_seat`, `flight_inventory`, `fare_class`, `itinerary_segment`.

---

### `airport`

**Por qué existe:** los nodos de la red. Guarda dos datos que no son decorativos: la **zona horaria** (sin ella no se puede calcular el horario UTC de un vuelo) y el **tiempo mínimo de conexión**.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `iata_code` | `CHAR(3)` | **PK** | Código IATA: `BOG`, `MDE` |
| `icao_code` | `CHAR(4)` | | Código OACI |
| `name`, `city` | `VARCHAR` | NN | Nombre del aeropuerto y ciudad |
| `country_code` | `CHAR(2)` | NN | ISO del país |
| `timezone` | `VARCHAR(50)` | NN | Zona horaria IANA (`America/Bogota`) |
| `min_connection_min` | `SMALLINT` | NN, entre 0 y 480, por defecto 45 | **MCT**: minutos mínimos para conectar aquí |

> **El detalle defendible:** `min_connection_min` es **por aeropuerto**, no una constante global. Conectar en un aeropuerto grande y congestionado toma más que en uno pequeño. La validación de itinerarios (RF-09) lo usa por nodo, y la búsqueda con escalas también.

**Le apuntan:** `route` (dos veces: origen y destino), `itinerary`, `seat_assignment` indirectamente vía vuelo.

---

### `route`

**Por qué existe:** un par origen-destino comercializable. Es la unidad sobre la que se define el precio y sobre la que la gerencia pide "ocupación por ruta".

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `route_id` | `SERIAL` | **PK** | |
| `origin_iata` | `CHAR(3)` | NN, **FK** → `airport` | Aeropuerto de salida |
| `destination_iata` | `CHAR(3)` | NN, **FK** → `airport` | Aeropuerto de llegada |
| `distance_km` | `INTEGER` | NN, > 0 | Usada para calcular tarifas proporcionales |
| `is_active` | `BOOLEAN` | NN, por defecto `TRUE` | Permite retirar una ruta sin borrarla |

**Restricciones:** `UNIQUE (origin_iata, destination_iata)` y `CHECK (origin_iata <> destination_iata)` — no existe una ruta de un aeropuerto a sí mismo.

> **La ruta es dirigida:** BOG→MDE y MDE→BOG son **dos filas distintas**. Es deliberado: pueden tener precios, horarios y demanda completamente diferentes.

---

### `aircraft_model` · `seat_map` · `seat_map_seat` · `aircraft`

**Por qué existen cuatro tablas y no una:** porque hay tres niveles de realidad distintos y confundirlos rompe el modelo.

- **`aircraft_model`** — el *modelo* (A320, ATR-72). Hay 12 aviones pero solo 3 modelos: separarlo evita repetir los datos del fabricante en cada avión.
- **`seat_map`** — una *configuración de cabina* de un modelo. Un mismo A320 puede tener una configuración de 180 sillas y otra de 150 con más ejecutiva.
- **`seat_map_seat`** — cada *silla individual* de esa configuración, con su cabina, si es ventana, pasillo o fila de emergencia.
- **`aircraft`** — el *avión físico*, identificado por su matrícula, que es su identidad real ante la autoridad aeronáutica.

| `aircraft_model` | Tipo | Restricción |
|---|---|---|
| `model_code` | `VARCHAR(20)` | **PK** |
| `manufacturer`, `name` | `VARCHAR` | NN |

| `seat_map` | Tipo | Restricción |
|---|---|---|
| `seat_map_id` | `SERIAL` | **PK** |
| `model_code` | `VARCHAR(20)` | NN, **FK** → `aircraft_model` |
| `name` | `VARCHAR(60)` | NN, `UNIQUE (model_code, name)` |

| `seat_map_seat` | Tipo | Restricción |
|---|---|---|
| `seat_map_id` | `INTEGER` | **PK** parcial, **FK** → `seat_map` (`ON DELETE CASCADE`) |
| `seat_number` | `VARCHAR(4)` | **PK** parcial — `14C` |
| `cabin_code` | `VARCHAR(10)` | NN, **FK** → `cabin` |
| `row_num`, `seat_letter` | `SMALLINT`, `CHAR(1)` | NN |
| `is_window`, `is_aisle`, `is_exit_row` | `BOOLEAN` | NN |

| `aircraft` | Tipo | Restricción |
|---|---|---|
| `aircraft_id` | `SERIAL` | **PK** |
| `registration` | `VARCHAR(10)` | NN, **UK** — la matrícula (`HK-4812`) |
| `model_code` | `VARCHAR(20)` | NN, **FK** → `aircraft_model` |
| `seat_map_id` | `INTEGER` | NN, **FK** → `seat_map` |
| `status` | `VARCHAR(20)` | `ACTIVE` / `MAINTENANCE` / `RETIRED` |

> **Aquí nace la capacidad.** La capacidad por cabina **se deriva contando filas de `seat_map_seat`** (la vista `v_seat_map_capacity` hace exactamente eso) y se copia a `flight_inventory` al materializar el vuelo. No se escribe a mano en ningún lado.

---

## Bloque 2 — Programación y operación

### `scheduled_flight`

**Por qué existe:** es la **plantilla recurrente** — "el AV-8320 sale 07:15 de BOG a MDE, de lunes a viernes, entre abril y octubre". Separarla de la instancia concreta es la decisión **DEC-1**.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `scheduled_flight_id` | `SERIAL` | **PK** | |
| `flight_number` | `VARCHAR(8)` | NN | `AV-8320` |
| `route_id` | `INTEGER` | NN, **FK** → `route` | |
| `departure_time_local` / `arrival_time_local` | `TIME` | NN | Hora **local**, no UTC |
| `arrival_day_offset` | `SMALLINT` | 0 o 1 | 1 cuando aterriza al día siguiente |
| `days_of_week` | `CHAR(7)` | NN | 7 posiciones ISO: `1234500` = lunes a viernes |
| `valid_from` / `valid_to` | `DATE` | NN, `valid_to >= valid_from` | Vigencia de la programación |
| `seat_map_id` | `INTEGER` | NN, **FK** → `seat_map` | Configuración prevista |

> **Por qué no una sola tabla `flight` con una fila por fecha:** cambiar el horario de un vuelo recurrente obligaría a actualizar ~250 filas, con riesgo de tocar fechas ya vendidas.

---

### `flight_instance`

**Por qué existe:** el vuelo **en una fecha concreta**, que es lo que realmente se vende. Recibe además los eventos operativos (retrasos, cambios de avión) del sistema externo.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `flight_instance_id` | `BIGSERIAL` | **PK** | |
| `scheduled_flight_id` | `INTEGER` | NN, **FK** → `scheduled_flight` | De qué plantilla salió |
| `flight_date` | `DATE` | NN | |
| `departure_utc` / `arrival_utc` | `TIMESTAMPTZ` | NN, `arrival > departure` | **Precalculados** al materializar |
| `aircraft_id` | `INTEGER` | **FK** → `aircraft`, **puede ser nulo** | Nulo hasta que operaciones asigna avión |
| `status` | `VARCHAR(20)` | `SCHEDULED`/`DELAYED`/`DEPARTED`/`ARRIVED`/`CANCELLED` | |

**Restricción clave:** `UNIQUE (scheduled_flight_id, flight_date)` — impide materializar dos veces el mismo vuelo del mismo día.

**Índices:** `ix_flight_instance_search (flight_date, scheduled_flight_id)` filtrado por `status <> 'CANCELLED'` — es el que sostiene el umbral de rendimiento de la búsqueda.

> **Por qué `departure_utc` está precalculado** aunque sea derivable: la aritmética de zonas horarias con horario de verano dentro de una consulta de búsqueda es cara y propensa a error. Es una de las **tres denormalizaciones declaradas** del modelo.

---

### `flight_inventory` — la tabla crítica del sistema

**Por qué existe:** es el **punto único de control de sobreventa**. Una fila por cada combinación de vuelo y cabina. **Es la fila que se bloquea** con `SELECT … FOR UPDATE` en cada reserva.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `flight_instance_id` | `BIGINT` | **PK** parcial, **FK** → `flight_instance` | |
| `cabin_code` | `VARCHAR(10)` | **PK** parcial, **FK** → `cabin` | |
| `capacity` | `SMALLINT` | NN, >= 0 | Copiada del mapa de sillas al materializar |
| `seats_sold` | `SMALLINT` | NN, por defecto 0 | Sillas ya pagadas |
| `seats_held` | `SMALLINT` | NN, por defecto 0 | Sillas retenidas sin pagar |
| `oversell_factor` | `NUMERIC(4,2)` | Entre 1.00 y 1.50, por defecto 1.00 | 1.00 = sin sobreventa |
| `version` | `INTEGER` | NN, por defecto 0 | Permitiría migrar a bloqueo optimista sin cambiar el esquema |
| `updated_at` | `TIMESTAMPTZ` | NN | |

**Las dos restricciones que sostienen el sistema entero:**

```sql
CONSTRAINT ck_inventory_non_negative
    CHECK (seats_sold >= 0 AND seats_held >= 0)

CONSTRAINT ck_inventory_no_oversell
    CHECK (seats_sold + seats_held <= FLOOR(capacity * oversell_factor))
```

> **La segunda es deliberadamente redundante** con la lógica de la aplicación. Es la **última línea de defensa**: si un error de programación, una migración o una consulta manual intentaran sobrevender, la transacción falla en el motor. *La regla de negocio más importante del sistema no vive únicamente en el código.*
>
> Y `oversell_factor` es **columna y no constante** por una razón de diseño: si el negocio pidiera permitir 5% de sobreventa, es un `UPDATE`, no un rediseño.

---

## Bloque 3 — Tarifas

### `fare_rule`

**Por qué existe:** separa las **condiciones comerciales** del precio. Dos tarifas con el mismo valor pueden tener condiciones opuestas, y es la condición —no el número— lo que determina qué puede hacer el pasajero.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `fare_rule_id` | `SERIAL` | **PK** | |
| `name` | `VARCHAR(60)` | NN | "Promo anticipada 21d" |
| `min_advance_days` | `SMALLINT` | NN, >= 0 | **Implementa la tarifa según anticipación** del enunciado |
| `max_advance_days` | `SMALLINT` | | Tope opcional |
| `is_refundable` / `refund_penalty` | `BOOLEAN` / `NUMERIC(12,2)` | NN | Reembolsabilidad y su costo |
| `is_changeable` / `change_penalty` | `BOOLEAN` / `NUMERIC(12,2)` | NN | Cambios y su penalidad |
| `baggage_pieces` | `SMALLINT` | NN, >= 0 | Maletas en bodega incluidas |
| `free_seat_choice` | `BOOLEAN` | NN | Si la selección de silla es gratuita |

---

### `fare_class`

**Por qué existe:** es el **producto comercial** que se vende dentro de una cabina. `P`, `Y` y `B` son el mismo asiento físico en económica — lo que cambia es la flexibilidad.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `fare_class_code` | `CHAR(1)` | **PK** | `P`, `Y`, `B`, `J` |
| `cabin_code` | `VARCHAR(10)` | NN, **FK** → `cabin` | En qué cabina se vuela |
| `name` | `VARCHAR(40)` | NN | "Económica promo" |
| `rank` | `SMALLINT` | NN | **Ordena de más restrictiva a más flexible** |

> `rank` es la columna que responde "¿por qué están ordenadas así?": codifica la escalera comercial. La aerolínea vende barato y temprano (promo, 21 días de anticipación) y caro y tarde (flexible, sin anticipación mínima).

---

### `fare`

**Por qué existe:** el **precio** de una clase en una ruta, vigente en un rango de fechas. Es la intersección de las tres dimensiones anteriores.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `fare_id` | `SERIAL` | **PK** | |
| `route_id` | `INTEGER` | NN, **FK** → `route` | |
| `fare_class_code` | `CHAR(1)` | NN, **FK** → `fare_class` | |
| `fare_rule_id` | `INTEGER` | NN, **FK** → `fare_rule` | Condiciones que aplican |
| `currency` | `CHAR(3)` | NN | |
| `base_amount` | `NUMERIC(12,2)` | NN, >= 0 | Tarifa antes de impuestos |
| `tax_pct` | `NUMERIC(5,2)` | NN, por defecto 19.00 | IVA |
| `valid_from` / `valid_to` | `DATE` | NN, `valid_to >= valid_from` | Vigencia |

**Índice:** `ix_fare_lookup (route_id, fare_class_code, currency, valid_from, valid_to)` — sostiene la resolución de tarifa elegible en cada búsqueda y cada reserva.

---

## Bloque 4 — Personas y agencias

### `passenger`

**Por qué existe:** los datos personales del viajero. **Es la tabla con información sensible del sistema.**

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `passenger_id` | `BIGSERIAL` | **PK** | |
| `first_name` / `last_name` | `VARCHAR(80)` | NN | El apellido es parte del control de acceso a la reserva |
| `birth_date` | `DATE` | | Determina si es adulto, niño o infante |
| `document_type` / `document_number` | `VARCHAR` | NN, **UK conjunta** | Identidad real de la persona |
| `email`, `phone` | `VARCHAR` | | Contacto |
| `loyalty_id` | `VARCHAR(40)` | | **Reservado y nulo**: no hay programa de viajero frecuente en v1 |
| `created_at` | `TIMESTAMPTZ` | NN | |

> `UNIQUE (document_type, document_number)` hace que una persona sea **una sola fila** aunque viaje cien veces. El `loyalty_id` está reservado a propósito: deja la puerta abierta a un programa de millas sin rediseñar nada.

---

### `agency`

**Por qué existe:** las agencias aliadas venden con tarifa neta y cobran comisión, contra un cupo de crédito en vez de una tarjeta.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `agency_id` | `SERIAL` | **PK** | |
| `legal_name` | `VARCHAR(140)` | NN | |
| `tax_id` | `VARCHAR(40)` | NN, **UK** | NIT |
| `net_fare_discount_pct` | `NUMERIC(5,2)` | 0 a 100 | Descuento contractual sobre tarifa pública |
| `commission_pct` | `NUMERIC(5,2)` | 0 a 100 | Comisión que devenga por venta |
| `credit_limit` / `credit_used` | `NUMERIC(14,2)` | NN, >= 0 | Cupo y consumo |
| `currency` | `CHAR(3)` | NN | |
| `status` | `VARCHAR(20)` | `ACTIVE` / `SUSPENDED` | |

**Restricción clave:** `CHECK (credit_used <= credit_limit)` — respalda a nivel de motor la validación de cupo, igual que la restricción de no-sobreventa respalda el inventario. **El mismo patrón de defensa en profundidad.**

---

## Bloque 5 — Reserva

### `reservation`

**Por qué existe:** es **el PNR**: la unidad de compra, de pago y de contacto con el pasajero.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `reservation_id` | `BIGSERIAL` | **PK** | Interno |
| `pnr` | `CHAR(6)` | NN, **UK** | El localizador que ve el pasajero. Aleatorio, no secuencial |
| `channel` | `VARCHAR(20)` | `WEB` / `B2B` / `COUNTER` | Por dónde entró la venta |
| `agency_id` | `INTEGER` | **FK** → `agency`, nulo si no es B2B | |
| `contact_email` / `contact_phone` | `VARCHAR` | email NN | |
| `status` | `VARCHAR(20)` | `HELD`/`CONFIRMED`/`CANCELLED`/`EXPIRED` | |
| `currency` | `CHAR(3)` | NN | Fijada al inicio, no se reconvierte |
| `total_amount` | `NUMERIC(14,2)` | NN, >= 0 | |
| `hold_expires_at` | `TIMESTAMPTZ` | Solo mientras está en `HELD` | Cuándo vence la retención |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | NN | |

**Dos restricciones que hacen imposible un estado incoherente:**

- `ck_reservation_hold` — si el estado es `HELD`, **obliga** a que `hold_expires_at` no sea nulo. No puede existir una retención sin fecha de vencimiento.
- `ck_reservation_agency` — si el canal es `B2B`, **obliga** a que haya agencia; y si no lo es, **prohíbe** que la haya.

**Índices:** `ix_reservation_expiry (hold_expires_at) WHERE status = 'HELD'` — índice **parcial**, solo indexa las reservas retenidas, que son las únicas que el proceso de expiración necesita recorrer.

---

### `reservation_passenger`

**Por qué existe:** vincula pasajeros al PNR y, sobre todo, **les da un identificador local**.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `reservation_id` | `BIGINT` | **PK** parcial, **FK** → `reservation` (`CASCADE`) | |
| `passenger_ref` | `SMALLINT` | **PK** parcial | **1, 2, 3… dentro de este PNR** |
| `passenger_id` | `BIGINT` | NN, **FK** → `passenger` | La persona real |
| `passenger_type` | `VARCHAR(3)` | `ADT` / `CHD` / `INF` | Adulto, niño, infante |

> **Dos decisiones concentradas en una tabla pequeña.** Primero, `passenger_ref` permite emitir tiquetes referidos al "pasajero 2 de este PNR" sin exponer el identificador global de la persona. Segundo, `passenger_type = 'INF'` **no consume inventario**: un infante viaja en brazos, y el cálculo de sillas lo excluye en los cuatro caminos que tocan contadores.

---

### `itinerary`

**Por qué existe:** es **un viaje** dentro del PNR — la ida, o el regreso. Es la **unidad de cancelación y de tarifación**.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `itinerary_id` | `BIGSERIAL` | **PK** | |
| `reservation_id` | `BIGINT` | NN, **FK** → `reservation` (`CASCADE`) | |
| `sequence_no` | `SMALLINT` | NN, `UNIQUE (reservation_id, sequence_no)` | 1 = ida, 2 = regreso |
| `origin_iata` / `destination_iata` | `CHAR(3)` | NN, **FK** → `airport` | **Del viaje completo**, no del primer tramo |
| `status` | `VARCHAR(20)` | `ACTIVE` / `CANCELLED` | |

> Que el origen y destino sean **los del viaje** y no los del primer tramo es lo que permite responder "ocupación por ruta comercial": si alguien vuela BOG→MDE→CTG, su viaje es BOG-CTG aunque ningún tramo se llame así.

---

### `itinerary_segment`

**Por qué existe:** es **un tramo volado** — la **unidad de inventario**. Cada tramo activo consume una silla en su `flight_inventory`.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `segment_id` | `BIGSERIAL` | **PK** | |
| `itinerary_id` | `BIGINT` | NN, **FK** → `itinerary` (`CASCADE`) | |
| `sequence_no` | `SMALLINT` | NN, `UNIQUE (itinerary_id, sequence_no)` | Orden dentro del viaje |
| `flight_instance_id` | `BIGINT` | NN, **FK** → `flight_instance` | Qué vuelo concreto |
| `cabin_code` | `VARCHAR(10)` | NN, **FK** → `cabin` | |
| `fare_class_code` | `CHAR(1)` | NN, **FK** → `fare_class` | |
| `quoted_fare_id` | `INTEGER` | NN, **FK** → `fare` | Tarifa vigente **al retener** |
| `quoted_fare_amount` / `quoted_tax_amount` | `NUMERIC(12,2)` | NN, >= 0 | **Precio congelado al retener** |
| `status` | `VARCHAR(20)` | `ACTIVE` / `CANCELLED` | |

**La restricción más interesante del esquema** es una clave foránea **compuesta**:

```sql
FOREIGN KEY (flight_instance_id, cabin_code)
    REFERENCES flight_inventory (flight_instance_id, cabin_code)
```

> Garantiza que **no puede existir un tramo vendido en una combinación de vuelo y cabina que no tenga fila de inventario**. Sin ella se podría vender un asiento en una cabina que ese avión no tiene, y nadie lo notaría hasta el embarque.

> **Las columnas `quoted_*` son una desviación declarada (D-1)** frente al diseño original: el precio se congela **al retener**, no al emitir. Entre el `HELD` y el pago pasan 20 minutos, y las tarifas cambian varias veces al día — sin esto, el pasajero vería un precio al reservar y se le cobraría otro al pagar.

---

## Bloque 6 — Emisión

### `ticket`

**Por qué existe:** es el **documento de valor**: un tiquete por cada combinación de pasajero y tramo. Es además **el grano del hecho analítico** del modelo estrella.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `ticket_id` | `BIGSERIAL` | **PK** | |
| `ticket_number` | `VARCHAR(20)` | NN, **UK** | Número del documento |
| `reservation_id` + `passenger_ref` | `BIGINT` + `SMALLINT` | NN, **FK compuesta** → `reservation_passenger` | A qué pasajero del PNR pertenece |
| `segment_id` | `BIGINT` | NN, **FK** → `itinerary_segment` | Qué tramo documenta |
| `fare_id` | `INTEGER` | NN, **FK** → `fare` | Tarifa aplicada |
| `fare_amount` | `NUMERIC(12,2)` | NN, >= 0 | **Congelado** |
| `tax_amount`, `surcharge_amount`, `discount_amount`, `commission_amount` | `NUMERIC(12,2)` | NN, >= 0 | Desglose completo, congelado |
| `currency` | `CHAR(3)` | NN | |
| `status` | `VARCHAR(20)` | `ISSUED`/`USED`/`CANCELLED`/`REFUNDED` | |
| `issued_at` / `cancelled_at` | `TIMESTAMPTZ` | | |

**Restricción:** `UNIQUE (segment_id, reservation_id, passenger_ref)` — impide emitir dos tiquetes al mismo pasajero para el mismo tramo.

**Índices:** `ix_ticket_issued_at` sostiene la **extracción incremental del ETL** — sin él, cada corrida tendría que escanear la tabla completa.

> **Por qué los montos están congelados y no se recalculan:** un tiquete es un **contrato**. Si mañana cambia la tarifa o la penalidad, el tiquete vendido ayer no puede cambiar de precio al consultarlo. Es una denormalización **temporal**, no un descuido: el dato no es "el precio de la tarifa", es "el precio que este pasajero pagó".

---

### `seat_assignment`

**Por qué existe:** la silla física asignada, normalmente en el check-in.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `ticket_id` | `BIGINT` | **PK** y **FK** → `ticket` (`CASCADE`) | Un tiquete, una silla |
| `flight_instance_id` | `BIGINT` | NN, **FK** → `flight_instance` | |
| `seat_number` | `VARCHAR(4)` | NN | `14C` |
| `assigned_at` / `assigned_by` | `TIMESTAMPTZ` / `VARCHAR(40)` | NN | Quién la asignó |
| `price` | `NUMERIC(12,2)` | NN | Costo de elegir silla |

**Restricción:** `UNIQUE (flight_instance_id, seat_number)` — dos pasajeros no pueden tener la misma silla.

> **Esta tabla NO participa en el control de capacidad**, y eso es el corazón del diseño. La unicidad de la silla es un problema de **integridad**; la capacidad es un problema de **concurrencia**. Se resuelven con mecanismos distintos: una restricción `UNIQUE` aquí, un bloqueo pesimista allá. Mezclarlos habría multiplicado el punto de contención por 180.

---

### `ancillary`

**Por qué existe:** productos auxiliares vendidos sobre un tiquete. Sin esta tabla, los ingresos por equipaje no aparecerían en el análisis de ingresos.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `ancillary_id` | `BIGSERIAL` | **PK** | |
| `ticket_id` | `BIGINT` | NN, **FK** → `ticket` (`CASCADE`) | |
| `ancillary_type` | `VARCHAR(20)` | `BAGGAGE`/`SEAT`/`INSURANCE`/`MEAL` | |
| `description` | `VARCHAR(120)` | | |
| `amount` | `NUMERIC(12,2)` | NN, >= 0 | |
| `currency` | `CHAR(3)` | NN | |
| `status` | `VARCHAR(20)` | `ACTIVE`/`CANCELLED`/`REFUNDED` | |

---

## Bloque 7 — Pagos

### `payment`

**Por qué existe:** el intento de cobro. **Lo más importante de esta tabla es lo que NO contiene.**

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `payment_id` | `BIGSERIAL` | **PK** | |
| `reservation_id` | `BIGINT` | NN, **FK** → `reservation` | |
| `method` | `VARCHAR(20)` | `CARD` / `AGENCY_CREDIT` | |
| `psp_provider` | `VARCHAR(40)` | | Qué pasarela |
| `psp_intent_id` | `VARCHAR(120)` | **UK** | **Referencia opaca del proveedor** |
| `amount` / `currency` | `NUMERIC(14,2)` / `CHAR(3)` | NN | |
| `status` | `VARCHAR(20)` | `PENDING`/`AUTHORIZED`/`CAPTURED`/`FAILED`/`REFUNDED` | |
| `idempotency_key` | `VARCHAR(80)` | NN, **UK** | Impide el doble cobro por reintento |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | NN | |

> **No hay ninguna columna para el número de tarjeta, ni para el CVV, ni para la fecha de vencimiento — y eso es intencional.** El sistema solo guarda una referencia opaca del proveedor. Es lo que reduce el alcance de cumplimiento PCI-DSS al nivel más bajo, y es la decisión de seguridad de mayor impacto del proyecto.

---

### `payment_event`

**Por qué existe:** registra los webhooks recibidos del proveedor de pagos, y **es lo que hace idempotente la confirmación**.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `payment_event_id` | `BIGSERIAL` | **PK** | |
| `payment_id` | `BIGINT` | NN, **FK** → `payment` | |
| `psp_event_id` | `VARCHAR(120)` | NN, **UK** | **La unicidad es el mecanismo de idempotencia** |
| `event_type` | `VARCHAR(60)` | NN | `payment.captured` |
| `payload` | `JSONB` | NN | El evento crudo, para auditoría |
| `received_at` | `TIMESTAMPTZ` | NN | |

> **Cómo funciona la idempotencia, en una frase:** si el proveedor reentrega diez veces el mismo evento, los intentos 2 al 10 **chocan contra el índice único** de `psp_event_id` y se descartan sin duplicar tiquetes ni cobros. No hace falta lógica adicional: la restricción *es* el mecanismo.

---

### `refund`

**Por qué existe:** registra las devoluciones. Apunta al pago, no a la reserva, porque **se reembolsa un cobro concreto**, no una reserva en abstracto.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `refund_id` | `BIGSERIAL` | **PK** | |
| `payment_id` | `BIGINT` | NN, **FK** → `payment` | |
| `amount` | `NUMERIC(14,2)` | NN, >= 0 | |
| `reason` | `VARCHAR(120)` | | |
| `status` | `VARCHAR(20)` | `PENDING`/`COMPLETED`/`FAILED` | |
| `created_at` | `TIMESTAMPTZ` | NN | |

---

## Bloque 8 — Auditoría

### `reservation_event`

**Por qué existe:** es el **log inmutable** de toda transición de estado. Cumple una función de cumplimiento y, sin haberlo previsto, resultó ser una fuente analítica imprescindible.

| Columna | Tipo | Restricción | Descripción |
|---|---|---|---|
| `event_id` | `BIGSERIAL` | **PK** | |
| `reservation_id` | `BIGINT` | NN, **FK** → `reservation` | |
| `occurred_at` | `TIMESTAMPTZ` | NN | Cuándo pasó |
| `actor_type` | `VARCHAR(20)` | `PASSENGER`/`AGENT`/`STAFF`/`ADMIN`/`SYSTEM`/`PSP` | Quién lo hizo |
| `actor_id` | `VARCHAR(80)` | | Identidad nominal |
| `channel` | `VARCHAR(20)` | NN | Por dónde |
| `event_type` | `VARCHAR(40)` | NN | `RESERVATION_HELD`, `RESERVATION_CONFIRMED`… |
| `from_status` / `to_status` | `VARCHAR(20)` | | Transición |
| `payload` | `JSONB` | NN | Detalle del evento |

**Inmutabilidad real, no declarada.** Hay un *trigger* que rechaza cualquier modificación:

```sql
CREATE TRIGGER trg_reservation_event_immutable
    BEFORE UPDATE OR DELETE ON reservation_event
    FOR EACH ROW EXECUTE FUNCTION airline.deny_mutation();
```

> **La anécdota que lo valida:** durante el desarrollo, las pruebas automatizadas **no pudieron borrar** el log para limpiar el entorno — el trigger las bloqueó y hubo que usar `TRUNCATE`. Que borrar el historial sea difícil incluso desde una prueba propia es la señal de que la garantía está realmente implementada.
>
> **Y este log es la única fuente posible de los patrones de cancelación.** Una reserva cancelada que solo guarda su estado final no dice ni cuándo se canceló, ni cuánto vivió, ni a cuántos días de la salida. Un requisito de cumplimiento terminó habilitando una capacidad de negocio.

---

## Bloque 9 — Vistas de apoyo

| Vista | Para qué sirve |
|---|---|
| **`v_flight_availability`** | Une vuelo, ruta e inventario y calcula `seats_available`. Es lo que consulta la **búsqueda**. Lectura sin bloqueo: la verdad se verifica al retener, no al buscar |
| **`v_seat_map_capacity`** | Deriva la capacidad por cabina **contando** sillas del mapa. Se usa al materializar vuelos, para no escribir capacidades a mano |
| **`v_oversell_check`** | **La aserción del invariante.** Devuelve las filas donde `vendidas + retenidas > capacidad`. **Debe devolver cero filas siempre**; es lo que verifica la prueba de concurrencia |
| **`v_inventory_reconciliation`** | Contrasta el contador materializado contra los tramos realmente vendidos. Cualquier diferencia indica un error en el servicio de inventario — es la vigilancia de la denormalización declarada |

---

## Mapa completo de relaciones

| Desde | Hacia | Tipo | Qué significa |
|---|---|---|---|
| `route` | `airport` ×2 | N:1 | Origen y destino |
| `seat_map` | `aircraft_model` | N:1 | Configuración de un modelo |
| `seat_map_seat` | `seat_map` (cascada), `cabin` | N:1 | Sillas de una configuración |
| `aircraft` | `aircraft_model`, `seat_map` | N:1 | Avión físico y su configuración |
| `scheduled_flight` | `route`, `seat_map` | N:1 | Plantilla recurrente |
| `flight_instance` | `scheduled_flight`, `aircraft` (opcional) | N:1 | Vuelo en una fecha |
| `flight_inventory` | `flight_instance`, `cabin` | N:1 | **Control de capacidad** |
| `fare_class` | `cabin` | N:1 | Producto dentro de una cabina |
| `fare` | `route`, `fare_class`, `fare_rule` | N:1 | Precio vigente |
| `reservation` | `agency` (opcional) | N:1 | Venta por agencia |
| `reservation_passenger` | `reservation` (cascada), `passenger` | N:1 | Pasajeros del PNR |
| `itinerary` | `reservation` (cascada), `airport` ×2 | N:1 | Viaje dentro del PNR |
| `itinerary_segment` | `itinerary` (cascada), `flight_instance`, `cabin`, `fare_class`, `fare`, **`flight_inventory` (compuesta)** | N:1 | **Tramo = unidad de inventario** |
| `ticket` | `itinerary_segment`, `fare`, **`reservation_passenger` (compuesta)** | N:1 | Documento de valor |
| `seat_assignment` | `ticket` (cascada, y PK), `flight_instance` | 1:1 | Silla física |
| `ancillary` | `ticket` (cascada) | N:1 | Producto auxiliar |
| `payment` | `reservation` | N:1 | Intento de cobro |
| `payment_event` | `payment` | N:1 | Webhook recibido |
| `refund` | `payment` | N:1 | Devolución |
| `reservation_event` | `reservation` | N:1 | **Auditoría inmutable** |

### Dónde hay borrado en cascada, y dónde no — a propósito

**Sí hay cascada** en las relaciones de *pertenencia*: los pasajeros, itinerarios y tramos **pertenecen** a la reserva; las sillas y auxiliares **pertenecen** al tiquete. Si desaparece el padre, el hijo no tiene sentido.

**No hay cascada** desde `reservation` hacia `ticket`, `payment` ni `reservation_event`. **Es deliberado: en producción una reserva no se borra, se cancela.** El tiquete es un documento de valor, el pago es un movimiento de dinero y el evento es un registro de auditoría — ninguno puede desaparecer porque alguien borró la fila padre.

> Esto se comprobó de forma accidental durante el desarrollo: al intentar limpiar la base en las pruebas, el `DELETE FROM reservation` **falló** por estas dos razones a la vez, y ambas eran correctas. **La prueba chocó contra las garantías del propio diseño y las garantías ganaron.**
