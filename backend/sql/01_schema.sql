-- =====================================================================
--  Sistema de Reservas — Aerolínea Regional
--  Esquema transaccional (OLTP) — PostgreSQL 16
--
--  Implementa el modelo entidad-relación de la sección 5.3.
--  Cada bloque referencia la decisión de diseño (DEC-n) que lo origina.
-- =====================================================================

DROP SCHEMA IF EXISTS airline CASCADE;
CREATE SCHEMA airline;
SET search_path TO airline, public;

-- ---------------------------------------------------------------------
-- 1. CATÁLOGO DE RED Y FLOTA
-- ---------------------------------------------------------------------

CREATE TABLE cabin (
    cabin_code        VARCHAR(10) PRIMARY KEY,
    name              VARCHAR(40)  NOT NULL,
    boarding_priority SMALLINT     NOT NULL
);
COMMENT ON TABLE cabin IS
  'Catálogo de cabinas. Es entidad y no ENUM: agregar PREMIUM_ECO debe ser INSERT, no migración.';

CREATE TABLE airport (
    iata_code          CHAR(3) PRIMARY KEY,
    icao_code          CHAR(4),
    name               VARCHAR(120) NOT NULL,
    city               VARCHAR(80)  NOT NULL,
    country_code       CHAR(2)      NOT NULL,
    timezone           VARCHAR(50)  NOT NULL,
    -- MCT: tiempo mínimo de conexión, por aeropuerto (RF-09).
    min_connection_min SMALLINT     NOT NULL DEFAULT 45
        CHECK (min_connection_min BETWEEN 0 AND 480)
);

CREATE TABLE route (
    route_id         SERIAL PRIMARY KEY,
    origin_iata      CHAR(3) NOT NULL REFERENCES airport(iata_code),
    destination_iata CHAR(3) NOT NULL REFERENCES airport(iata_code),
    distance_km      INTEGER NOT NULL CHECK (distance_km > 0),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_route UNIQUE (origin_iata, destination_iata),
    CONSTRAINT ck_route_distinct CHECK (origin_iata <> destination_iata)
);
COMMENT ON TABLE route IS 'Ruta dirigida: BOG->MDE y MDE->BOG son dos filas distintas.';

CREATE TABLE aircraft_model (
    model_code   VARCHAR(20) PRIMARY KEY,
    manufacturer VARCHAR(40) NOT NULL,
    name         VARCHAR(60) NOT NULL
);

CREATE TABLE seat_map (
    seat_map_id SERIAL PRIMARY KEY,
    model_code  VARCHAR(20) NOT NULL REFERENCES aircraft_model(model_code),
    name        VARCHAR(60) NOT NULL,
    CONSTRAINT uq_seat_map UNIQUE (model_code, name)
);

CREATE TABLE seat_map_seat (
    seat_map_id INTEGER     NOT NULL REFERENCES seat_map(seat_map_id) ON DELETE CASCADE,
    seat_number VARCHAR(4)  NOT NULL,
    cabin_code  VARCHAR(10) NOT NULL REFERENCES cabin(cabin_code),
    row_num     SMALLINT    NOT NULL,
    seat_letter CHAR(1)     NOT NULL,
    is_window   BOOLEAN     NOT NULL DEFAULT FALSE,
    is_aisle    BOOLEAN     NOT NULL DEFAULT FALSE,
    is_exit_row BOOLEAN     NOT NULL DEFAULT FALSE,
    PRIMARY KEY (seat_map_id, seat_number)
);
COMMENT ON TABLE seat_map_seat IS
  'DEC-3: la capacidad por cabina se DERIVA contando aquí, y se copia a flight_inventory al materializar.';

CREATE TABLE aircraft (
    aircraft_id  SERIAL PRIMARY KEY,
    registration VARCHAR(10) NOT NULL UNIQUE,
    model_code   VARCHAR(20) NOT NULL REFERENCES aircraft_model(model_code),
    seat_map_id  INTEGER     NOT NULL REFERENCES seat_map(seat_map_id),
    status       VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'MAINTENANCE', 'RETIRED'))
);

