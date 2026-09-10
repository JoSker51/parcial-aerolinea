"""Servicio de reservas — el núcleo transaccional del sistema.

Implementa DEC-4 (bloqueo pesimista sobre `flight_inventory`) y por tanto es
donde se cumple o se rompe RNF-C1: nunca confirmar más inventario que la
capacidad.

Tres propiedades que el código sostiene de forma explícita y que hay que poder
defender línea por línea:

1. **Orden canónico de bloqueo.** Las filas se bloquean SIEMPRE ordenadas por
   `(flight_instance_id, cabin_code)`. Sin ese orden, dos itinerarios que
   comparten tramos en sentido inverso (A->B->C y C->B->A) producen un abrazo
   mortal. Con orden fijo es imposible por construcción.

2. **Cero E/S externa dentro de la transacción.** Si una llamada al proveedor
   de pagos ocurriera con la fila bloqueada, un vuelo entero quedaría detenido
   mientras responde un tercero. Por eso el pago vive fuera (DEC-5).

3. **La restricción CHECK de la base es la última línea de defensa.** La lógica
   de aplicación verifica disponibilidad, pero `ck_inventory_no_oversell` la
   respalda: si un bug la burlara, el motor aborta la transacción. La regla de
   negocio más importante del sistema no vive solo en Python.
"""
from __future__ import annotations

import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from psycopg import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import apply_lock_timeout
from app.errors import (
    CreditLimitExceeded,
    InvalidItinerary,
    InvalidState,
    InventoryLockTimeout,
    NotFound,
    SeatUnavailable,
)
from app.schemas import ReservationCreate

# RF-13 / RNF-S2: alfabeto sin caracteres ambiguos (I, O, 0, 1) y sorteo
# aleatorio. Un PNR secuencial permitiría enumerar reservas ajenas.
_PNR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_pnr() -> str:
    return "".join(secrets.choice(_PNR_ALPHABET) for _ in range(6))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------

_SEGMENT_INFO_SQL = text("""
SELECT fi.flight_instance_id, fi.flight_date, fi.departure_utc, fi.arrival_utc,
       fi.status, sf.flight_number, sf.route_id,
       r.origin_iata, r.destination_iata,
       ap_d.min_connection_min AS hub_min_connection
FROM flight_instance  fi
JOIN scheduled_flight sf   ON sf.scheduled_flight_id = fi.scheduled_flight_id
JOIN route            r    ON r.route_id = sf.route_id
JOIN airport          ap_d ON ap_d.iata_code = r.destination_iata
WHERE fi.flight_instance_id = ANY(:ids)
""")

# DEC-4: EL bloqueo. `ORDER BY` antes de `FOR UPDATE` fija el orden de
# adquisición; PostgreSQL coloca el nodo LockRows por encima del Sort, de modo
# que las filas se bloquean en el orden ordenado y no en el de llegada.
_LOCK_INVENTORY_SQL = text("""
SELECT flight_instance_id, cabin_code, capacity, seats_sold, seats_held,
       oversell_factor, version,
       FLOOR(capacity * oversell_factor) - seats_sold - seats_held AS seats_available
FROM flight_inventory
WHERE (flight_instance_id, cabin_code)
      IN (SELECT * FROM unnest(CAST(:ids AS bigint[]), CAST(:cabins AS varchar[])))
ORDER BY flight_instance_id, cabin_code
FOR UPDATE
""")

_ELIGIBLE_FARE_SQL = text("""
SELECT f.fare_id, f.fare_class_code, f.base_amount, f.tax_pct
FROM fare f
JOIN fare_class fc ON fc.fare_class_code = f.fare_class_code
JOIN fare_rule  fr ON fr.fare_rule_id    = f.fare_rule_id
WHERE f.route_id = :route_id
  AND fc.cabin_code = :cabin
  AND f.currency = :currency
  AND CURRENT_DATE BETWEEN f.valid_from AND f.valid_to
  AND fr.min_advance_days <= (CAST(:flight_date AS date) - CURRENT_DATE)
  -- El CAST no es opcional: con :fare_class en NULL, PostgreSQL no puede
  -- inferir el tipo del parámetro y aborta con AmbiguousParameter.
  AND (CAST(:fare_class AS varchar) IS NULL
       OR f.fare_class_code = CAST(:fare_class AS varchar))
ORDER BY f.base_amount ASC
LIMIT 1
""")


