"""Errores de dominio.

Cada error lleva un código estable para que el frontend pueda reaccionar de
forma específica. DEC-13: ante 409 SEAT_UNAVAILABLE la interfaz no muestra un
error genérico, recarga disponibilidad y propone alternativas — para eso
necesita distinguir este caso de cualquier otro 409.
"""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"
    status_code: int = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFound(DomainError):
    code = "NOT_FOUND"
    status_code = 404


class InvalidItinerary(DomainError):
    """RF-09: continuidad geográfica, MCT y solapamiento horario."""

    code = "INVALID_ITINERARY"
    status_code = 422


class SeatUnavailable(DomainError):
    """RNF-C1: el rechazo determinista que recibe quien pierde la carrera."""

    code = "SEAT_UNAVAILABLE"
    status_code = 409


class InventoryLockTimeout(DomainError):
    """DEC-4: la espera por el bloqueo superó `lock_timeout`."""

    code = "INVENTORY_BUSY"
    status_code = 503


class InvalidState(DomainError):
    code = "INVALID_STATE"
    status_code = 409


class CreditLimitExceeded(DomainError):
    """RF-22: la agencia no tiene cupo suficiente."""

    code = "CREDIT_LIMIT_EXCEEDED"
    status_code = 402