-- ---------------------------------------------------------------------
-- 2. PROGRAMACIÓN Y OPERACIÓN  (DEC-1: programado != instancia)
-- ---------------------------------------------------------------------

CREATE TABLE scheduled_flight (
    scheduled_flight_id  SERIAL PRIMARY KEY,
    flight_number        VARCHAR(8) NOT NULL,
    route_id             INTEGER    NOT NULL REFERENCES route(route_id),
    departure_time_local TIME       NOT NULL,
    arrival_time_local   TIME       NOT NULL,
    -- 1 cuando el vuelo aterriza al día siguiente.
    arrival_day_offset   SMALLINT   NOT NULL DEFAULT 0
        CHECK (arrival_day_offset IN (0, 1)),
    -- 7 posiciones, una por día ISO (lunes=1). '1234500' = lunes a viernes.
    days_of_week         CHAR(7)    NOT NULL,
    valid_from           DATE       NOT NULL,
    valid_to             DATE       NOT NULL,
    seat_map_id          INTEGER    NOT NULL REFERENCES seat_map(seat_map_id),
    CONSTRAINT ck_sched_validity CHECK (valid_to >= valid_from),
    CONSTRAINT uq_sched UNIQUE (flight_number, valid_from)
);
COMMENT ON TABLE scheduled_flight IS
  'DEC-1: plantilla recurrente. Cambiar un horario aquí no toca fechas ya vendidas.';

CREATE TABLE flight_instance (
    flight_instance_id  BIGSERIAL PRIMARY KEY,
    scheduled_flight_id INTEGER     NOT NULL REFERENCES scheduled_flight(scheduled_flight_id),
    flight_date         DATE        NOT NULL,
    -- Precalculado al materializar (denormalización justificada en 5.3.2):
    -- evita aritmética de zonas horarias dentro de la consulta de búsqueda.
    departure_utc       TIMESTAMPTZ NOT NULL,
    arrival_utc         TIMESTAMPTZ NOT NULL,
    aircraft_id         INTEGER     REFERENCES aircraft(aircraft_id),
    status              VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED'
        CHECK (status IN ('SCHEDULED','DELAYED','DEPARTED','ARRIVED','CANCELLED')),
    CONSTRAINT uq_flight_instance UNIQUE (scheduled_flight_id, flight_date),
    CONSTRAINT ck_flight_times CHECK (arrival_utc > departure_utc)
);
COMMENT ON TABLE flight_instance IS
  'DEC-1: vuelo en una fecha concreta. La capacidad es de la instancia, no del programado.';

-- Índice que sostiene RNF-P1 (búsqueda de vuelos directos).
CREATE INDEX ix_flight_instance_search
    ON flight_instance (flight_date, scheduled_flight_id)
    WHERE status <> 'CANCELLED';
CREATE INDEX ix_flight_instance_dep_utc ON flight_instance (departure_utc);

-- =====================================================================
--  TABLA CRÍTICA DEL SISTEMA  (DEC-3 + DEC-4)
--  Punto único de control de sobreventa. Una fila por (vuelo x cabina).
--  Es la fila que se bloquea con SELECT ... FOR UPDATE.
-- =====================================================================
CREATE TABLE flight_inventory (
    flight_instance_id BIGINT      NOT NULL REFERENCES flight_instance(flight_instance_id),
    cabin_code         VARCHAR(10) NOT NULL REFERENCES cabin(cabin_code),
    capacity           SMALLINT    NOT NULL CHECK (capacity >= 0),
    seats_sold         SMALLINT    NOT NULL DEFAULT 0,
    seats_held         SMALLINT    NOT NULL DEFAULT 0,
    -- SUP-6: 1.00 = sin overbooking. Es columna y no constante para que la
    -- restricción hipotética "permitir 5% de overbooking" sea un UPDATE (PE-0).
    oversell_factor    NUMERIC(4,2) NOT NULL DEFAULT 1.00
        CHECK (oversell_factor >= 1.00 AND oversell_factor <= 1.50),
    version            INTEGER     NOT NULL DEFAULT 0,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (flight_instance_id, cabin_code),

    CONSTRAINT ck_inventory_non_negative
        CHECK (seats_sold >= 0 AND seats_held >= 0),

    -- ÚLTIMA LÍNEA DE DEFENSA DE RNF-C1.
    -- Deliberadamente redundante con la lógica de aplicación: si un bug, una
    -- migración o una consulta manual intentaran sobrevender, el motor aborta.
    CONSTRAINT ck_inventory_no_oversell
        CHECK (seats_sold + seats_held <= FLOOR(capacity * oversell_factor))
);
COMMENT ON TABLE flight_inventory IS
  'RNF-C1. Control de sobreventa por conteo por cabina (DEC-3). Bloqueo pesimista (DEC-4).';

