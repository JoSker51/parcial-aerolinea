"""Pruebas automatizadas de RNF-C1 y del flujo de reserva.

Requieren el stack levantado (`docker compose up -d`). No usan `TestClient`
en proceso a propósito: la contención debe atravesar la API real y varios
procesos uvicorn, tal como ocurre en producción. Una prueba en proceso
mediría el GIL de Python, no el bloqueo de PostgreSQL.

    pytest tests/ -v
"""
from __future__ import annotations

import os
import threading
from datetime import date, timedelta

import httpx
import psycopg
import pytest

API = os.getenv("API_URL", "http://localhost:8010")
DSN = os.getenv("TEST_DSN", "postgresql://airline:airline@localhost:5433/airline")


# --------------------------------------------------------------- utilidades

def db():
    conn = psycopg.connect(DSN, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SET search_path TO airline, public")
    return conn


# Tablas transaccionales, en el orden en que se vacían.
# Se usa TRUNCATE y no DELETE por dos razones que vale la pena entender:
#   1. `payment`, `ticket` y `reservation_event` referencian `reservation` sin
#      ON DELETE CASCADE — deliberadamente: en producción una reserva no se
#      borra jamás, se cancela.
#   2. `reservation_event` tiene un trigger que prohíbe DELETE (RNF-A2). Un
#      TRUNCATE no dispara triggers de fila, así que respeta la inmutabilidad
#      del log en operación normal y aun así permite limpiar en pruebas.
_BOOKING_TABLES = (
    "reservation_event", "payment_event", "refund", "payment",
    "seat_assignment", "ancillary", "ticket",
    "itinerary_segment", "itinerary", "reservation_passenger", "reservation",
)


def reset_bookings() -> None:
    """Devuelve la base a un estado conocido: sin reservas, inventario en cero."""
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            f"TRUNCATE {', '.join(_BOOKING_TABLES)} RESTART IDENTITY CASCADE"
        )
        cur.execute(
            "UPDATE flight_inventory SET seats_sold = 0, seats_held = 0, "
            "version = version + 1"
        )


