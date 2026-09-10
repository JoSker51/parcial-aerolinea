"""Contratos de la API (Pydantic v2)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CabinCode = Literal["ECO", "BUS"]
PassengerType = Literal["ADT", "CHD", "INF"]


# --------------------------------------------------------------- Búsqueda

class SegmentOffer(BaseModel):
    flight_instance_id: int
    flight_number: str
    origin: str
    destination: str
    departure_utc: datetime
    arrival_utc: datetime
    cabin_code: CabinCode
    fare_class_code: str
    seats_available: int


class ItineraryOffer(BaseModel):
    """RF-02: un itinerario ofertado, directo o con escalas."""

    origin: str
    destination: str
    stops: int
    departure_utc: datetime
    arrival_utc: datetime
    total_duration_minutes: int
    connection_minutes: list[int] = Field(default_factory=list)
    segments: list[SegmentOffer]
    # RF-04: desglose. El frontend nunca convierte moneda (DEC-15).
    currency: str
    fare_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    price_per_passenger: Decimal


class SearchResponse(BaseModel):
    origin: str
    destination: str
    flight_date: date
    cabin_code: CabinCode
    passengers: int
    offers: list[ItineraryOffer]
    elapsed_ms: float


# ---------------------------------------------------------------- Reserva

class PassengerIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    document_type: str = Field(default="CC", max_length=20)
    document_number: str = Field(min_length=3, max_length=40)
    birth_date: date | None = None
    email: str | None = None
    phone: str | None = None
    passenger_type: PassengerType = "ADT"


class SegmentIn(BaseModel):
    flight_instance_id: int
    cabin_code: CabinCode = "ECO"
    fare_class_code: str | None = Field(
        default=None,
        description="Si se omite, se cotiza la clase elegible más barata de la cabina.",
    )


class ItineraryIn(BaseModel):
    segments: list[SegmentIn] = Field(min_length=1, max_length=4)


class ReservationCreate(BaseModel):
    """RF-08: una sola reserva con varios itinerarios y varios tramos."""

    channel: Literal["WEB", "B2B", "COUNTER"] = "WEB"
    agency_id: int | None = None
    contact_email: str
    contact_phone: str | None = None
    currency: str = "COP"
    itineraries: list[ItineraryIn] = Field(min_length=1, max_length=4)
    passengers: list[PassengerIn] = Field(min_length=1, max_length=9)

    @field_validator("passengers")
    @classmethod
    def _at_least_one_seat(cls, v: list[PassengerIn]) -> list[PassengerIn]:
        # Un infante viaja en brazos: no puede ser el único pasajero.
        if all(p.passenger_type == "INF" for p in v):
            raise ValueError("La reserva requiere al menos un pasajero con silla")
        return v


class SegmentOut(BaseModel):
    segment_id: int
    sequence_no: int
    flight_instance_id: int
    flight_number: str
    origin: str
    destination: str
    departure_utc: datetime
    arrival_utc: datetime
    cabin_code: str
    fare_class_code: str
    status: str
    quoted_fare_amount: Decimal
    quoted_tax_amount: Decimal


class ItineraryOut(BaseModel):
    itinerary_id: int
    sequence_no: int
    origin: str
    destination: str
    status: str
    segments: list[SegmentOut]


class PassengerOut(BaseModel):
    passenger_ref: int
    first_name: str
    last_name: str
    passenger_type: str


class TicketOut(BaseModel):
    ticket_number: str
    passenger_ref: int
    segment_id: int
    fare_amount: Decimal
    tax_amount: Decimal
    total: Decimal
    status: str


class ReservationOut(BaseModel):
    pnr: str
    status: str
    channel: str
    currency: str
    total_amount: Decimal
    hold_expires_at: datetime | None
    created_at: datetime
    passengers: list[PassengerOut]
    itineraries: list[ItineraryOut]
    tickets: list[TicketOut] = Field(default_factory=list)


class ConfirmIn(BaseModel):
    """DEC-5: simula el webhook del PSP.

    En producción este cuerpo lo envía el proveedor con su firma HMAC; aquí se
    expone como endpoint para poder ejercitar la transición HELD -> CONFIRMED.
    """

    psp_event_id: str = Field(description="Identificador único del evento del PSP")
    psp_intent_id: str | None = None
    method: Literal["CARD", "AGENCY_CREDIT"] = "CARD"


class CancelIn(BaseModel):
    reason: str = "PASSENGER_REQUEST"
    itinerary_id: int | None = Field(
        default=None,
        description="Si se omite, cancela la reserva completa (RF-16).",
    )


class ErrorOut(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
