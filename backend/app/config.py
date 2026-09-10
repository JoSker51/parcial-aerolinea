"""Configuración de la aplicación."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://airline:airline@localhost:5433/airline",
    )
    # SUP-7: duración de la retención de inventario.
    hold_minutes: int = int(os.getenv("HOLD_MINUTES", "20"))
    # DEC-4: antes que dejar crecer una espera sin límite, se falla rápido
    # con un mensaje claro. Un fallo limpio es mejor que degradar en silencio.
    lock_timeout_ms: int = int(os.getenv("LOCK_TIMEOUT_MS", "3000"))
    # Búsqueda con escalas: ventana máxima de conexión (RF-02).
    max_connection_hours: int = int(os.getenv("MAX_CONNECTION_HOURS", "12"))
    max_stops: int = int(os.getenv("MAX_STOPS", "1"))
    sql_echo: bool = os.getenv("SQL_ECHO", "0") == "1"
    pool_size: int = int(os.getenv("POOL_SIZE", "20"))
    max_overflow: int = int(os.getenv("MAX_OVERFLOW", "40"))


settings = Settings()
