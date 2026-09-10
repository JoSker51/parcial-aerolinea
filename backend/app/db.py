"""Motor de base de datos y sesión.

El pool se dimensiona por encima del número de trabajadores para que la prueba
de concurrencia (5.6) ejerza contención real sobre la FILA de inventario y no
contención artificial sobre el pool de conexiones. Si el pool fuera más pequeño
que el número de solicitudes simultáneas, la prueba mediría la cola del pool en
vez del mecanismo de bloqueo, y no probaría nada sobre RNF-C1.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    pool_size=settings.pool_size,
    max_overflow=settings.max_overflow,
    pool_pre_ping=True,
    # search_path fijo: todo el modelo vive en el esquema `airline`.
    connect_args={"options": "-csearch_path=airline,public"},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _set_session_defaults(dbapi_connection, connection_record):  # noqa: ANN001
    with dbapi_connection.cursor() as cur:
        cur.execute("SET application_name = 'airline-booking-api'")
    dbapi_connection.commit()


def get_session() -> Iterator[Session]:
    """Dependencia de FastAPI: una sesión por solicitud."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def apply_lock_timeout(session: Session) -> None:
    """DEC-4: acota la espera por el bloqueo dentro de la transacción actual.

    `SET LOCAL` alcanza solo a la transacción en curso, así que no contamina
    la conexión cuando vuelve al pool.
    """
    session.execute(text(f"SET LOCAL lock_timeout = '{settings.lock_timeout_ms}ms'"))