def scarce_flight(cabin: str = "BUS", seats_left: int = 1) -> dict:
    """Deja un vuelo futuro con exactamente `seats_left` sillas libres.

    Reinicia primero todo el inventario: cada prueba parte de un estado
    conocido y no hereda los vuelos que otra dejó llenos.
    """
    reset_bookings()
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT fi.flight_instance_id, inv.capacity, r.origin_iata,
                      r.destination_iata, fi.flight_date
                 FROM flight_instance fi
                 JOIN scheduled_flight sf ON sf.scheduled_flight_id = fi.scheduled_flight_id
                 JOIN route r ON r.route_id = sf.route_id
                 JOIN flight_inventory inv ON inv.flight_instance_id = fi.flight_instance_id
                                          AND inv.cabin_code = %s
                WHERE fi.flight_date >= CURRENT_DATE + 2 AND fi.status = 'SCHEDULED'
                ORDER BY fi.flight_date, fi.flight_instance_id LIMIT 1""",
            (cabin,),
        )
        fid, capacity, origin, destination, flight_date = cur.fetchone()
        cur.execute(
            """UPDATE flight_inventory
                  SET seats_sold = %s, seats_held = 0, version = version + 1
                WHERE flight_instance_id = %s AND cabin_code = %s""",
            (capacity - seats_left, fid, cabin),
        )
    return {
        "flight_instance_id": fid, "capacity": capacity, "cabin": cabin,
        "origin": origin, "destination": destination, "flight_date": flight_date,
    }


def booking_payload(flight_instance_id: int, cabin: str, tag: str,
                    passengers: int = 1) -> dict:
    return {
        "channel": "WEB",
        "contact_email": f"{tag}@example.com",
        "currency": "COP",
        "itineraries": [
            {"segments": [{"flight_instance_id": flight_instance_id,
                           "cabin_code": cabin}]}
        ],
        "passengers": [
            {
                "first_name": f"Pax{i}", "last_name": f"Test{tag}",
                "document_type": "CC", "document_number": f"{tag}{i:04d}",
                "passenger_type": "ADT",
            }
            for i in range(passengers)
        ],
    }


def oversell_violations() -> int:
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM v_oversell_check")
        return cur.fetchone()[0]


def fire_concurrently(payloads: list[dict], timeout: float = 30.0) -> list[dict]:
    """Lanza N POST simultáneos y devuelve las respuestas.

    La `Barrier` es lo que hace válida la prueba: sin ella, la primera
    solicitud terminaría antes de que la segunda empezara.
    """
    n = len(payloads)
    barrier = threading.Barrier(n)
    out: list[dict] = [None] * n  # type: ignore[list-item]

    def worker(idx: int) -> None:
        with httpx.Client(timeout=timeout) as client:
            barrier.wait()
            resp = client.post(f"{API}/api/v1/reservations", json=payloads[idx])
            out[idx] = {"status": resp.status_code, "body": resp.json()}

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


# ------------------------------------------------------------------ pruebas

@pytest.fixture(scope="session", autouse=True)
def _clean_slate():
    """API viva e inventario en cero antes de empezar.

    Sin esto las pruebas no son independientes: `scarce_flight` y
    `test_multi_segment_hold_is_atomic` dejan vuelos deliberadamente llenos, y
    esa marca sobrevive a la corrida. La siguiente ejecución de la suite
    encontraría sin cupo los vuelos que otra prueba necesita y fallaría por un
    motivo que no tiene nada que ver con lo que estaba verificando.
    """
    try:
        httpx.get(f"{API}/health", timeout=5).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"La API no responde en {API}: {exc}")

    reset_bookings()
    yield


def test_search_returns_direct_flights():
    """RF-01."""
    target = date.today() + timedelta(days=7)
    r = httpx.get(f"{API}/api/v1/flights/search", params={
        "origin": "BOG", "destination": "MDE", "date": target.isoformat(),
        "cabin": "ECO", "passengers": 1, "max_stops": 0,
    }, timeout=20)
    assert r.status_code == 200
    body = r.json()
    assert body["offers"], "Se esperaba al menos un vuelo BOG-MDE"
    assert all(o["stops"] == 0 for o in body["offers"])
    assert all(float(o["total_amount"]) > 0 for o in body["offers"])


def test_search_returns_connections():
    """RF-02: itinerarios con escala, respetando el MCT del aeropuerto."""
    target = date.today() + timedelta(days=7)
    r = httpx.get(f"{API}/api/v1/flights/search", params={
        "origin": "BOG", "destination": "SMR", "date": target.isoformat(),
        "cabin": "ECO", "passengers": 1, "max_stops": 1,
    }, timeout=20)
    assert r.status_code == 200
    offers = r.json()["offers"]
    connecting = [o for o in offers if o["stops"] == 1]
    assert connecting, "Se esperaba al menos un itinerario BOG-CTG-SMR"
    for offer in connecting:
        assert len(offer["segments"]) == 2
        assert offer["segments"][0]["destination"] == offer["segments"][1]["origin"]
        # CTG declara 40 min de conexión mínima.
        assert offer["connection_minutes"][0] >= 40


def test_reservation_lifecycle():
    """RF-08, RF-10, RF-12, RF-15, RF-16 de extremo a extremo."""
    flight = scarce_flight(cabin="ECO", seats_left=10)
    payload = booking_payload(flight["flight_instance_id"], "ECO", "LIFE")

    created = httpx.post(f"{API}/api/v1/reservations", json=payload, timeout=20)
    assert created.status_code == 201
    reservation = created.json()
    pnr = reservation["pnr"]
    assert reservation["status"] == "HELD"
    assert reservation["hold_expires_at"] is not None
    assert reservation["tickets"] == []  # aún no se emite: no se ha pagado

    # RF-15 con control de acceso (RNF-S2).
    ok = httpx.get(f"{API}/api/v1/reservations/{pnr}",
                   params={"last_name": "TestLIFE"}, timeout=20)
    assert ok.status_code == 200
    bad = httpx.get(f"{API}/api/v1/reservations/{pnr}",
                    params={"last_name": "Equivocado"}, timeout=20)
    assert bad.status_code == 404, "Un apellido incorrecto no debe revelar el PNR"

    # RF-12: confirmación vía webhook simulado.
    confirmed = httpx.post(
        f"{API}/api/v1/reservations/{pnr}/confirm",
        json={"psp_event_id": f"evt_{pnr}_1"}, timeout=20,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    assert len(confirmed.json()["tickets"]) == 1  # 1 pasajero x 1 tramo

    # RF-16: cancelación libera inventario.
    cancelled = httpx.post(f"{API}/api/v1/reservations/{pnr}/cancel",
                           json={"reason": "TEST"}, timeout=20)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert oversell_violations() == 0


def test_confirm_is_idempotent():
    """RNF-C4: un webhook reentregado no duplica tiquetes ni cobros."""
    flight = scarce_flight(cabin="ECO", seats_left=10)
    payload = booking_payload(flight["flight_instance_id"], "ECO", "IDEM")
    pnr = httpx.post(f"{API}/api/v1/reservations", json=payload,
                     timeout=20).json()["pnr"]

    event_id = f"evt_{pnr}_dup"
    for _ in range(5):
        resp = httpx.post(f"{API}/api/v1/reservations/{pnr}/confirm",
                          json={"psp_event_id": event_id}, timeout=20)
        assert resp.status_code == 200

    final = httpx.get(f"{API}/api/v1/reservations/{pnr}", timeout=20).json()
    assert final["status"] == "CONFIRMED"
    assert len(final["tickets"]) == 1, "El webhook duplicado emitió tiquetes de más"

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) FROM payment p
                 JOIN reservation r ON r.reservation_id = p.reservation_id
                WHERE r.pnr = %s""",
            (pnr,),
        )
        assert cur.fetchone()[0] == 1, "Se registró más de un pago"