-- ---------------------------------------------------------------------
-- 3. TARIFAS
-- ---------------------------------------------------------------------

CREATE TABLE fare_rule (
    fare_rule_id     SERIAL PRIMARY KEY,
    name             VARCHAR(60)  NOT NULL,
    -- Implementa la "tarifa según anticipación" del enunciado.
    min_advance_days SMALLINT     NOT NULL DEFAULT 0 CHECK (min_advance_days >= 0),
    max_advance_days SMALLINT,
    is_refundable    BOOLEAN      NOT NULL DEFAULT FALSE,
    refund_penalty   NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (refund_penalty >= 0),
    is_changeable    BOOLEAN      NOT NULL DEFAULT FALSE,
    change_penalty   NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (change_penalty >= 0),
    baggage_pieces   SMALLINT     NOT NULL DEFAULT 0 CHECK (baggage_pieces >= 0),
    free_seat_choice BOOLEAN      NOT NULL DEFAULT FALSE
);

CREATE TABLE fare_class (
    fare_class_code CHAR(1) PRIMARY KEY,
    cabin_code      VARCHAR(10) NOT NULL REFERENCES cabin(cabin_code),
    name            VARCHAR(40) NOT NULL,
    rank            SMALLINT    NOT NULL
);

CREATE TABLE fare (
    fare_id         SERIAL PRIMARY KEY,
    route_id        INTEGER      NOT NULL REFERENCES route(route_id),
    fare_class_code CHAR(1)      NOT NULL REFERENCES fare_class(fare_class_code),
    fare_rule_id    INTEGER      NOT NULL REFERENCES fare_rule(fare_rule_id),
    currency        CHAR(3)      NOT NULL,
    base_amount     NUMERIC(12,2) NOT NULL CHECK (base_amount >= 0),
    tax_pct         NUMERIC(5,2)  NOT NULL DEFAULT 19.00,
    valid_from      DATE         NOT NULL,
    valid_to        DATE         NOT NULL,
    CONSTRAINT ck_fare_validity CHECK (valid_to >= valid_from)
);
CREATE INDEX ix_fare_lookup ON fare (route_id, fare_class_code, currency, valid_from, valid_to);

-- ---------------------------------------------------------------------
-- 4. PERSONAS Y AGENCIAS
-- ---------------------------------------------------------------------

CREATE TABLE passenger (
    passenger_id    BIGSERIAL PRIMARY KEY,
    first_name      VARCHAR(80) NOT NULL,
    last_name       VARCHAR(80) NOT NULL,
    birth_date      DATE,
    document_type   VARCHAR(20) NOT NULL,
    document_number VARCHAR(40) NOT NULL,
    email           VARCHAR(160),
    phone           VARCHAR(40),
    -- SUP-9: reservado para un futuro programa de viajero frecuente.
    loyalty_id      VARCHAR(40),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_passenger_document UNIQUE (document_type, document_number)
);
COMMENT ON TABLE passenger IS
  'Contiene PII. Sujeta a RNF-S3: cifrado en reposo y anonimización a 5 años.';

