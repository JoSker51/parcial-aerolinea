"""Búsqueda de vuelos — RF-01, RF-02.

Camino de LECTURA. En producción va contra réplicas de lectura (DEC-6) y su
resultado se cachea 60 s (DEC-8). El desfase de esa caché solo puede producir
un falso positivo de disponibilidad, que se corrige al retener: la verdad vive
en `flight_inventory` bajo bloqueo (DEC-4), nunca aquí.
"""
from __future__ import annotations

import time
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.schemas import ItineraryOffer, SearchResponse, SegmentOffer

# Tarifa elegible más barata para una ruta y cabina.
# `min_advance_days <= dias_hasta_el_vuelo` es la regla de "tarifa según
# anticipación" del enunciado, implementada en `fare_rule`.
_ELIGIBLE_FARE = """
    SELECT f.fare_id, f.fare_class_code, f.base_amount, f.tax_pct
    FROM fare f
    JOIN fare_class fc ON fc.fare_class_code = f.fare_class_code
    JOIN fare_rule  fr ON fr.fare_rule_id    = f.fare_rule_id
    WHERE f.route_id = av.route_id
      AND fc.cabin_code = av.cabin_code
      AND f.currency = :currency
      AND CURRENT_DATE BETWEEN f.valid_from AND f.valid_to
      AND fr.min_advance_days <= (av.flight_date - CURRENT_DATE)
    ORDER BY f.base_amount ASC
    LIMIT 1
"""

# --- Vuelos directos ---------------------------------------------------
# Se apoya en ix_flight_instance_search (RNF-P1).
_DIRECT_SQL = text(f"""
SELECT
    av.flight_instance_id, av.flight_number, av.origin_iata, av.destination_iata,
    av.departure_utc, av.arrival_utc, av.cabin_code, av.seats_available,
    fare.fare_class_code, fare.base_amount, fare.tax_pct
FROM v_flight_availability av
JOIN LATERAL ({_ELIGIBLE_FARE}) fare ON TRUE
WHERE av.origin_iata      = :origin
  AND av.destination_iata = :destination
  AND av.flight_date      = :flight_date
  AND av.cabin_code       = :cabin
  AND av.flight_status    = 'SCHEDULED'
  AND av.seats_available >= :passengers
ORDER BY (fare.base_amount * (1 + fare.tax_pct / 100)) ASC, av.departure_utc ASC
LIMIT 50
""")

# --- Itinerarios con una escala ----------------------------------------
# El tiempo mínimo de conexión es por aeropuerto (`airport.min_connection_min`),
# no una constante global: conectar en BOG toma más que en SMR.
_ONE_STOP_SQL = text(f"""
WITH avail AS (
    SELECT av.*, fare.fare_id, fare.fare_class_code, fare.base_amount, fare.tax_pct
    FROM v_flight_availability av
    JOIN LATERAL ({_ELIGIBLE_FARE}) fare ON TRUE
    WHERE av.cabin_code    = :cabin
      AND av.flight_status = 'SCHEDULED'
      AND av.seats_available >= :passengers
      AND av.flight_date BETWEEN :flight_date AND (CAST(:flight_date AS date) + 1)
)
SELECT
    l1.flight_instance_id AS l1_id, l1.flight_number AS l1_num,
    l1.origin_iata AS l1_orig, l1.destination_iata AS l1_dest,
    l1.departure_utc AS l1_dep, l1.arrival_utc AS l1_arr,
    l1.seats_available AS l1_seats, l1.fare_class_code AS l1_class,
    l1.base_amount AS l1_base, l1.tax_pct AS l1_tax,
    l2.flight_instance_id AS l2_id, l2.flight_number AS l2_num,
    l2.origin_iata AS l2_orig, l2.destination_iata AS l2_dest,
    l2.departure_utc AS l2_dep, l2.arrival_utc AS l2_arr,
    l2.seats_available AS l2_seats, l2.fare_class_code AS l2_class,
    l2.base_amount AS l2_base, l2.tax_pct AS l2_tax,
    EXTRACT(EPOCH FROM (l2.departure_utc - l1.arrival_utc)) / 60 AS connection_minutes
FROM avail l1
JOIN airport hub ON hub.iata_code = l1.destination_iata
JOIN avail l2    ON l2.origin_iata = l1.destination_iata
WHERE l1.origin_iata      = :origin
  AND l1.flight_date      = :flight_date
  AND l2.destination_iata = :destination
  AND l2.destination_iata <> l1.origin_iata
  -- RF-09: respeta el MCT del aeropuerto de conexión...
  AND l2.departure_utc >= l1.arrival_utc + (hub.min_connection_min || ' minutes')::interval
  -- ...y acota la ventana para no ofrecer conexiones absurdas.
  AND l2.departure_utc <= l1.arrival_utc + (:max_connection_hours || ' hours')::interval
ORDER BY ((l1.base_amount * (1 + l1.tax_pct / 100))
        + (l2.base_amount * (1 + l2.tax_pct / 100))) ASC,
         (l2.arrival_utc - l1.departure_utc) ASC
LIMIT 50
""")