def test_invalid_connection_is_rejected():
    """RF-09: un itinerario sin continuidad geográfica no se retiene."""
    target = date.today() + timedelta(days=7)
    a = httpx.get(f"{API}/api/v1/flights/search", params={
        "origin": "BOG", "destination": "MDE", "date": target.isoformat(),
        "cabin": "ECO", "max_stops": 0}, timeout=20).json()["offers"]
    b = httpx.get(f"{API}/api/v1/flights/search", params={
        "origin": "BOG", "destination": "CLO", "date": target.isoformat(),
        "cabin": "ECO", "max_stops": 0}, timeout=20).json()["offers"]
    assert a and b

    # BOG->MDE seguido de BOG->CLO: el segundo tramo no sale donde llegó el primero.
    payload = {
        "channel": "WEB", "contact_email": "bad@example.com", "currency": "COP",
        "itineraries": [{"segments": [
            {"flight_instance_id": a[0]["segments"][0]["flight_instance_id"],
             "cabin_code": "ECO"},
            {"flight_instance_id": b[0]["segments"][0]["flight_instance_id"],
             "cabin_code": "ECO"},
        ]}],
        "passengers": [{"first_name": "Mal", "last_name": "Itinerario",
                        "document_type": "CC", "document_number": "BADITIN01",
                        "passenger_type": "ADT"}],
    }
    resp = httpx.post(f"{API}/api/v1/reservations", json=payload, timeout=20)
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_ITINERARY"


# ===================================================================
#  RNF-C1 — la prueba que importa
# ===================================================================

