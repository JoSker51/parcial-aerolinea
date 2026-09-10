# Backend — Guía de ejecución (sección 5.6)

Implementación de la API de reservas diseñada en 5.4, sobre el modelo de 5.3.

## Requisitos

- Docker y Docker Compose
- Python 3.11+ (solo para correr las pruebas desde el anfitrión)

## Levantar el stack

```bash
docker compose up -d --build
```

Esto arranca tres servicios:

| Servicio | Puerto | Contenido |
|---|---|---|
| `oltp` | 5433 | PostgreSQL transaccional. Ejecuta `sql/01_schema.sql` y `sql/02_seed.sql` al primer arranque. |
| `olap` | 5434 | PostgreSQL analítico de la sección 5.7 (`etl/sql/olap_schema.sql`). |
| `api` | 8010 | FastAPI con 4 procesos uvicorn. |

Comprobar que quedó arriba:

```bash
curl http://localhost:8010/health
```

Documentación interactiva de la API: <http://localhost:8010/docs>

## Operaciones expuestas

| Método | Ruta | Requisito |
|---|---|---|
| `GET` | `/api/v1/flights/search` | RF-01, RF-02 |
| `POST` | `/api/v1/reservations` | RF-08, RF-10 |
| `GET` | `/api/v1/reservations/{pnr}` | RF-15 |
| `POST` | `/api/v1/reservations/{pnr}/confirm` | RF-12 |
| `POST` | `/api/v1/reservations/{pnr}/cancel` | RF-16 |
| `POST` | `/internal/jobs/expire-holds` | RF-11 |
| `GET` | `/internal/oversell-check` | RNF-C1 |

## Recorrido manual

Buscar vuelos con escala Bogotá → Santa Marta:

```bash
curl "http://localhost:8010/api/v1/flights/search?origin=BOG&destination=SMR&date=2026-09-15&cabin=ECO&passengers=1&max_stops=1"
```

Crear una reserva (sustituya `flight_instance_id` por uno de la búsqueda):

```bash
curl -X POST http://localhost:8010/api/v1/reservations -H "Content-Type: application/json" -d '{"channel":"WEB","contact_email":"ana@example.com","currency":"COP","itineraries":[{"segments":[{"flight_instance_id":1,"cabin_code":"ECO"}]}],"passengers":[{"first_name":"Ana","last_name":"Gomez","document_type":"CC","document_number":"1020304050","passenger_type":"ADT"}]}'
```

Confirmar el pago (simula el webhook del PSP):

```bash
curl -X POST http://localhost:8010/api/v1/reservations/ABC234/confirm -H "Content-Type: application/json" -d '{"psp_event_id":"evt_demo_1"}'
```

Verificar el invariante de no-sobreventa en vivo:

```bash
curl http://localhost:8010/internal/oversell-check
```

## Prueba de concurrencia (la evidencia de RNF-C1)

Escenario del enunciado llevado al supuesto SUP-5: 40 solicitudes simultáneas por la última silla.

```bash
pip install -r requirements.txt
python scripts/concurrency_stress.py --workers 40 --seats 1
```

Genera `docs/evidencia/concurrencia.md` y `.json`. Termina con código de salida distinto de cero si se produjo sobreventa, así que sirve tal cual en un pipeline de CI.

Variantes útiles para la sustentación:

```bash
python scripts/concurrency_stress.py --workers 2 --seats 1
```

```bash
python scripts/concurrency_stress.py --workers 60 --seats 5 --cabin ECO
```

## Suite automatizada

```bash
pytest tests/ -v
```

Cubre búsqueda directa y con escalas, ciclo de vida completo de la reserva, idempotencia del webhook, validación de itinerario, atomicidad multi-tramo, expiración de retenciones y cuatro escenarios de concurrencia.

## Reiniciar desde cero

```bash
docker compose down -v && docker compose up -d --build
```

## Dónde está lo importante

| Archivo | Qué contiene |
|---|---|
| [`sql/01_schema.sql`](sql/01_schema.sql) | Modelo de 5.3. La tabla `flight_inventory` y su `CHECK` de no-sobreventa. |
| [`app/services/booking.py`](app/services/booking.py) | **El bloqueo pesimista de DEC-4.** La sección crítica está marcada en el código. |
| [`app/services/search.py`](app/services/search.py) | Búsqueda directa y con escalas, con MCT por aeropuerto. |
| [`scripts/concurrency_stress.py`](scripts/concurrency_stress.py) | Generador de la evidencia de RNF-C1. |