def _money(base: Decimal, tax_pct: Decimal) -> tuple[Decimal, Decimal]:
    """Devuelve (tarifa, impuesto) redondeados a peso."""
    fare = Decimal(base).quantize(Decimal("0.01"))
    tax = (fare * Decimal(tax_pct) / Decimal(100)).quantize(Decimal("0.01"))
    return fare, tax


def _duration_minutes(start, end) -> int:
    return int((end - start).total_seconds() // 60)


def search_itineraries(
    session: Session,
    *,
    origin: str,
    destination: str,
    flight_date: date,
    cabin: str,
    passengers: int,
    currency: str = "COP",
    max_stops: int | None = None,
) -> SearchResponse:
    started = time.perf_counter()
    max_stops = settings.max_stops if max_stops is None else max_stops
    params = {
        "origin": origin.upper(),
        "destination": destination.upper(),
        "flight_date": flight_date,
        "cabin": cabin.upper(),
        "passengers": passengers,
        "currency": currency.upper(),
    }
    offers: list[ItineraryOffer] = []

    # --- Directos ---
    for row in session.execute(_DIRECT_SQL, params).mappings():
        fare, tax = _money(row["base_amount"], row["tax_pct"])
        segment = SegmentOffer(
            flight_instance_id=row["flight_instance_id"],
            flight_number=row["flight_number"],
            origin=row["origin_iata"],
            destination=row["destination_iata"],
            departure_utc=row["departure_utc"],
            arrival_utc=row["arrival_utc"],
            cabin_code=row["cabin_code"],
            fare_class_code=row["fare_class_code"],
            seats_available=row["seats_available"],
        )
        per_pax = fare + tax
        offers.append(
            ItineraryOffer(
                origin=segment.origin,
                destination=segment.destination,
                stops=0,
                departure_utc=segment.departure_utc,
                arrival_utc=segment.arrival_utc,
                total_duration_minutes=_duration_minutes(
                    segment.departure_utc, segment.arrival_utc
                ),
                segments=[segment],
                currency=params["currency"],
                fare_amount=fare * passengers,
                tax_amount=tax * passengers,
                total_amount=per_pax * passengers,
                price_per_passenger=per_pax,
            )
        )

    # --- Con una escala ---
    if max_stops >= 1:
        conn_params = {**params, "max_connection_hours": settings.max_connection_hours}
        for row in session.execute(_ONE_STOP_SQL, conn_params).mappings():
            f1, t1 = _money(row["l1_base"], row["l1_tax"])
            f2, t2 = _money(row["l2_base"], row["l2_tax"])
            segs = [
                SegmentOffer(
                    flight_instance_id=row["l1_id"], flight_number=row["l1_num"],
                    origin=row["l1_orig"], destination=row["l1_dest"],
                    departure_utc=row["l1_dep"], arrival_utc=row["l1_arr"],
                    cabin_code=params["cabin"], fare_class_code=row["l1_class"],
                    seats_available=row["l1_seats"],
                ),
                SegmentOffer(
                    flight_instance_id=row["l2_id"], flight_number=row["l2_num"],
                    origin=row["l2_orig"], destination=row["l2_dest"],
                    departure_utc=row["l2_dep"], arrival_utc=row["l2_arr"],
                    cabin_code=params["cabin"], fare_class_code=row["l2_class"],
                    seats_available=row["l2_seats"],
                ),
            ]
            per_pax = f1 + t1 + f2 + t2
            offers.append(
                ItineraryOffer(
                    origin=segs[0].origin,
                    destination=segs[-1].destination,
                    stops=1,
                    departure_utc=segs[0].departure_utc,
                    arrival_utc=segs[-1].arrival_utc,
                    total_duration_minutes=_duration_minutes(
                        segs[0].departure_utc, segs[-1].arrival_utc
                    ),
                    connection_minutes=[int(row["connection_minutes"])],
                    segments=segs,
                    currency=params["currency"],
                    fare_amount=(f1 + f2) * passengers,
                    tax_amount=(t1 + t2) * passengers,
                    total_amount=per_pax * passengers,
                    price_per_passenger=per_pax,
                )
            )

    offers.sort(key=lambda o: (o.total_amount, o.total_duration_minutes))
    return SearchResponse(
        origin=params["origin"],
        destination=params["destination"],
        flight_date=flight_date,
        cabin_code=params["cabin"],  # type: ignore[arg-type]
        passengers=passengers,
        offers=offers[:40],
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
    )
