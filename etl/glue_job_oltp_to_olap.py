#!/usr/bin/env python
"""ETL OLTP -> OLAP — sección 5.7.

Alimenta el esquema en estrella de `analytics` desde el transaccional
`airline`, respondiendo las tres preguntas de la gerencia comercial:
ocupación por ruta, ingresos por tarifa y patrones de cancelación.

Diseñado como **AWS Glue Python Shell job** (0.0625 DPU). El mismo archivo
corre sin cambios en local contra los dos contenedores de `docker-compose`,
lo que permite desarrollar y probar el ETL sin consumir crédito del Learner
Lab. En Glue los parámetros llegan por `--KEY value` y las credenciales por
Secrets Manager; en local, por variables de entorno.

Decisiones documentadas en 5.7.2:

* **Se transforma, no se copia tal cual.** Copiar `ticket` a la analítica
  reproduciría el problema del OLTP: consultas de negocio con seis joins.
  El ETL desnormaliza a estrella y precalcula las métricas de negocio
  (`load_factor`, `days_before_departure`, tiempos de ciclo de vida).
* **Extracción incremental** por marca de agua (`etl_watermark`). Un escaneo
  completo diario crecería de forma lineal con la historia acumulada y
  encarecería el job para siempre.
* **Lectura desde la réplica**, nunca desde el primario (DEC-11, RNF-P4).
* **Idempotente**: todas las escrituras son `INSERT … ON CONFLICT DO UPDATE`,
  así que reejecutar el job tras un fallo no duplica filas.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg

# ---------------------------------------------------------------------
# Parámetros: Glue (--KEY value) o entorno
# ---------------------------------------------------------------------

def _resolve_args() -> dict[str, str]:
    args: dict[str, str] = {}
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            args[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return args


_ARGS = _resolve_args()


def param(name: str, default: str | None = None) -> str:
    value = _ARGS.get(name) or os.getenv(name.upper().replace("-", "_")) or default
    if value is None:
        raise SystemExit(f"Falta el parámetro requerido: --{name} / {name.upper()}")
    return value


OLTP_DSN = param("oltp_dsn", "postgresql://airline:airline@localhost:5433/airline")
OLAP_DSN = param("olap_dsn", "postgresql://analytics:analytics@localhost:5434/analytics")
# Ventana de reproceso: se reextrae un margen hacia atrás de la marca de agua
# para no perder filas escritas por transacciones que estaban abiertas cuando
# la corrida anterior tomó su instantánea.
LOOKBACK_MINUTES = int(param("lookback_minutes", "60"))
FULL_REFRESH = param("full_refresh", "false").lower() == "true"

HIGH_SEASON_MONTHS = {1, 6, 7, 12}  # SUP-4: temporada alta


@contextmanager
def connect(dsn: str, search_path: str):
    conn = psycopg.connect(dsn, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {search_path}, public")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {message}", flush=True)


# ---------------------------------------------------------------------
# Marca de agua
# ---------------------------------------------------------------------

def get_watermark(olap: psycopg.Connection, table: str) -> datetime:
    if FULL_REFRESH:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    with olap.cursor() as cur:
        cur.execute(
            "SELECT last_loaded_at FROM etl_watermark WHERE table_name = %s", (table,)
        )
        row = cur.fetchone()
    if row is None:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    return row[0] - timedelta(minutes=LOOKBACK_MINUTES)


def set_watermark(olap: psycopg.Connection, table: str, value: datetime) -> None:
    with olap.cursor() as cur:
        cur.execute(
            """INSERT INTO etl_watermark (table_name, last_loaded_at)
               VALUES (%s, %s)
               ON CONFLICT (table_name) DO UPDATE
                   SET last_loaded_at = EXCLUDED.last_loaded_at,
                       updated_at = now()""",
            (table, value),
        )


# ---------------------------------------------------------------------
# Dimensiones
# ---------------------------------------------------------------------

def load_dim_date(olap: psycopg.Connection) -> int:
    """Dimensión de fechas. Se genera, no se extrae: es un calendario."""
    rows = []
    start = datetime(2026, 1, 1).date()
    for offset in range(365 * 3):
        d = start + timedelta(days=offset)
        rows.append((
            int(d.strftime("%Y%m%d")), d, d.year, (d.month - 1) // 3 + 1, d.month,
            d.strftime("%B"), d.day, d.isoweekday(), d.strftime("%A"),
            d.isoweekday() >= 6, d.month in HIGH_SEASON_MONTHS,
        ))
    with olap.cursor() as cur:
        cur.executemany(
            """INSERT INTO dim_date (date_key, full_date, year, quarter, month,
                    month_name, day, day_of_week, day_name, is_weekend, is_high_season)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (date_key) DO NOTHING""",
            rows,
        )
    return len(rows)


def load_dim_route(oltp: psycopg.Connection, olap: psycopg.Connection) -> int:
    with oltp.cursor() as cur:
        cur.execute("""
            SELECT r.route_id, r.origin_iata, ao.city, r.destination_iata, ad.city,
                   r.origin_iata || '-' || r.destination_iata, r.distance_km,
                   CASE WHEN r.distance_km < 400 THEN 'corta'
                        WHEN r.distance_km < 900 THEN 'media'
                        ELSE 'larga' END
              FROM route r
              JOIN airport ao ON ao.iata_code = r.origin_iata
              JOIN airport ad ON ad.iata_code = r.destination_iata
        """)
        rows = cur.fetchall()
    with olap.cursor() as cur:
        cur.executemany(
            """INSERT INTO dim_route (route_key, origin_iata, origin_city,
                    destination_iata, destination_city, route_label, distance_km,
                    distance_band)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (route_key) DO UPDATE
                   SET distance_km = EXCLUDED.distance_km,
                       distance_band = EXCLUDED.distance_band""",
            rows,
        )
    return len(rows)


def load_dim_fare(oltp: psycopg.Connection, olap: psycopg.Connection) -> int:
    with oltp.cursor() as cur:
        cur.execute("""
            SELECT f.fare_id, fc.fare_class_code, fc.name, c.cabin_code, c.name,
                   fr.name, fr.min_advance_days, fr.is_refundable, fr.is_changeable
              FROM fare f
              JOIN fare_class fc ON fc.fare_class_code = f.fare_class_code
              JOIN cabin      c  ON c.cabin_code = fc.cabin_code
              JOIN fare_rule  fr ON fr.fare_rule_id = f.fare_rule_id
        """)
        rows = cur.fetchall()
    with olap.cursor() as cur:
        cur.executemany(
            """INSERT INTO dim_fare (fare_key, fare_class_code, fare_class_name,
                    cabin_code, cabin_name, rule_name, min_advance_days,
                    is_refundable, is_changeable)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (fare_key) DO UPDATE
                   SET rule_name = EXCLUDED.rule_name,
                       min_advance_days = EXCLUDED.min_advance_days""",
            rows,
        )
    return len(rows)


def load_dim_flight(oltp: psycopg.Connection, olap: psycopg.Connection) -> int:
    with oltp.cursor() as cur:
        cur.execute("""
            SELECT fi.flight_instance_id, sf.flight_number, sf.route_id,
                   CAST(to_char(fi.flight_date, 'YYYYMMDD') AS INTEGER),
                   fi.departure_utc, am.model_code, a.registration, fi.status
              FROM flight_instance  fi
              JOIN scheduled_flight sf ON sf.scheduled_flight_id = fi.scheduled_flight_id
              LEFT JOIN aircraft       a  ON a.aircraft_id = fi.aircraft_id
              LEFT JOIN aircraft_model am ON am.model_code = a.model_code
             WHERE fi.flight_date >= CURRENT_DATE - 365
        """)
        rows = cur.fetchall()
    with olap.cursor() as cur:
        cur.executemany(
            """INSERT INTO dim_flight (flight_key, flight_number, route_key,
                    departure_date_key, departure_utc, aircraft_model,
                    aircraft_registration, flight_status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (flight_key) DO UPDATE
                   SET flight_status = EXCLUDED.flight_status,
                       aircraft_registration = EXCLUDED.aircraft_registration""",
            rows,
        )
    return len(rows)


def load_dim_channel(oltp: psycopg.Connection, olap: psycopg.Connection) -> int:
    with oltp.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT r.channel, a.legal_name
              FROM reservation r
              LEFT JOIN agency a ON a.agency_id = r.agency_id
        """)
        rows = [(ch, name, ch == "B2B") for ch, name in cur.fetchall()]
    if not rows:
        rows = [("WEB", None, False)]
    with olap.cursor() as cur:
        cur.executemany(
            """INSERT INTO dim_channel (channel, agency_name, is_indirect)
               VALUES (%s,%s,%s)
               ON CONFLICT (channel, agency_name) DO NOTHING""",
            rows,
        )
    return len(rows)