def _audit(
    session: Session,
    reservation_id: int,
    *,
    event_type: str,
    actor_type: str,
    channel: str,
    from_status: str | None = None,
    to_status: str | None = None,
    actor_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """RNF-A1: se escribe en la MISMA transacción que la operación.

    Si la auditoría fuera asíncrona, un fallo entre la operación y su registro
    dejaría una transición sin rastro, y RNF-A1 exige el 100%.
    """
    session.execute(
        text("""
            INSERT INTO reservation_event
                (reservation_id, actor_type, actor_id, channel, event_type,
                 from_status, to_status, payload)
            VALUES (:rid, :atype, :aid, :chan, :etype, :from_s, :to_s,
                    CAST(:payload AS jsonb))
        """),
        {
            "rid": reservation_id, "atype": actor_type, "aid": actor_id,
            "chan": channel, "etype": event_type, "from_s": from_status,
            "to_s": to_status,
            "payload": __import__("json").dumps(payload or {}),
        },
    )


def _translate_db_error(exc: Exception) -> Exception:
    """Traduce fallos del motor a errores de dominio con significado."""
    orig = getattr(exc, "orig", None)
    if isinstance(orig, pg_errors.LockNotAvailable):
        return InventoryLockTimeout(
            "El inventario de este vuelo está siendo modificado; reintente.",
        )
    if isinstance(orig, pg_errors.CheckViolation):
        constraint = getattr(getattr(orig, "diag", None), "constraint_name", "") or ""
        if "oversell" in constraint or "non_negative" in constraint:
            # Última línea de defensa de RNF-C1: la aplicación falló, la base no.
            return SeatUnavailable(
                "Sin disponibilidad en uno de los tramos solicitados.",
                guard="db_check_constraint",
                constraint=constraint,
            )
        if "agency_credit" in constraint:
            return CreditLimitExceeded("La agencia no tiene cupo de crédito suficiente.")
    return exc


# ---------------------------------------------------------------------
# Validación del itinerario (RF-09)
# ---------------------------------------------------------------------

def _validate_itinerary(segments: list[dict[str, Any]]) -> None:
    """Continuidad geográfica, MCT y ausencia de solapamiento."""
    now = _now()
    for idx, seg in enumerate(segments):
        if seg["status"] != "SCHEDULED":
            raise InvalidItinerary(
                f"El vuelo {seg['flight_number']} no está disponible para venta.",
                flight_instance_id=seg["flight_instance_id"], status=seg["status"],
            )
        if seg["departure_utc"] <= now:
            raise InvalidItinerary(
                f"El vuelo {seg['flight_number']} ya salió o está por salir.",
                flight_instance_id=seg["flight_instance_id"],
            )
        if idx == 0:
            continue

        prev = segments[idx - 1]
        # Continuidad geográfica: el tramo n empieza donde terminó el n-1.
        if prev["destination_iata"] != seg["origin_iata"]:
            raise InvalidItinerary(
                "Los tramos no son continuos: "
                f"{prev['destination_iata']} != {seg['origin_iata']}.",
                previous=prev["flight_number"], current=seg["flight_number"],
            )
        # Tiempo mínimo de conexión, por aeropuerto.
        gap_minutes = (seg["departure_utc"] - prev["arrival_utc"]).total_seconds() / 60
        mct = prev["hub_min_connection"]
        if gap_minutes < mct:
            raise InvalidItinerary(
                f"Conexión insuficiente en {prev['destination_iata']}: "
                f"{int(gap_minutes)} min disponibles, {mct} min requeridos.",
                airport=prev["destination_iata"],
                available_minutes=int(gap_minutes), required_minutes=mct,
            )
        if gap_minutes > settings.max_connection_hours * 60:
            raise InvalidItinerary(
                f"Conexión demasiado larga en {prev['destination_iata']} "
                f"({int(gap_minutes)} min).",
                airport=prev["destination_iata"],
            )


# ---------------------------------------------------------------------
# RF-10 — Creación de la reserva con retención atómica de inventario
# ---------------------------------------------------------------------

def create_reservation(session: Session, payload: ReservationCreate) -> str:
    """Crea una reserva en estado HELD reteniendo inventario de forma atómica.

    Devuelve el PNR. Toda la operación ocurre en UNA transacción: o se retienen
    todos los tramos, o ninguno (RNF-C2).
    """
    # --- 1. Resolver los tramos solicitados -------------------------------
    requested_ids = [
        s.flight_instance_id for it in payload.itineraries for s in it.segments
    ]
    rows = session.execute(
        _SEGMENT_INFO_SQL, {"ids": requested_ids}
    ).mappings().all()
    info = {r["flight_instance_id"]: dict(r) for r in rows}

    missing = set(requested_ids) - set(info)
    if missing:
        raise NotFound(
            "Alguna instancia de vuelo no existe.", flight_instance_ids=sorted(missing)
        )

    # --- 2. Validar cada itinerario (RF-09) -------------------------------
    for itinerary in payload.itineraries:
        _validate_itinerary([info[s.flight_instance_id] for s in itinerary.segments])

    # --- 3. Cuántas sillas consume la reserva -----------------------------
    # Un infante viaja en brazos y no consume inventario (SUP-11 / diccionario).
    seats_needed = sum(1 for p in payload.passengers if p.passenger_type != "INF")
    if seats_needed == 0:
        raise InvalidItinerary("La reserva requiere al menos un pasajero con silla.")

    # Un mismo vuelo puede aparecer en más de un itinerario: se acumula.
    demand: dict[tuple[int, str], int] = defaultdict(int)
    for itinerary in payload.itineraries:
        for seg in itinerary.segments:
            demand[(seg.flight_instance_id, seg.cabin_code)] += seats_needed

    # --- 4. Cotizar ANTES de bloquear ------------------------------------
    # Consultar tarifas es lectura pura; hacerlo dentro del bloqueo alargaría
    # la sección crítica sin ninguna necesidad.
    quotes: dict[tuple[int, str, str | None], dict[str, Any]] = {}
    for itinerary in payload.itineraries:
        for seg in itinerary.segments:
            key = (seg.flight_instance_id, seg.cabin_code, seg.fare_class_code)
            if key in quotes:
                continue
            meta = info[seg.flight_instance_id]
            fare = session.execute(_ELIGIBLE_FARE_SQL, {
                "route_id": meta["route_id"],
                "cabin": seg.cabin_code,
                "currency": payload.currency,
                "flight_date": meta["flight_date"],
                "fare_class": seg.fare_class_code,
            }).mappings().first()
            if fare is None:
                raise InvalidItinerary(
                    f"No hay tarifa vigente para el vuelo {meta['flight_number']} "
                    f"en cabina {seg.cabin_code}.",
                    flight_instance_id=seg.flight_instance_id,
                )
            quotes[key] = dict(fare)

    # --- 5. Descuento de agencia (SUP-13, RF-21) --------------------------
    discount_pct = Decimal(0)
    commission_pct = Decimal(0)
    if payload.channel == "B2B":
        if payload.agency_id is None:
            raise InvalidItinerary("El canal B2B exige agency_id.")
        agency = session.execute(
            text("""SELECT agency_id, net_fare_discount_pct, commission_pct,
                           credit_limit, credit_used, status
                    FROM agency WHERE agency_id = :aid FOR UPDATE"""),
            {"aid": payload.agency_id},
        ).mappings().first()
        if agency is None:
            raise NotFound("Agencia no encontrada.", agency_id=payload.agency_id)
        if agency["status"] != "ACTIVE":
            raise InvalidState("La agencia está suspendida.")
        discount_pct = Decimal(agency["net_fare_discount_pct"])
        commission_pct = Decimal(agency["commission_pct"])

    try:
        # === SECCIÓN CRÍTICA ==========================================
        apply_lock_timeout(session)

        # --- 6. Bloquear TODAS las filas de inventario, en orden canónico ---
        keys = sorted(demand.keys())
        locked = session.execute(_LOCK_INVENTORY_SQL, {
            "ids": [k[0] for k in keys],
            "cabins": [k[1] for k in keys],
        }).mappings().all()

        by_key = {(r["flight_instance_id"], r["cabin_code"]): r for r in locked}
        if len(by_key) != len(keys):
            missing_inv = [k for k in keys if k not in by_key]
            raise NotFound(
                "No existe inventario para alguna combinación vuelo/cabina.",
                keys=[{"flight_instance_id": k[0], "cabin": k[1]} for k in missing_inv],
            )

        # --- 7. Verificar disponibilidad de TODOS los tramos (RNF-C2) ------
        # Se verifica todo antes de escribir nada: la atomicidad del itinerario
        # multi-tramo exige que un tramo sin cupo aborte la reserva completa.
        for key in keys:
            row = by_key[key]
            if row["seats_available"] < demand[key]:
                raise SeatUnavailable(
                    "Sin disponibilidad en uno de los tramos solicitados.",
                    flight_instance_id=key[0],
                    cabin_code=key[1],
                    requested=demand[key],
                    available=int(row["seats_available"]),
                )

        # --- 8. Retener ----------------------------------------------------
        for key in keys:
            session.execute(
                text("""
                    UPDATE flight_inventory
                       SET seats_held = seats_held + :n,
                           version    = version + 1,
                           updated_at = now()
                     WHERE flight_instance_id = :fid AND cabin_code = :cabin
                """),
                {"n": demand[key], "fid": key[0], "cabin": key[1]},
            )

        # --- 9. Crear la reserva ------------------------------------------
        expires_at = _now() + timedelta(minutes=settings.hold_minutes)
        reservation_id = None
        for attempt in range(5):
            pnr = _new_pnr()
            try:
                with session.begin_nested():
                    reservation_id = session.execute(
                        text("""
                            INSERT INTO reservation
                                (pnr, channel, agency_id, contact_email, contact_phone,
                                 status, currency, total_amount, hold_expires_at)
                            VALUES (:pnr, :channel, :agency, :email, :phone,
                                    'HELD', :currency, 0, :expires)
                            RETURNING reservation_id
                        """),
                        {
                            "pnr": pnr, "channel": payload.channel,
                            "agency": payload.agency_id, "email": payload.contact_email,
                            "phone": payload.contact_phone, "currency": payload.currency,
                            "expires": expires_at,
                        },
                    ).scalar_one()
                break
            except IntegrityError:
                if attempt == 4:  # 32^6 combinaciones: 5 colisiones es imposible
                    raise
                continue

        # --- 10. Pasajeros --------------------------------------------------
        for ref, pax in enumerate(payload.passengers, start=1):
            passenger_id = session.execute(
                text("""
                    INSERT INTO passenger (first_name, last_name, birth_date,
                                           document_type, document_number, email, phone)
                    VALUES (:fn, :ln, :bd, :dt, :dn, :em, :ph)
                    ON CONFLICT (document_type, document_number) DO UPDATE
                        SET first_name = EXCLUDED.first_name,
                            last_name  = EXCLUDED.last_name,
                            email      = COALESCE(EXCLUDED.email, passenger.email)
                    RETURNING passenger_id
                """),
                {
                    "fn": pax.first_name, "ln": pax.last_name, "bd": pax.birth_date,
                    "dt": pax.document_type, "dn": pax.document_number,
                    "em": pax.email, "ph": pax.phone,
                },
            ).scalar_one()
            session.execute(
                text("""
                    INSERT INTO reservation_passenger
                        (reservation_id, passenger_ref, passenger_id, passenger_type)
                    VALUES (:rid, :ref, :pid, :ptype)
                """),
                {"rid": reservation_id, "ref": ref, "pid": passenger_id,
                 "ptype": pax.passenger_type},
            )

        # --- 11. Itinerarios y tramos ---------------------------------------
        total = Decimal(0)
        for it_seq, itinerary in enumerate(payload.itineraries, start=1):
            first = info[itinerary.segments[0].flight_instance_id]
            last = info[itinerary.segments[-1].flight_instance_id]
            itinerary_id = session.execute(
                text("""
                    INSERT INTO itinerary (reservation_id, sequence_no,
                                           origin_iata, destination_iata, status)
                    VALUES (:rid, :seq, :orig, :dest, 'ACTIVE')
                    RETURNING itinerary_id
                """),
                {"rid": reservation_id, "seq": it_seq,
                 "orig": first["origin_iata"], "dest": last["destination_iata"]},
            ).scalar_one()

            for seg_seq, seg in enumerate(itinerary.segments, start=1):
                quote = quotes[(seg.flight_instance_id, seg.cabin_code,
                                seg.fare_class_code)]
                base = Decimal(quote["base_amount"])
                net = (base * (Decimal(100) - discount_pct) / Decimal(100)) \
                    .quantize(Decimal("0.01"))
                tax = (net * Decimal(quote["tax_pct"]) / Decimal(100)) \
                    .quantize(Decimal("0.01"))
                session.execute(
                    text("""
                        INSERT INTO itinerary_segment
                            (itinerary_id, sequence_no, flight_instance_id, cabin_code,
                             fare_class_code, quoted_fare_id, quoted_fare_amount,
                             quoted_tax_amount, status)
                        VALUES (:iid, :seq, :fid, :cabin, :fclass, :fare_id,
                                :fare_amt, :tax_amt, 'ACTIVE')
                    """),
                    {
                        "iid": itinerary_id, "seq": seg_seq,
                        "fid": seg.flight_instance_id, "cabin": seg.cabin_code,
                        "fclass": quote["fare_class_code"],
                        "fare_id": quote["fare_id"], "fare_amt": net, "tax_amt": tax,
                    },
                )
                total += (net + tax) * seats_needed

        session.execute(
            text("UPDATE reservation SET total_amount = :t WHERE reservation_id = :rid"),
            {"t": total, "rid": reservation_id},
        )

        # --- 12. Cupo de crédito de la agencia (RF-22) ----------------------
        # Se debita DENTRO de la misma transacción que la retención: es la única
        # diferencia real de flujo entre canales.
        if payload.channel == "B2B":
            session.execute(
                text("""UPDATE agency SET credit_used = credit_used + :amt
                         WHERE agency_id = :aid"""),
                {"amt": total, "aid": payload.agency_id},
            )

        # --- 13. Auditoría (RNF-A1) -----------------------------------------
        _audit(
            session, reservation_id,
            event_type="RESERVATION_HELD",
            actor_type="AGENT" if payload.channel == "B2B" else "PASSENGER",
            actor_id=str(payload.agency_id) if payload.agency_id else None,
            channel=payload.channel, from_status=None, to_status="HELD",
            payload={
                "seats_per_segment": seats_needed,
                "segments": [
                    {"flight_instance_id": k[0], "cabin": k[1], "seats": v}
                    for k, v in demand.items()
                ],
                "total_amount": str(total),
            },
        )

        session.commit()
        return pnr
        # === FIN SECCIÓN CRÍTICA ======================================

    except DBAPIError as exc:
        session.rollback()
        raise _translate_db_error(exc) from exc
    except Exception:
        session.rollback()
        raise


# ---------------------------------------------------------------------
# RF-12 — Confirmación (webhook del PSP)
# ---------------------------------------------------------------------

def confirm_reservation(
    session: Session, pnr: str, *, psp_event_id: str,
    psp_intent_id: str | None = None, method: str = "CARD",
) -> str:
    """HELD -> CONFIRMED: mueve `seats_held` a `seats_sold` y emite tiquetes.

    Idempotente (RNF-C4): la unicidad de `payment_event.psp_event_id` hace que
    un webhook reentregado se descarte sin duplicar tiquetes ni cobros.
    """
    try:
        apply_lock_timeout(session)
        res = session.execute(
            text("""SELECT reservation_id, status, currency, total_amount, channel,
                           agency_id
                      FROM reservation WHERE pnr = :pnr FOR UPDATE"""),
            {"pnr": pnr},
        ).mappings().first()
        if res is None:
            raise NotFound("Reserva no encontrada.", pnr=pnr)

        # Idempotencia: si el evento ya se procesó, se devuelve el estado actual.
        already = session.execute(
            text("SELECT 1 FROM payment_event WHERE psp_event_id = :eid"),
            {"eid": psp_event_id},
        ).first()
        if already:
            session.rollback()
            return res["status"]

        if res["status"] == "CONFIRMED":
            session.rollback()
            return "CONFIRMED"
        if res["status"] != "HELD":
            raise InvalidState(
                f"No se puede confirmar una reserva en estado {res['status']}.",
                pnr=pnr, status=res["status"],
            )

        reservation_id = res["reservation_id"]

        # Demanda por (vuelo, cabina), igual que al retener.
        seats_needed = session.execute(
            text("""SELECT COUNT(*) FROM reservation_passenger
                     WHERE reservation_id = :rid AND passenger_type <> 'INF'"""),
            {"rid": reservation_id},
        ).scalar_one()

        segments = session.execute(
            text("""SELECT s.segment_id, s.flight_instance_id, s.cabin_code,
                           s.quoted_fare_id, s.quoted_fare_amount, s.quoted_tax_amount
                      FROM itinerary_segment s
                      JOIN itinerary i ON i.itinerary_id = s.itinerary_id
                     WHERE i.reservation_id = :rid AND s.status = 'ACTIVE'
                     ORDER BY s.flight_instance_id, s.cabin_code"""),
            {"rid": reservation_id},
        ).mappings().all()

        demand: dict[tuple[int, str], int] = defaultdict(int)
        for s in segments:
            demand[(s["flight_instance_id"], s["cabin_code"])] += seats_needed

        keys = sorted(demand.keys())
        session.execute(_LOCK_INVENTORY_SQL, {
            "ids": [k[0] for k in keys], "cabins": [k[1] for k in keys],
        })
        for key in keys:
            session.execute(
                text("""
                    UPDATE flight_inventory
                       SET seats_held = seats_held - :n,
                           seats_sold = seats_sold + :n,
                           version    = version + 1,
                           updated_at = now()
                     WHERE flight_instance_id = :fid AND cabin_code = :cabin
                """),
                {"n": demand[key], "fid": key[0], "cabin": key[1]},
            )

        payment_id = session.execute(
            text("""
                INSERT INTO payment (reservation_id, method, psp_provider,
                                     psp_intent_id, amount, currency, status,
                                     idempotency_key)
                VALUES (:rid, :method, 'MOCK_PSP', :intent, :amount, :currency,
                        'CAPTURED', :idem)
                ON CONFLICT (idempotency_key) DO UPDATE SET status = 'CAPTURED'
                RETURNING payment_id
            """),
            {
                "rid": reservation_id, "method": method,
                "intent": psp_intent_id or f"intent_{pnr}",
                "amount": res["total_amount"], "currency": res["currency"],
                "idem": f"pay_{pnr}",
            },
        ).scalar_one()

        session.execute(
            text("""INSERT INTO payment_event (payment_id, psp_event_id, event_type,
                                               payload)
                    VALUES (:pid, :eid, 'payment.captured', CAST(:pl AS jsonb))"""),
            {"pid": payment_id, "eid": psp_event_id,
             "pl": __import__("json").dumps({"pnr": pnr})},
        )

        # RF-14: un tiquete por (pasajero x tramo), con montos congelados (DEC-5).
        refs = session.execute(
            text("""SELECT passenger_ref FROM reservation_passenger
                     WHERE reservation_id = :rid AND passenger_type <> 'INF'
                     ORDER BY passenger_ref"""),
            {"rid": reservation_id},
        ).scalars().all()

        commission_pct = Decimal(0)
        if res["agency_id"]:
            commission_pct = Decimal(session.execute(
                text("SELECT commission_pct FROM agency WHERE agency_id = :aid"),
                {"aid": res["agency_id"]},
            ).scalar_one())

        for seg in segments:
            for ref in refs:
                fare_amt = Decimal(seg["quoted_fare_amount"])
                commission = (fare_amt * commission_pct / Decimal(100)) \
                    .quantize(Decimal("0.01"))
                session.execute(
                    text("""
                        INSERT INTO ticket
                            (ticket_number, reservation_id, passenger_ref, segment_id,
                             fare_id, fare_amount, tax_amount, commission_amount,
                             currency, status)
                        VALUES (:tno, :rid, :ref, :sid, :fid, :fare, :tax, :comm,
                                :cur, 'ISSUED')
                    """),
                    {
                        "tno": f"{pnr}-{seg['segment_id']}-{ref}",
                        "rid": reservation_id, "ref": ref,
                        "sid": seg["segment_id"], "fid": seg["quoted_fare_id"],
                        "fare": fare_amt, "tax": seg["quoted_tax_amount"],
                        "comm": commission, "cur": res["currency"],
                    },
                )

        session.execute(
            text("""UPDATE reservation
                       SET status = 'CONFIRMED', hold_expires_at = NULL,
                           updated_at = now()
                     WHERE reservation_id = :rid"""),
            {"rid": reservation_id},
        )
        _audit(
            session, reservation_id, event_type="RESERVATION_CONFIRMED",
            actor_type="PSP", actor_id=psp_event_id, channel=res["channel"],
            from_status="HELD", to_status="CONFIRMED",
            payload={"payment_id": payment_id, "tickets": len(segments) * len(refs)},
        )
        session.commit()
        return "CONFIRMED"

    except DBAPIError as exc:
        session.rollback()
        raise _translate_db_error(exc) from exc
    except Exception:
        session.rollback()
        raise


# ---------------------------------------------------------------------
# RF-16 — Cancelación
# ---------------------------------------------------------------------

def cancel_reservation(
    session: Session, pnr: str, *, reason: str = "PASSENGER_REQUEST",
    itinerary_id: int | None = None,
) -> str:
    """Cancela la reserva o un itinerario y libera inventario INMEDIATAMENTE.

    Inventario retenido por una reserva muerta es ingreso perdido: la liberación
    no puede esperar a un proceso posterior.
    """
    try:
        apply_lock_timeout(session)
        res = session.execute(
            text("""SELECT reservation_id, status, channel, total_amount, agency_id
                      FROM reservation WHERE pnr = :pnr FOR UPDATE"""),
            {"pnr": pnr},
        ).mappings().first()
        if res is None:
            raise NotFound("Reserva no encontrada.", pnr=pnr)
        if res["status"] in ("CANCELLED", "EXPIRED"):
            session.rollback()
            return res["status"]

        reservation_id = res["reservation_id"]
        was_confirmed = res["status"] == "CONFIRMED"

        seats = session.execute(
            text("""SELECT COUNT(*) FROM reservation_passenger
                     WHERE reservation_id = :rid AND passenger_type <> 'INF'"""),
            {"rid": reservation_id},
        ).scalar_one()

        segments = session.execute(
            text("""SELECT s.segment_id, s.flight_instance_id, s.cabin_code
                      FROM itinerary_segment s
                      JOIN itinerary i ON i.itinerary_id = s.itinerary_id
                     WHERE i.reservation_id = :rid AND s.status = 'ACTIVE'
                       AND (CAST(:iid AS bigint) IS NULL OR i.itinerary_id = CAST(:iid AS bigint))"""),
            {"rid": reservation_id, "iid": itinerary_id},
        ).mappings().all()

        demand: dict[tuple[int, str], int] = defaultdict(int)
        for s in segments:
            demand[(s["flight_instance_id"], s["cabin_code"])] += seats

        keys = sorted(demand.keys())
        if keys:
            session.execute(_LOCK_INVENTORY_SQL, {
                "ids": [k[0] for k in keys], "cabins": [k[1] for k in keys],
            })
            column = "seats_sold" if was_confirmed else "seats_held"
            for key in keys:
                session.execute(
                    text(f"""
                        UPDATE flight_inventory
                           SET {column} = GREATEST({column} - :n, 0),
                               version  = version + 1,
                               updated_at = now()
                         WHERE flight_instance_id = :fid AND cabin_code = :cabin
                    """),
                    {"n": demand[key], "fid": key[0], "cabin": key[1]},
                )

        session.execute(
            text("""UPDATE itinerary_segment SET status = 'CANCELLED'
                     WHERE segment_id = ANY(:sids)"""),
            {"sids": [s["segment_id"] for s in segments]},
        )
        session.execute(
            text("""UPDATE itinerary SET status = 'CANCELLED'
                     WHERE reservation_id = :rid
                       AND (CAST(:iid AS bigint) IS NULL OR itinerary_id = CAST(:iid AS bigint))"""),
            {"rid": reservation_id, "iid": itinerary_id},
        )
        session.execute(
            text("""UPDATE ticket SET status = 'CANCELLED', cancelled_at = now()
                     WHERE reservation_id = :rid AND segment_id = ANY(:sids)"""),
            {"rid": reservation_id, "sids": [s["segment_id"] for s in segments]},
        )

        # Si no queda ningún itinerario activo, la reserva completa se cancela.
        remaining = session.execute(
            text("""SELECT COUNT(*) FROM itinerary
                     WHERE reservation_id = :rid AND status = 'ACTIVE'"""),
            {"rid": reservation_id},
        ).scalar_one()
        new_status = res["status"]
        if remaining == 0:
            new_status = "CANCELLED"
            session.execute(
                text("""UPDATE reservation
                           SET status = 'CANCELLED', hold_expires_at = NULL,
                               updated_at = now()
                         WHERE reservation_id = :rid"""),
                {"rid": reservation_id},
            )
            if res["agency_id"]:
                session.execute(
                    text("""UPDATE agency
                               SET credit_used = GREATEST(credit_used - :amt, 0)
                             WHERE agency_id = :aid"""),
                    {"amt": res["total_amount"], "aid": res["agency_id"]},
                )

        _audit(
            session, reservation_id, event_type="RESERVATION_CANCELLED",
            actor_type="PASSENGER", channel=res["channel"],
            from_status=res["status"], to_status=new_status,
            payload={"reason": reason, "itinerary_id": itinerary_id,
                     "released_seats": sum(demand.values())},
        )
        session.commit()
        return new_status

    except DBAPIError as exc:
        session.rollback()
        raise _translate_db_error(exc) from exc
    except Exception:
        session.rollback()
        raise


# ---------------------------------------------------------------------
# RF-11 / RNF-C3 — Expiración de retenciones
# ---------------------------------------------------------------------

def expire_holds(session: Session, *, limit: int = 500) -> int:
    """Libera el inventario de las retenciones vencidas.

    En producción corre cada 30 s (RNF-C3 exige liberar en <= 60 s). Se usa
    `SKIP LOCKED` para que varias instancias del job puedan correr en paralelo
    sin pisarse ni bloquearse entre ellas.
    """
    expired = session.execute(
        text("""SELECT reservation_id, pnr FROM reservation
                 WHERE status = 'HELD' AND hold_expires_at < now()
                 ORDER BY hold_expires_at
                 LIMIT :lim FOR UPDATE SKIP LOCKED"""),
        {"lim": limit},
    ).mappings().all()

    released = 0
    for res in expired:
        rid = res["reservation_id"]
        seats = session.execute(
            text("""SELECT COUNT(*) FROM reservation_passenger
                     WHERE reservation_id = :rid AND passenger_type <> 'INF'"""),
            {"rid": rid},
        ).scalar_one()
        segments = session.execute(
            text("""SELECT s.flight_instance_id, s.cabin_code
                      FROM itinerary_segment s
                      JOIN itinerary i ON i.itinerary_id = s.itinerary_id
                     WHERE i.reservation_id = :rid AND s.status = 'ACTIVE'"""),
            {"rid": rid},
        ).mappings().all()

        demand: dict[tuple[int, str], int] = defaultdict(int)
        for s in segments:
            demand[(s["flight_instance_id"], s["cabin_code"])] += seats

        for key in sorted(demand.keys()):
            session.execute(
                text("""UPDATE flight_inventory
                           SET seats_held = GREATEST(seats_held - :n, 0),
                               version = version + 1, updated_at = now()
                         WHERE flight_instance_id = :fid AND cabin_code = :cabin"""),
                {"n": demand[key], "fid": key[0], "cabin": key[1]},
            )
        session.execute(
            text("""UPDATE reservation
                       SET status = 'EXPIRED', hold_expires_at = NULL,
                           updated_at = now()
                     WHERE reservation_id = :rid"""),
            {"rid": rid},
        )
        _audit(
            session, rid, event_type="HOLD_EXPIRED", actor_type="SYSTEM",
            channel="SYSTEM", from_status="HELD", to_status="EXPIRED",
            payload={"released_seats": sum(demand.values())},
        )
        released += 1

    session.commit()
    return released


# ---------------------------------------------------------------------
# RF-15 — Consulta
# ---------------------------------------------------------------------

def get_reservation(
    session: Session, pnr: str, *, last_name: str | None = None
) -> dict[str, Any]:
    """RNF-S2: el par PNR + apellido no es una comodidad, es el control de acceso."""
    res = session.execute(
        text("""SELECT reservation_id, pnr, status, channel, currency, total_amount,
                       hold_expires_at, created_at
                  FROM reservation WHERE pnr = :pnr"""),
        {"pnr": pnr.upper()},
    ).mappings().first()
    if res is None:
        raise NotFound("Reserva no encontrada.", pnr=pnr)

    passengers = session.execute(
        text("""SELECT rp.passenger_ref, p.first_name, p.last_name, rp.passenger_type
                  FROM reservation_passenger rp
                  JOIN passenger p ON p.passenger_id = rp.passenger_id
                 WHERE rp.reservation_id = :rid
                 ORDER BY rp.passenger_ref"""),
        {"rid": res["reservation_id"]},
    ).mappings().all()

    if last_name is not None:
        names = {p["last_name"].strip().upper() for p in passengers}
        if last_name.strip().upper() not in names:
            # Mismo error que "no existe": revelar que el PNR existe pero el
            # apellido no coincide facilitaría la enumeración (RNF-S2).
            raise NotFound("Reserva no encontrada.", pnr=pnr)

    itineraries = session.execute(
        text("""SELECT i.itinerary_id, i.sequence_no, i.origin_iata,
                       i.destination_iata, i.status
                  FROM itinerary i WHERE i.reservation_id = :rid
                 ORDER BY i.sequence_no"""),
        {"rid": res["reservation_id"]},
    ).mappings().all()

    segments = session.execute(
        text("""SELECT s.segment_id, s.itinerary_id, s.sequence_no,
                       s.flight_instance_id, sf.flight_number,
                       r.origin_iata, r.destination_iata,
                       fi.departure_utc, fi.arrival_utc,
                       s.cabin_code, s.fare_class_code, s.status,
                       s.quoted_fare_amount, s.quoted_tax_amount
                  FROM itinerary_segment s
                  JOIN itinerary i        ON i.itinerary_id = s.itinerary_id
                  JOIN flight_instance fi ON fi.flight_instance_id = s.flight_instance_id
                  JOIN scheduled_flight sf ON sf.scheduled_flight_id = fi.scheduled_flight_id
                  JOIN route r            ON r.route_id = sf.route_id
                 WHERE i.reservation_id = :rid
                 ORDER BY i.sequence_no, s.sequence_no"""),
        {"rid": res["reservation_id"]},
    ).mappings().all()

    tickets = session.execute(
        text("""SELECT ticket_number, passenger_ref, segment_id, fare_amount,
                       tax_amount, status
                  FROM ticket WHERE reservation_id = :rid
                 ORDER BY ticket_number"""),
        {"rid": res["reservation_id"]},
    ).mappings().all()

    by_itinerary: dict[int, list[dict]] = defaultdict(list)
    for s in segments:
        by_itinerary[s["itinerary_id"]].append({
            "segment_id": s["segment_id"], "sequence_no": s["sequence_no"],
            "flight_instance_id": s["flight_instance_id"],
            "flight_number": s["flight_number"], "origin": s["origin_iata"],
            "destination": s["destination_iata"],
            "departure_utc": s["departure_utc"], "arrival_utc": s["arrival_utc"],
            "cabin_code": s["cabin_code"], "fare_class_code": s["fare_class_code"],
            "status": s["status"],
            "quoted_fare_amount": s["quoted_fare_amount"],
            "quoted_tax_amount": s["quoted_tax_amount"],
        })

    return {
        "pnr": res["pnr"], "status": res["status"], "channel": res["channel"],
        "currency": res["currency"], "total_amount": res["total_amount"],
        "hold_expires_at": res["hold_expires_at"], "created_at": res["created_at"],
        "passengers": [dict(p) for p in passengers],
        "itineraries": [
            {
                "itinerary_id": it["itinerary_id"], "sequence_no": it["sequence_no"],
                "origin": it["origin_iata"], "destination": it["destination_iata"],
                "status": it["status"],
                "segments": by_itinerary.get(it["itinerary_id"], []),
            }
            for it in itineraries
        ],
        "tickets": [
            {
                "ticket_number": t["ticket_number"],
                "passenger_ref": t["passenger_ref"], "segment_id": t["segment_id"],
                "fare_amount": t["fare_amount"], "tax_amount": t["tax_amount"],
                "total": Decimal(t["fare_amount"]) + Decimal(t["tax_amount"]),
                "status": t["status"],
            }
            for t in tickets
        ],
    }
