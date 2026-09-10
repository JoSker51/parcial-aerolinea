-- =====================================================================
--  Base analítica (OLAP) — sección 5.7
--  PostgreSQL 16 · Esquema en ESTRELLA
--
--  Deliberadamente distinto del transaccional. Justificación en 5.7.2:
--  el OLTP está en 3FN para escribir con integridad; este modelo está
--  desnormalizado para leer agregando. Un solo modelo para ambos fines
--  queda mal normalizado para transaccionar y mal desnormalizado para
--  analizar.
--
--  Responde las tres preguntas de la gerencia comercial del enunciado:
--    1. Ocupación por ruta       -> fact_flight_occupancy
--    2. Ingresos por tarifa      -> fact_ticket_sale
--    3. Patrones de cancelación  -> fact_reservation_lifecycle
-- =====================================================================

DROP SCHEMA IF EXISTS analytics CASCADE;
CREATE SCHEMA analytics;
SET search_path TO analytics, public;

-- ---------------------------------------------------------------------
-- DIMENSIONES
-- ---------------------------------------------------------------------

CREATE TABLE dim_date (
    date_key     INTEGER PRIMARY KEY,          -- AAAAMMDD
    full_date    DATE    NOT NULL UNIQUE,
    year         SMALLINT NOT NULL,
    quarter      SMALLINT NOT NULL,
    month        SMALLINT NOT NULL,
    month_name   VARCHAR(20) NOT NULL,
    day          SMALLINT NOT NULL,
    day_of_week  SMALLINT NOT NULL,
    day_name     VARCHAR(20) NOT NULL,
    is_weekend   BOOLEAN NOT NULL,
    -- SUP-4: la temporada alta es la ventana de negocio que concentra el
    -- ingreso; marcarla en la dimensión evita repetir la definición en
    -- cada consulta de BI.
    is_high_season BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE dim_route (
    route_key        INTEGER PRIMARY KEY,
    origin_iata      CHAR(3)     NOT NULL,
    origin_city      VARCHAR(80) NOT NULL,
    destination_iata CHAR(3)     NOT NULL,
    destination_city VARCHAR(80) NOT NULL,
    route_label      VARCHAR(20) NOT NULL,     -- 'BOG-MDE'
    distance_km      INTEGER     NOT NULL,
    distance_band    VARCHAR(20) NOT NULL      -- corta / media / larga
);

CREATE TABLE dim_fare (
    fare_key         INTEGER PRIMARY KEY,
    fare_class_code  CHAR(1)     NOT NULL,
    fare_class_name  VARCHAR(40) NOT NULL,
    cabin_code       VARCHAR(10) NOT NULL,
    cabin_name       VARCHAR(40) NOT NULL,
    rule_name        VARCHAR(60) NOT NULL,
    min_advance_days SMALLINT    NOT NULL,
    is_refundable    BOOLEAN     NOT NULL,
    is_changeable    BOOLEAN     NOT NULL
);

CREATE TABLE dim_flight (
    flight_key       BIGINT PRIMARY KEY,       -- = flight_instance_id del OLTP
    flight_number    VARCHAR(8) NOT NULL,
    route_key        INTEGER    NOT NULL REFERENCES dim_route(route_key),
    departure_date_key INTEGER  NOT NULL REFERENCES dim_date(date_key),
    departure_utc    TIMESTAMPTZ NOT NULL,
    aircraft_model   VARCHAR(20),
    aircraft_registration VARCHAR(10),
    flight_status    VARCHAR(20) NOT NULL
);

CREATE TABLE dim_channel (
    channel_key  SERIAL PRIMARY KEY,
    channel      VARCHAR(20) NOT NULL,
    agency_name  VARCHAR(140),
    is_indirect  BOOLEAN NOT NULL,
    CONSTRAINT uq_dim_channel UNIQUE (channel, agency_name)
);

-- ---------------------------------------------------------------------
-- HECHOS
-- ---------------------------------------------------------------------

-- Grano: UN TIQUETE = un pasajero en un tramo.
-- Es el grano más fino con valor monetario del OLTP (DEC-11). Desde aquí se
-- agrega a ruta, tarifa, vuelo o fecha; al revés no se puede.
CREATE TABLE fact_ticket_sale (
    ticket_key       BIGINT PRIMARY KEY,       -- = ticket_id del OLTP
    ticket_number    VARCHAR(20) NOT NULL,
    flight_key       BIGINT  NOT NULL REFERENCES dim_flight(flight_key),
    route_key        INTEGER NOT NULL REFERENCES dim_route(route_key),
    fare_key         INTEGER NOT NULL REFERENCES dim_fare(fare_key),
    channel_key      INTEGER NOT NULL REFERENCES dim_channel(channel_key),
    issue_date_key   INTEGER NOT NULL REFERENCES dim_date(date_key),
    departure_date_key INTEGER NOT NULL REFERENCES dim_date(date_key),

    -- Métricas aditivas
    fare_amount      NUMERIC(12,2) NOT NULL,
    tax_amount       NUMERIC(12,2) NOT NULL,
    discount_amount  NUMERIC(12,2) NOT NULL,
    commission_amount NUMERIC(12,2) NOT NULL,
    ancillary_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_amount     NUMERIC(12,2) NOT NULL,
    seats            SMALLINT      NOT NULL DEFAULT 1,

    -- Métrica derivada de negocio: días de anticipación de la compra.
    -- Se precalcula en el ETL porque es el eje de la pregunta "ingresos por
    -- tarifa según anticipación" y calcularla al vuelo obliga a un join con
    -- dim_date en cada consulta.
    days_before_departure SMALLINT,

    currency         CHAR(3)     NOT NULL,
    ticket_status    VARCHAR(20) NOT NULL,
    is_cancelled     BOOLEAN     NOT NULL DEFAULT FALSE,
    reservation_pnr  CHAR(6)     NOT NULL,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_fts_route_date ON fact_ticket_sale (route_key, departure_date_key);
CREATE INDEX ix_fts_fare       ON fact_ticket_sale (fare_key);
CREATE INDEX ix_fts_issue      ON fact_ticket_sale (issue_date_key);

-- Grano: UNA CABINA DE UN VUELO. Responde "ocupación por ruta".
CREATE TABLE fact_flight_occupancy (
    flight_key       BIGINT      NOT NULL REFERENCES dim_flight(flight_key),
    cabin_code       VARCHAR(10) NOT NULL,
    route_key        INTEGER     NOT NULL REFERENCES dim_route(route_key),
    departure_date_key INTEGER   NOT NULL REFERENCES dim_date(date_key),
    capacity         SMALLINT    NOT NULL,
    seats_sold       SMALLINT    NOT NULL,
    seats_held       SMALLINT    NOT NULL,
    -- Semiaditiva: se promedia por ruta, NO se suma. Se materializa para que
    -- el BI no la recalcule mal.
    load_factor      NUMERIC(5,4) NOT NULL,
    revenue_amount   NUMERIC(14,2) NOT NULL DEFAULT 0,
    -- Ingreso por silla disponible: la métrica estándar del sector para
    -- comparar rutas de distinta capacidad.
    revenue_per_seat NUMERIC(12,2) NOT NULL DEFAULT 0,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (flight_key, cabin_code)
);
CREATE INDEX ix_ffo_route_date ON fact_flight_occupancy (route_key, departure_date_key);

-- Grano: UNA RESERVA. Responde "patrones de cancelación".
-- Se alimenta de `reservation_event` (DEC-10): sin el log de auditoría estas
-- métricas no existen, porque una reserva cancelada que solo guarda su estado
-- final no dice ni cuándo se canceló ni cuánto tiempo estuvo viva.
CREATE TABLE fact_reservation_lifecycle (
    reservation_key   BIGINT PRIMARY KEY,
    pnr               CHAR(6)  NOT NULL,
    channel_key       INTEGER  NOT NULL REFERENCES dim_channel(channel_key),
    route_key         INTEGER  REFERENCES dim_route(route_key),
    created_date_key  INTEGER  NOT NULL REFERENCES dim_date(date_key),
    departure_date_key INTEGER REFERENCES dim_date(date_key),
    final_status      VARCHAR(20) NOT NULL,
    passengers        SMALLINT NOT NULL,
    segments          SMALLINT NOT NULL,
    total_amount      NUMERIC(14,2) NOT NULL,
    currency          CHAR(3)  NOT NULL,

    -- Métricas de ciclo de vida
    minutes_to_confirm    INTEGER,  -- HELD -> CONFIRMED
    minutes_to_cancel     INTEGER,  -- creación -> cancelación
    days_before_departure_at_cancel INTEGER,
    was_held              BOOLEAN NOT NULL DEFAULT FALSE,
    was_confirmed         BOOLEAN NOT NULL DEFAULT FALSE,
    was_cancelled         BOOLEAN NOT NULL DEFAULT FALSE,
    expired_without_payment BOOLEAN NOT NULL DEFAULT FALSE,
    event_count           SMALLINT NOT NULL DEFAULT 0,
    loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_frl_status ON fact_reservation_lifecycle (final_status);
CREATE INDEX ix_frl_created ON fact_reservation_lifecycle (created_date_key);

-- ---------------------------------------------------------------------
-- CONTROL DEL ETL
-- ---------------------------------------------------------------------

CREATE TABLE etl_watermark (
    table_name     VARCHAR(60) PRIMARY KEY,
    last_loaded_at TIMESTAMPTZ NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE etl_watermark IS
  'Marca de agua de la extracción incremental (DEC-11). Un escaneo completo '
  'diario de `ticket` crecería sin límite y encarecería el ETL de forma lineal '
  'con la historia acumulada.';

CREATE TABLE etl_run_log (
    run_id       BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ,
    status       VARCHAR(20) NOT NULL,
    rows_read    INTEGER NOT NULL DEFAULT 0,
    rows_written INTEGER NOT NULL DEFAULT 0,
    detail       JSONB   NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------
-- VISTAS DE NEGOCIO — las tres preguntas del enunciado
-- ---------------------------------------------------------------------

-- 1. Ocupación por ruta.
CREATE VIEW v_occupancy_by_route AS
SELECT
    r.route_label,
    r.origin_city,
    r.destination_city,
    d.year,
    d.month,
    d.is_high_season,
    o.cabin_code,
    COUNT(*)                        AS flights,
    SUM(o.capacity)                 AS seats_offered,
    SUM(o.seats_sold)               AS seats_sold,
    ROUND(AVG(o.load_factor) * 100, 2) AS avg_load_factor_pct,
    SUM(o.revenue_amount)           AS revenue
FROM fact_flight_occupancy o
JOIN dim_route r ON r.route_key = o.route_key
JOIN dim_date  d ON d.date_key = o.departure_date_key
GROUP BY r.route_label, r.origin_city, r.destination_city,
         d.year, d.month, d.is_high_season, o.cabin_code;

-- 2. Ingresos por tarifa, con el eje de anticipación de compra.
CREATE VIEW v_revenue_by_fare AS
SELECT
    f.cabin_name,
    f.fare_class_code,
    f.fare_class_name,
    f.rule_name,
    f.min_advance_days,
    d.year,
    d.month,
    COUNT(*)                        AS tickets,
    SUM(t.seats)                    AS seats,
    SUM(t.fare_amount)              AS fare_revenue,
    SUM(t.tax_amount)               AS tax_collected,
    SUM(t.ancillary_amount)         AS ancillary_revenue,
    SUM(t.commission_amount)        AS commissions_paid,
    SUM(t.total_amount)             AS total_revenue,
    ROUND(AVG(t.fare_amount), 2)    AS avg_fare,
    ROUND(AVG(t.days_before_departure), 1) AS avg_days_before_departure
FROM fact_ticket_sale t
JOIN dim_fare f ON f.fare_key = t.fare_key
JOIN dim_date d ON d.date_key = t.issue_date_key
WHERE t.is_cancelled = FALSE
GROUP BY f.cabin_name, f.fare_class_code, f.fare_class_name, f.rule_name,
         f.min_advance_days, d.year, d.month;

-- 3. Patrones de cancelación.
CREATE VIEW v_cancellation_patterns AS
SELECT
    c.channel,
    c.agency_name,
    r.route_label,
    d.year,
    d.month,
    COUNT(*)                                            AS reservations,
    COUNT(*) FILTER (WHERE l.was_cancelled)             AS cancelled,
    COUNT(*) FILTER (WHERE l.expired_without_payment)   AS expired_unpaid,
    ROUND(100.0 * COUNT(*) FILTER (WHERE l.was_cancelled)
          / NULLIF(COUNT(*), 0), 2)                     AS cancellation_rate_pct,
    ROUND(AVG(l.minutes_to_confirm) FILTER (WHERE l.was_confirmed), 1)
                                                        AS avg_minutes_to_confirm,
    ROUND(AVG(l.days_before_departure_at_cancel)
          FILTER (WHERE l.was_cancelled), 1)            AS avg_days_before_dep_at_cancel,
    SUM(l.total_amount) FILTER (WHERE l.was_cancelled)  AS cancelled_value
FROM fact_reservation_lifecycle l
JOIN dim_channel c ON c.channel_key = l.channel_key
LEFT JOIN dim_route r ON r.route_key = l.route_key
JOIN dim_date  d ON d.date_key = l.created_date_key
GROUP BY c.channel, c.agency_name, r.route_label, d.year, d.month;