def channel_map(olap: psycopg.Connection) -> dict[tuple[str, str | None], int]:
    with olap.cursor() as cur:
        cur.execute("SELECT channel_key, channel, agency_name FROM dim_channel")
        return {(ch, name): key for key, ch, name in cur.fetchall()}


# ---------------------------------------------------------------------
# Hechos
# ---------------------------------------------------------------------

def load_fact_ticket_sale(
    oltp: psycopg.Connection, olap: psycopg.Connection,
    watermark: datetime, channels: dict,
) -> tuple[int, datetime]:
    """Ingresos por tarifa. Grano: un tiquete (pasajero x tramo)."""
    with oltp.cursor() as cur:
        cur.execute(
            """
            SELECT t.ticket_id, t.ticket_number, s.flight_instance_id, sf.route_id,
                   t.fare_id, r.channel, ag.legal_name,
                   CAST(to_char(t.issued_at, 'YYYYMMDD') AS INTEGER),
                   CAST(to_char(fi.flight_date, 'YYYYMMDD') AS INTEGER),
                   t.fare_amount, t.tax_amount, t.discount_amount, t.commission_amount,
                   COALESCE(anc.total, 0),
                   t.fare_amount + t.tax_amount + COALESCE(anc.total, 0),
                   -- Métrica de negocio precalculada: anticipación de compra.
                   (fi.flight_date - t.issued_at::date) AS days_before,
                   t.currency, t.status, (t.status = 'CANCELLED'), r.pnr, t.issued_at
              FROM ticket t
              JOIN itinerary_segment s ON s.segment_id = t.segment_id
              JOIN itinerary       i   ON i.itinerary_id = s.itinerary_id
              JOIN reservation     r   ON r.reservation_id = t.reservation_id
              LEFT JOIN agency     ag  ON ag.agency_id = r.agency_id
              JOIN flight_instance fi  ON fi.flight_instance_id = s.flight_instance_id
              JOIN scheduled_flight sf ON sf.scheduled_flight_id = fi.scheduled_flight_id
              LEFT JOIN LATERAL (
                    SELECT SUM(a.amount) AS total FROM ancillary a
                     WHERE a.ticket_id = t.ticket_id AND a.status = 'ACTIVE'
              ) anc ON TRUE
             WHERE t.issued_at > %s
             ORDER BY t.issued_at
            """,
            (watermark,),
        )
        rows = cur.fetchall()

    if not rows:
        return 0, watermark

    payload = []
    max_ts = watermark
    for r in rows:
        (tid, tno, fkey, rkey, fare_id, channel, agency, issue_dk, dep_dk,
         fare, tax, disc, comm, anc, total, days_before, cur_code, status,
         cancelled, pnr, issued_at) = r
        payload.append((
            tid, tno, fkey, rkey, fare_id,
            channels.get((channel, agency)) or channels.get((channel, None)),
            issue_dk, dep_dk, fare, tax, disc, comm, anc, total, 1,
            days_before, cur_code, status, cancelled, pnr,
        ))
        max_ts = max(max_ts, issued_at)

    with olap.cursor() as cur:
        cur.executemany(
            """INSERT INTO fact_ticket_sale (ticket_key, ticket_number, flight_key,
                    route_key, fare_key, channel_key, issue_date_key,
                    departure_date_key, fare_amount, tax_amount, discount_amount,
                    commission_amount, ancillary_amount, total_amount, seats,
                    days_before_departure, currency, ticket_status, is_cancelled,
                    reservation_pnr)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (ticket_key) DO UPDATE
                   SET ticket_status = EXCLUDED.ticket_status,
                       is_cancelled  = EXCLUDED.is_cancelled,
                       ancillary_amount = EXCLUDED.ancillary_amount,
                       total_amount  = EXCLUDED.total_amount,
                       loaded_at     = now()""",
            payload,
        )
    return len(payload), max_ts