CREATE TABLE agency (
    agency_id             SERIAL PRIMARY KEY,
    legal_name            VARCHAR(140) NOT NULL,
    tax_id                VARCHAR(40)  NOT NULL UNIQUE,
    net_fare_discount_pct NUMERIC(5,2) NOT NULL DEFAULT 0
        CHECK (net_fare_discount_pct BETWEEN 0 AND 100),
    commission_pct        NUMERIC(5,2) NOT NULL DEFAULT 0
        CHECK (commission_pct BETWEEN 0 AND 100),
    credit_limit          NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (credit_limit >= 0),
    credit_used           NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (credit_used >= 0),
    currency              CHAR(3)      NOT NULL DEFAULT 'COP',
    status                VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','SUSPENDED')),
    -- RF-22: respalda la validación de cupo antes de confirmar.
    CONSTRAINT ck_agency_credit CHECK (credit_used <= credit_limit)
);

-- ---------------------------------------------------------------------
-- 5. RESERVA  (DEC-2: reservation -> itinerary -> itinerary_segment)
-- ---------------------------------------------------------------------

CREATE TABLE reservation (
    reservation_id  BIGSERIAL PRIMARY KEY,
    -- RF-13: 6 caracteres, aleatorio y no secuencial (RNF-S2).
    pnr             CHAR(6)      NOT NULL UNIQUE,
    channel         VARCHAR(20)  NOT NULL
        CHECK (channel IN ('WEB','B2B','COUNTER')),
    agency_id       INTEGER      REFERENCES agency(agency_id),
    contact_email   VARCHAR(160) NOT NULL,
    contact_phone   VARCHAR(40),
    status          VARCHAR(20)  NOT NULL
        CHECK (status IN ('HELD','CONFIRMED','CANCELLED','EXPIRED')),
    currency        CHAR(3)      NOT NULL,
    total_amount    NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    -- SUP-7: no nulo únicamente mientras la reserva está en HELD.
    hold_expires_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_reservation_hold
        CHECK ((status = 'HELD' AND hold_expires_at IS NOT NULL)
            OR (status <> 'HELD')),
    CONSTRAINT ck_reservation_agency
        CHECK ((channel = 'B2B' AND agency_id IS NOT NULL)
            OR (channel <> 'B2B' AND agency_id IS NULL))
);
-- Sostiene el job de expiración (RF-11, RNF-C3).
CREATE INDEX ix_reservation_expiry
    ON reservation (hold_expires_at) WHERE status = 'HELD';
CREATE INDEX ix_reservation_agency ON reservation (agency_id) WHERE agency_id IS NOT NULL;

CREATE TABLE reservation_passenger (
    reservation_id BIGINT   NOT NULL REFERENCES reservation(reservation_id) ON DELETE CASCADE,
    -- Identificador LOCAL dentro del PNR (1,2,3...). Permite emitir tiquetes
    -- sin exponer el passenger_id global.
    passenger_ref  SMALLINT NOT NULL,
    passenger_id   BIGINT   NOT NULL REFERENCES passenger(passenger_id),
    passenger_type VARCHAR(3) NOT NULL CHECK (passenger_type IN ('ADT','CHD','INF')),
    PRIMARY KEY (reservation_id, passenger_ref)
);
COMMENT ON COLUMN reservation_passenger.passenger_type IS
  'INF (infante en brazos) no consume inventario: ver cálculo de seats en el servicio de booking.';

CREATE TABLE itinerary (
    itinerary_id     BIGSERIAL PRIMARY KEY,
    reservation_id   BIGINT   NOT NULL REFERENCES reservation(reservation_id) ON DELETE CASCADE,
    sequence_no      SMALLINT NOT NULL,
    origin_iata      CHAR(3)  NOT NULL REFERENCES airport(iata_code),
    destination_iata CHAR(3)  NOT NULL REFERENCES airport(iata_code),
    status           VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','CANCELLED')),
    CONSTRAINT uq_itinerary UNIQUE (reservation_id, sequence_no)
);
COMMENT ON TABLE itinerary IS
  'DEC-2: unidad de CANCELACIÓN y TARIFACIÓN. Origen/destino del viaje completo, no del primer tramo.';

