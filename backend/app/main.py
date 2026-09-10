"""API de reservas — sección 5.6.

Alcance implementado (mínimo exigido por el enunciado, más lo necesario para
poder demostrar el mecanismo de concurrencia de extremo a extremo):

  GET    /api/v1/flights/search          RF-01, RF-02   buscar vuelos
  POST   /api/v1/reservations            RF-08, RF-10   crear reserva (HELD)
  GET    /api/v1/reservations/{pnr}      RF-15          consultar reserva
  POST   /api/v1/reservations/{pnr}/confirm  RF-12      confirmar (webhook PSP)
  POST   /api/v1/reservations/{pnr}/cancel   RF-16      cancelar
  POST   /internal/jobs/expire-holds     RF-11          liberar retenciones
  GET    /internal/oversell-check        RNF-C1         aserción de invariante
"""
from __future__ import annotations

from datetime import date

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.errors import DomainError
from app.schemas import (
    CancelIn,
    ConfirmIn,
    ReservationCreate,
    ReservationOut,
    SearchResponse,
)
from app.services import booking, search

app = FastAPI(
    title="Airline Booking API",
    version="0.1.0",
    description=(
        "Primera implementación del backend diseñado en 5.4. "
        "El control de sobreventa (RNF-C1) usa bloqueo pesimista sobre "
        "flight_inventory (DEC-4)."
    ),
)


@app.exception_handler(DomainError)
async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.get("/health", tags=["infra"])
def health(session: Session = Depends(get_session)) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


# ----------------------------------------------------------- RF-01, RF-02

@app.get("/api/v1/flights/search", response_model=SearchResponse, tags=["búsqueda"])
def search_flights(
    origin: str = Query(min_length=3, max_length=3, description="IATA de origen"),
    destination: str = Query(min_length=3, max_length=3),
    flight_date: date = Query(alias="date"),
    cabin: str = Query(default="ECO", pattern="^(ECO|BUS)$"),
    passengers: int = Query(default=1, ge=1, le=9),
    currency: str = Query(default="COP", min_length=3, max_length=3),
    max_stops: int = Query(default=1, ge=0, le=2),
    session: Session = Depends(get_session),
) -> SearchResponse:
    return search.search_itineraries(
        session, origin=origin, destination=destination, flight_date=flight_date,
        cabin=cabin, passengers=passengers, currency=currency, max_stops=max_stops,
    )


# ----------------------------------------------------------- RF-08, RF-10

@app.post(
    "/api/v1/reservations",
    response_model=ReservationOut,
    status_code=201,
    tags=["reservas"],
    responses={
        409: {"description": "SEAT_UNAVAILABLE — sin disponibilidad (RNF-C1)"},
        503: {"description": "INVENTORY_BUSY — lock_timeout excedido (DEC-4)"},
    },
)
def create_reservation(
    payload: ReservationCreate, session: Session = Depends(get_session)
) -> ReservationOut:
    pnr = booking.create_reservation(session, payload)
    return ReservationOut(**booking.get_reservation(session, pnr))


# ---------------------------------------------------------------- RF-15

@app.get(
    "/api/v1/reservations/{pnr}", response_model=ReservationOut, tags=["reservas"]
)
def get_reservation(
    pnr: str,
    last_name: str | None = Query(
        default=None,
        description="RNF-S2: obligatorio para acceso sin sesión autenticada.",
    ),
    session: Session = Depends(get_session),
) -> ReservationOut:
    return ReservationOut(**booking.get_reservation(session, pnr, last_name=last_name))


# ---------------------------------------------------------------- RF-12

@app.post(
    "/api/v1/reservations/{pnr}/confirm",
    response_model=ReservationOut,
    tags=["reservas"],
)
def confirm_reservation(
    pnr: str, payload: ConfirmIn, session: Session = Depends(get_session)
) -> ReservationOut:
    booking.confirm_reservation(
        session, pnr.upper(), psp_event_id=payload.psp_event_id,
        psp_intent_id=payload.psp_intent_id, method=payload.method,
    )
    return ReservationOut(**booking.get_reservation(session, pnr.upper()))


# ---------------------------------------------------------------- RF-16

@app.post(
    "/api/v1/reservations/{pnr}/cancel",
    response_model=ReservationOut,
    tags=["reservas"],
)
def cancel_reservation(
    pnr: str, payload: CancelIn, session: Session = Depends(get_session)
) -> ReservationOut:
    booking.cancel_reservation(
        session, pnr.upper(), reason=payload.reason, itinerary_id=payload.itinerary_id
    )
    return ReservationOut(**booking.get_reservation(session, pnr.upper()))


# ------------------------------------------------------- Procesos internos

@app.post("/internal/jobs/expire-holds", tags=["infra"])
def run_expire_holds(session: Session = Depends(get_session)) -> dict:
    """RF-11 / RNF-C3. En producción lo dispara un planificador cada 30 s."""
    return {"expired": booking.expire_holds(session)}


@app.get("/internal/oversell-check", tags=["infra"])
def oversell_check(session: Session = Depends(get_session)) -> dict:
    """RNF-C1: debe devolver siempre `violations: []`.

    Es la misma aserción que ejecuta la prueba de concurrencia, expuesta como
    endpoint para poder verificar el invariante en vivo durante la sustentación.
    """
    rows = session.execute(text("SELECT * FROM v_oversell_check")).mappings().all()
    return {"violations": [dict(r) for r in rows], "ok": len(rows) == 0}


@app.get("/internal/inventory/{flight_instance_id}", tags=["infra"])
def inventory_state(
    flight_instance_id: int, session: Session = Depends(get_session)
) -> dict:
    rows = session.execute(
        text("""SELECT cabin_code, capacity, seats_sold, seats_held, version,
                       FLOOR(capacity * oversell_factor) - seats_sold - seats_held
                           AS seats_available
                  FROM flight_inventory WHERE flight_instance_id = :fid
                 ORDER BY cabin_code"""),
        {"fid": flight_instance_id},
    ).mappings().all()
    return {"flight_instance_id": flight_instance_id, "cabins": [dict(r) for r in rows]}