def load_fact_flight_occupancy(
    oltp: psycopg.Connection, olap: psycopg.Connection
) -> int:
    """Ocupación por ruta. Grano: una cabina de un vuelo.

    Se agrega en el ORIGEN (no se copia fila a fila y se agrega después):
    el volumen que viaja por la red se reduce, que es donde está el costo
    del ETL en Glue.
    """
    with oltp.cursor() as cur:
        cur.execute("""
            SELECT inv.flight_instance_id, inv.cabin_code, sf.route_id,
                   CAST(to_char(fi.flight_date, 'YYYYMMDD') AS INTEGER),
                   inv.capacity, inv.seats_sold, inv.seats_held,
                   CASE WHEN inv.capacity = 0 THEN 0
                        ELSE ROUND(inv.seats_sold::numeric / inv.capacity, 4) END,
                   COALESCE(rev.revenue, 0),
                   CASE WHEN inv.capacity = 0 THEN 0
                        ELSE ROUND(COALESCE(rev.revenue, 0) / inv.capacity, 2) END
              FROM flight_inventory inv
              JOIN flight_instance  fi ON fi.flight_instance_id = inv.flight_instance_id
              JOIN scheduled_flight sf ON sf.scheduled_flight_id = fi.scheduled_flight_id
              LEFT JOIN LATERAL (
                    SELECT SUM(t.fare_amount + t.tax_amount) AS revenue
                      FROM ticket t
                      JOIN itinerary_segment s ON s.segment_id = t.segment_id
                     WHERE s.flight_instance_id = inv.flight_instance_id
                       AND s.cabin_code = inv.cabin_code
                       AND t.status <> 'CANCELLED'
              ) rev ON TRUE
             WHERE fi.flight_date BETWEEN CURRENT_DATE - 365 AND CURRENT_DATE + 365
        """)
        rows = cur.fetchall()

    with olap.cursor() as cur:
        cur.executemany(
            """INSERT INTO fact_flight_occupancy (flight_key, cabin_code, route_key,
                    departure_date_key, capacity, seats_sold, seats_held,
                    load_factor, revenue_amount, revenue_per_seat)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (flight_key, cabin_code) DO UPDATE
                   SET capacity = EXCLUDED.capacity,
                       seats_sold = EXCLUDED.seats_sold,
                       seats_held = EXCLUDED.seats_held,
                       load_factor = EXCLUDED.load_factor,
                       revenue_amount = EXCLUDED.revenue_amount,
                       revenue_per_seat = EXCLUDED.revenue_per_seat,
                       loaded_at = now()""",
            rows,
        )
    return len(rows)