CREATE TABLE itinerary_segment (
    segment_id         BIGSERIAL PRIMARY KEY,
    itinerary_id       BIGINT      NOT NULL REFERENCES itinerary(itinerary_id) ON DELETE CASCADE,
    sequence_no        SMALLINT    NOT NULL,
    flight_instance_id BIGINT      NOT NULL REFERENCES flight_instance(flight_instance_id),
    cabin_code         VARCHAR(10) NOT NULL REFERENCES cabin(cabin_code),
    fare_class_code    CHAR(1)     NOT NULL REFERENCES fare_class(fare_class_code),
    -- DESVIACIÓN DECLARADA D-1 respecto al modelo de 5.3 (ver 5.6.5).
    -- El precio se congela al RETENER, no al emitir: entre el hold y el pago
    -- pueden pasar 20 minutos, y el usuario aceptó un precio concreto.
    -- Sin esto, un cambio de tarifa en esa ventana cambiaría lo que se cobra.
    quoted_fare_id     INTEGER     NOT NULL REFERENCES fare(fare_id),
    quoted_fare_amount NUMERIC(12,2) NOT NULL CHECK (quoted_fare_amount >= 0),
    quoted_tax_amount  NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (quoted_tax_amount >= 0),
    status             VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','CANCELLED')),
    CONSTRAINT uq_segment UNIQUE (itinerary_id, sequence_no),
    -- Se necesita para la FK compuesta desde flight_inventory conceptualmente;
    -- aquí se indexa porque es el join más frecuente del ETL (5.7).
    CONSTRAINT fk_segment_inventory
        FOREIGN KEY (flight_instance_id, cabin_code)
        REFERENCES flight_inventory (flight_instance_id, cabin_code)
);
COMMENT ON TABLE itinerary_segment IS
  'DEC-2: unidad de INVENTARIO. Cada tramo activo consume una silla en su flight_inventory.';
CREATE INDEX ix_segment_flight ON itinerary_segment (flight_instance_id, cabin_code);

-- ---------------------------------------------------------------------
-- 6. EMISIÓN
-- ---------------------------------------------------------------------

CREATE TABLE ticket (
    ticket_id       BIGSERIAL PRIMARY KEY,
    ticket_number   VARCHAR(20) NOT NULL UNIQUE,
    reservation_id  BIGINT      NOT NULL,
    passenger_ref   SMALLINT    NOT NULL,
    segment_id      BIGINT      NOT NULL REFERENCES itinerary_segment(segment_id),
    fare_id         INTEGER     NOT NULL REFERENCES fare(fare_id),
    -- DEC-5: montos CONGELADOS en el momento de la emisión.
    -- Un tiquete es un contrato: no puede cambiar de precio al consultarlo.
    fare_amount      NUMERIC(12,2) NOT NULL CHECK (fare_amount >= 0),
    tax_amount       NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0),
    surcharge_amount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (surcharge_amount >= 0),
    discount_amount  NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    commission_amount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (commission_amount >= 0),
    currency        CHAR(3)     NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'ISSUED'
        CHECK (status IN ('ISSUED','USED','CANCELLED','REFUNDED')),
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at    TIMESTAMPTZ,
    FOREIGN KEY (reservation_id, passenger_ref)
        REFERENCES reservation_passenger (reservation_id, passenger_ref),
    CONSTRAINT uq_ticket_pax_segment UNIQUE (segment_id, reservation_id, passenger_ref)
);
COMMENT ON TABLE ticket IS
  'Grano del hecho analítico de 5.7: un tiquete por (pasajero x tramo). RF-14.';
CREATE INDEX ix_ticket_reservation ON ticket (reservation_id);
-- Sostiene la extracción incremental del ETL (DEC-11).
CREATE INDEX ix_ticket_issued_at ON ticket (issued_at);

CREATE TABLE seat_assignment (
    ticket_id          BIGINT      PRIMARY KEY REFERENCES ticket(ticket_id) ON DELETE CASCADE,
    flight_instance_id BIGINT      NOT NULL REFERENCES flight_instance(flight_instance_id),
    seat_number        VARCHAR(4)  NOT NULL,
    assigned_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_by        VARCHAR(40) NOT NULL DEFAULT 'PASSENGER',
    price              NUMERIC(12,2) NOT NULL DEFAULT 0,
    -- Integridad de silla, NO control de capacidad (DEC-3).
    CONSTRAINT uq_seat_per_flight UNIQUE (flight_instance_id, seat_number)
);
COMMENT ON TABLE seat_assignment IS
  'DEC-3/SUP-11: la silla física NO participa en el control de sobreventa.';