@pytest.mark.parametrize(
    ("workers", "seats"),
    [(2, 1), (10, 1), (40, 1), (40, 5)],
    ids=["dos-por-la-ultima", "diez-por-la-ultima", "cuarenta-por-la-ultima",
         "cuarenta-por-cinco"],
)
def test_no_oversell_under_concurrency(workers: int, seats: int):
    """N solicitudes simultáneas por K sillas: exactamente K ganan, 0 sobreventa.

    El caso (2, 1) es literalmente el escenario del enunciado: "dos pasajeros
    intentan reservar la última silla al mismo tiempo". Los demás lo escalan
    hasta el supuesto SUP-5 (40 solicitudes concurrentes).
    """
    flight = scarce_flight(cabin="BUS", seats_left=seats)
    payloads = [
        booking_payload(flight["flight_instance_id"], "BUS", f"C{workers}W{i}")
        for i in range(workers)
    ]

    results = fire_concurrently(payloads)

    created = [r for r in results if r["status"] == 201]
    rejected = [r for r in results if r["status"] == 409]
    other = [r for r in results if r["status"] not in (201, 409)]

    assert not other, f"Respuestas inesperadas: {other}"
    assert len(created) == seats, (
        f"Se esperaban {seats} reservas exitosas, hubo {len(created)}"
    )
    assert len(rejected) == workers - seats
    # El rechazo debe ser específico, no un error genérico: el frontend
    # reacciona distinto a SEAT_UNAVAILABLE (DEC-13).
    assert all(r["body"]["code"] == "SEAT_UNAVAILABLE" for r in rejected)

    # Aserción dura: contra el motor, no contra los códigos HTTP.
    assert oversell_violations() == 0, "Se produjo sobreventa"

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT capacity, seats_sold, seats_held
                 FROM flight_inventory
                WHERE flight_instance_id = %s AND cabin_code = 'BUS'""",
            (flight["flight_instance_id"],),
        )
        capacity, sold, held = cur.fetchone()
        assert sold + held == capacity, (
            "La cabina debería quedar exactamente llena, "
            f"quedó en {sold + held}/{capacity}"
        )
        assert held == seats


def test_multi_segment_hold_is_atomic():
    """RNF-C2: si un tramo no tiene cupo, ningún tramo queda retenido."""
    target = date.today() + timedelta(days=7)
    offers = httpx.get(f"{API}/api/v1/flights/search", params={
        "origin": "BOG", "destination": "SMR", "date": target.isoformat(),
        "cabin": "ECO", "max_stops": 1}, timeout=20).json()["offers"]
    connecting = [o for o in offers if o["stops"] == 1]
    if not connecting:
        pytest.skip("No hay itinerarios con escala en el rango sembrado")

    itinerary = connecting[0]
    leg1, leg2 = (s["flight_instance_id"] for s in itinerary["segments"])

    # Se agota el SEGUNDO tramo. El primero sigue con cupo de sobra.
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE flight_inventory SET seats_sold = capacity, seats_held = 0
                WHERE flight_instance_id = %s AND cabin_code = 'ECO'""",
            (leg2,),
        )
        cur.execute(
            """SELECT seats_held FROM flight_inventory
                WHERE flight_instance_id = %s AND cabin_code = 'ECO'""",
            (leg1,),
        )
        held_before = cur.fetchone()[0]

    payload = {
        "channel": "WEB", "contact_email": "atomic@example.com", "currency": "COP",
        "itineraries": [{"segments": [
            {"flight_instance_id": leg1, "cabin_code": "ECO"},
            {"flight_instance_id": leg2, "cabin_code": "ECO"},
        ]}],
        "passengers": [{"first_name": "Ato", "last_name": "Mico",
                        "document_type": "CC", "document_number": "ATOMIC001",
                        "passenger_type": "ADT"}],
    }
    resp = httpx.post(f"{API}/api/v1/reservations", json=payload, timeout=20)
    assert resp.status_code == 409
    assert resp.json()["code"] == "SEAT_UNAVAILABLE"

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT seats_held FROM flight_inventory
                WHERE flight_instance_id = %s AND cabin_code = 'ECO'""",
            (leg1,),
        )
        held_after = cur.fetchone()[0]
    assert held_after == held_before, (
        "El primer tramo quedó retenido pese a que el itinerario falló: "
        "la retención no fue atómica"
    )


def test_expired_hold_releases_inventory():
    """RF-11 / RNF-C3: la retención vencida devuelve el inventario."""
    flight = scarce_flight(cabin="BUS", seats_left=1)
    payload = booking_payload(flight["flight_instance_id"], "BUS", "EXPIRE")
    pnr = httpx.post(f"{API}/api/v1/reservations", json=payload,
                     timeout=20).json()["pnr"]

    # El vuelo queda sin cupo mientras la retención vive.
    second = httpx.post(
        f"{API}/api/v1/reservations",
        json=booking_payload(flight["flight_instance_id"], "BUS", "EXPIRE2"),
        timeout=20,
    )
    assert second.status_code == 409

    # Se fuerza el vencimiento en lugar de esperar 20 minutos reales.
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE reservation SET hold_expires_at = now() - interval '1 minute' "
            "WHERE pnr = %s",
            (pnr,),
        )

    released = httpx.post(f"{API}/internal/jobs/expire-holds", timeout=20)
    assert released.status_code == 200
    assert released.json()["expired"] >= 1

    # Ahora sí debe haber cupo.
    third = httpx.post(
        f"{API}/api/v1/reservations",
        json=booking_payload(flight["flight_instance_id"], "BUS", "EXPIRE3"),
        timeout=20,
    )
    assert third.status_code == 201
    assert oversell_violations() == 0