def load_fact_reservation_lifecycle(
    oltp: psycopg.Connection, olap: psycopg.Connection, channels: dict
) -> int:
    """Patrones de cancelación. Grano: una reserva.

    Se construye desde `reservation_event` (DEC-10). Sin ese log de auditoría
    estas métricas no existirían: una reserva cancelada que solo guarda su
    estado final no dice cuándo se canceló ni cuánto tiempo estuvo viva.
    """
    with oltp.cursor() as cur:
        cur.execute("""
            WITH events AS (
                SELECT reservation_id,
                       MIN(occurred_at) FILTER (WHERE to_status = 'HELD')      AS held_at,
                       MIN(occurred_at) FILTER (WHERE to_status = 'CONFIRMED') AS confirmed_at,
                       MIN(occurred_at) FILTER (WHERE to_status = 'CANCELLED') AS cancelled_at,
                       MIN(occurred_at) FILTER (WHERE to_status = 'EXPIRED')   AS expired_at,
                       COUNT(*) AS event_count
                  FROM reservation_event
                 GROUP BY reservation_id
            ),
            trip AS (
                SELECT i.reservation_id,
                       MIN(sf.route_id) AS route_id,
                       MIN(fi.flight_date) AS first_departure,
                       COUNT(s.segment_id) AS segments
                  FROM itinerary i
                  JOIN itinerary_segment s ON s.itinerary_id = i.itinerary_id
                  JOIN flight_instance  fi ON fi.flight_instance_id = s.flight_instance_id
                  JOIN scheduled_flight sf ON sf.scheduled_flight_id = fi.scheduled_flight_id
                 GROUP BY i.reservation_id
            )
            SELECT r.reservation_id, r.pnr, r.channel, ag.legal_name, tr.route_id,
                   CAST(to_char(r.created_at, 'YYYYMMDD') AS INTEGER),
                   CASE WHEN tr.first_departure IS NULL THEN NULL
                        ELSE CAST(to_char(tr.first_departure, 'YYYYMMDD') AS INTEGER) END,
                   r.status,
                   (SELECT COUNT(*) FROM reservation_passenger rp
                     WHERE rp.reservation_id = r.reservation_id),
                   COALESCE(tr.segments, 0), r.total_amount, r.currency,
                   CASE WHEN e.confirmed_at IS NOT NULL AND e.held_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (e.confirmed_at - e.held_at)) / 60
                        END,
                   CASE WHEN e.cancelled_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (e.cancelled_at - r.created_at)) / 60
                        END,
                   CASE WHEN e.cancelled_at IS NOT NULL AND tr.first_departure IS NOT NULL
                        THEN tr.first_departure - e.cancelled_at::date
                        END,
                   (e.held_at IS NOT NULL), (e.confirmed_at IS NOT NULL),
                   (e.cancelled_at IS NOT NULL), (e.expired_at IS NOT NULL),
                   COALESCE(e.event_count, 0)
              FROM reservation r
              LEFT JOIN events e  ON e.reservation_id = r.reservation_id
              LEFT JOIN trip   tr ON tr.reservation_id = r.reservation_id
              LEFT JOIN agency ag ON ag.agency_id = r.agency_id
        """)
        rows = cur.fetchall()

    payload = []
    for r in rows:
        (rid, pnr, channel, agency, route_id, created_dk, dep_dk, status, pax,
         segments, total, currency, mins_confirm, mins_cancel, days_before,
         was_held, was_conf, was_canc, expired, ev_count) = r
        payload.append((
            rid, pnr,
            channels.get((channel, agency)) or channels.get((channel, None)),
            route_id, created_dk, dep_dk, status, pax, segments, total, currency,
            int(mins_confirm) if mins_confirm is not None else None,
            int(mins_cancel) if mins_cancel is not None else None,
            days_before, was_held, was_conf, was_canc, expired, ev_count,
        ))

    with olap.cursor() as cur:
        cur.executemany(
            """INSERT INTO fact_reservation_lifecycle (reservation_key, pnr,
                    channel_key, route_key, created_date_key, departure_date_key,
                    final_status, passengers, segments, total_amount, currency,
                    minutes_to_confirm, minutes_to_cancel,
                    days_before_departure_at_cancel, was_held, was_confirmed,
                    was_cancelled, expired_without_payment, event_count)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (reservation_key) DO UPDATE
                   SET final_status = EXCLUDED.final_status,
                       minutes_to_confirm = EXCLUDED.minutes_to_confirm,
                       minutes_to_cancel  = EXCLUDED.minutes_to_cancel,
                       days_before_departure_at_cancel =
                           EXCLUDED.days_before_departure_at_cancel,
                       was_confirmed = EXCLUDED.was_confirmed,
                       was_cancelled = EXCLUDED.was_cancelled,
                       expired_without_payment = EXCLUDED.expired_without_payment,
                       event_count = EXCLUDED.event_count,
                       loaded_at = now()""",
            payload,
        )
    return len(payload)