CREATE TABLE ancillary (
    ancillary_id   BIGSERIAL PRIMARY KEY,
    ticket_id      BIGINT      NOT NULL REFERENCES ticket(ticket_id) ON DELETE CASCADE,
    ancillary_type VARCHAR(20) NOT NULL
        CHECK (ancillary_type IN ('BAGGAGE','SEAT','INSURANCE','MEAL')),
    description    VARCHAR(120),
    amount         NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    currency       CHAR(3)     NOT NULL,
    status         VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','CANCELLED','REFUNDED'))
);

-- ---------------------------------------------------------------------
-- 7. PAGOS  (DEC-5: SIN datos de tarjeta, RNF-S1)
-- ---------------------------------------------------------------------

CREATE TABLE payment (
    payment_id      BIGSERIAL PRIMARY KEY,
    reservation_id  BIGINT      NOT NULL REFERENCES reservation(reservation_id),
    method          VARCHAR(20) NOT NULL CHECK (method IN ('CARD','AGENCY_CREDIT')),
    psp_provider    VARCHAR(40),
    -- Referencia opaca del proveedor. NUNCA el PAN (RNF-S1, SUP-14).
    psp_intent_id   VARCHAR(120) UNIQUE,
    amount          NUMERIC(14,2) NOT NULL CHECK (amount >= 0),
    currency        CHAR(3)     NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','AUTHORIZED','CAPTURED','FAILED','REFUNDED')),
    -- Impide el doble cobro por reintento del cliente.
    idempotency_key VARCHAR(80) NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payment_event (
    payment_event_id BIGSERIAL PRIMARY KEY,
    payment_id       BIGINT      NOT NULL REFERENCES payment(payment_id),
    -- RNF-C4: la unicidad es lo que hace idempotente el webhook.
    -- Un evento reentregado choca contra este índice y se descarta.
    psp_event_id     VARCHAR(120) NOT NULL UNIQUE,
    event_type       VARCHAR(60) NOT NULL,
    payload          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE refund (
    refund_id  BIGSERIAL PRIMARY KEY,
    payment_id BIGINT      NOT NULL REFERENCES payment(payment_id),
    amount     NUMERIC(14,2) NOT NULL CHECK (amount >= 0),
    reason     VARCHAR(120),
    status     VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','COMPLETED','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 8. AUDITORÍA  (DEC-10, RNF-A1/A2) — SOLO INSERCIÓN
-- ---------------------------------------------------------------------

CREATE TABLE reservation_event (
    event_id       BIGSERIAL PRIMARY KEY,
    reservation_id BIGINT      NOT NULL REFERENCES reservation(reservation_id),
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_type     VARCHAR(20) NOT NULL
        CHECK (actor_type IN ('PASSENGER','AGENT','STAFF','ADMIN','SYSTEM','PSP')),
    actor_id       VARCHAR(80),
    channel        VARCHAR(20) NOT NULL,
    event_type     VARCHAR(40) NOT NULL,
    from_status    VARCHAR(20),
    to_status      VARCHAR(20),
    payload        JSONB       NOT NULL DEFAULT '{}'::jsonb
);
COMMENT ON TABLE reservation_event IS
  'RNF-A1: log inmutable. Se escribe en la MISMA transacción que la operación. '
  'RNF-A2: UPDATE/DELETE revocados incluso para el rol de aplicación. '
  'Es fuente del pipeline analítico: sin él no hay patrones de cancelación (5.7).';
CREATE INDEX ix_event_reservation ON reservation_event (reservation_id, occurred_at);
CREATE INDEX ix_event_occurred ON reservation_event (occurred_at);

-- RNF-A2: hacer el log realmente inmutable a nivel de motor.
CREATE OR REPLACE FUNCTION airline.deny_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'reservation_event es de solo inserción (RNF-A2)';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_reservation_event_immutable
    BEFORE UPDATE OR DELETE ON reservation_event
    FOR EACH ROW EXECUTE FUNCTION airline.deny_mutation();

-- ---------------------------------------------------------------------
-- 9. VISTAS DE APOYO
-- ---------------------------------------------------------------------

-- Disponibilidad expuesta a la búsqueda (RF-01). Lectura sin bloqueo:
-- la verdad se verifica al retener, no al buscar (DEC-8).
CREATE VIEW v_flight_availability AS
SELECT
    fi.flight_instance_id,
    fi.flight_date,
    fi.departure_utc,
    fi.arrival_utc,
    fi.status                AS flight_status,
    sf.flight_number,
    r.route_id,
    r.origin_iata,
    r.destination_iata,
    inv.cabin_code,
    inv.capacity,
    inv.seats_sold,
    inv.seats_held,
    FLOOR(inv.capacity * inv.oversell_factor)
        - inv.seats_sold - inv.seats_held  AS seats_available
FROM flight_instance  fi
JOIN scheduled_flight sf  ON sf.scheduled_flight_id = fi.scheduled_flight_id
JOIN route            r   ON r.route_id = sf.route_id
JOIN flight_inventory inv ON inv.flight_instance_id = fi.flight_instance_id
WHERE fi.status <> 'CANCELLED';

-- Capacidad por cabina derivada del mapa de sillas (usada al materializar, RF-25).
CREATE VIEW v_seat_map_capacity AS
SELECT seat_map_id, cabin_code, COUNT(*)::SMALLINT AS capacity
FROM seat_map_seat
GROUP BY seat_map_id, cabin_code;

-- Consulta de verificación de la invariante de RNF-C1.
-- Debe devolver CERO filas siempre. La usa la prueba de concurrencia de 5.6.
CREATE VIEW v_oversell_check AS
SELECT
    inv.flight_instance_id,
    inv.cabin_code,
    inv.capacity,
    inv.seats_sold,
    inv.seats_held,
    inv.seats_sold + inv.seats_held AS committed
FROM flight_inventory inv
WHERE inv.seats_sold + inv.seats_held > FLOOR(inv.capacity * inv.oversell_factor);
COMMENT ON VIEW v_oversell_check IS
  'RNF-C1: debe devolver 0 filas SIEMPRE. Es la aserción de la prueba de concurrencia.';

-- Contraste entre el contador materializado y los tramos reales.
-- Detecta desviaciones entre flight_inventory y itinerary_segment (5.3.2).
CREATE VIEW v_inventory_reconciliation AS
SELECT
    inv.flight_instance_id,
    inv.cabin_code,
    inv.seats_sold,
    inv.seats_held,
    COALESCE(agg.confirmed_segments, 0) AS actual_confirmed,
    COALESCE(agg.held_segments, 0)      AS actual_held
FROM flight_inventory inv
LEFT JOIN (
    -- Un tramo consume tantas sillas como pasajeros que ocupan silla
    -- (los INF viajan en brazos y no consumen inventario).
    SELECT s.flight_instance_id,
           s.cabin_code,
           SUM(pax.seats) FILTER (WHERE r.status = 'CONFIRMED') AS confirmed_segments,
           SUM(pax.seats) FILTER (WHERE r.status = 'HELD')      AS held_segments
    FROM itinerary_segment s
    JOIN itinerary   i ON i.itinerary_id = s.itinerary_id
    JOIN reservation r ON r.reservation_id = i.reservation_id
    JOIN LATERAL (
        SELECT COUNT(*) AS seats
        FROM reservation_passenger rp
        WHERE rp.reservation_id = r.reservation_id
          AND rp.passenger_type <> 'INF'
    ) pax ON TRUE
    WHERE s.status = 'ACTIVE'
    GROUP BY s.flight_instance_id, s.cabin_code
) agg ON agg.flight_instance_id = inv.flight_instance_id
     AND agg.cabin_code = inv.cabin_code;
COMMENT ON VIEW v_inventory_reconciliation IS
  'Contrasta el contador materializado (5.3.2) con los tramos reales. '
  'Cualquier diferencia indica un bug en el servicio de inventario.';