# ---------------------------------------------------------------------

def main() -> int:
    started = datetime.now(timezone.utc)
    log(f"ETL iniciado (full_refresh={FULL_REFRESH})")
    stats: dict[str, int] = {}

    with connect(OLTP_DSN, "airline") as oltp, connect(OLAP_DSN, "analytics") as olap:
        run_id = None
        with olap.cursor() as cur:
            cur.execute(
                """INSERT INTO etl_run_log (started_at, status)
                   VALUES (%s, 'RUNNING') RETURNING run_id""",
                (started,),
            )
            run_id = cur.fetchone()[0]

        log("Cargando dimensiones…")
        stats["dim_date"] = load_dim_date(olap)
        stats["dim_route"] = load_dim_route(oltp, olap)
        stats["dim_fare"] = load_dim_fare(oltp, olap)
        stats["dim_flight"] = load_dim_flight(oltp, olap)
        stats["dim_channel"] = load_dim_channel(oltp, olap)
        olap.commit()

        channels = channel_map(olap)

        log("Cargando hechos…")
        watermark = get_watermark(olap, "fact_ticket_sale")
        log(f"  marca de agua fact_ticket_sale: {watermark:%Y-%m-%d %H:%M:%S}")
        tickets, new_watermark = load_fact_ticket_sale(oltp, olap, watermark, channels)
        stats["fact_ticket_sale"] = tickets
        if tickets:
            set_watermark(olap, "fact_ticket_sale", new_watermark)

        stats["fact_flight_occupancy"] = load_fact_flight_occupancy(oltp, olap)
        stats["fact_reservation_lifecycle"] = load_fact_reservation_lifecycle(
            oltp, olap, channels
        )

        finished = datetime.now(timezone.utc)
        with olap.cursor() as cur:
            cur.execute(
                """UPDATE etl_run_log
                      SET finished_at = %s, status = 'SUCCESS',
                          rows_written = %s, detail = %s
                    WHERE run_id = %s""",
                (finished, sum(stats.values()), json.dumps(stats), run_id),
            )

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    log(f"ETL completado en {elapsed:.1f}s")
    for table, count in stats.items():
        log(f"  {table:<32} {count:>8} filas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
