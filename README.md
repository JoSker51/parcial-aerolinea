# Parcial 1 — Sistema de Reservas de una Aerolínea

**Big Data e Ingeniería de Datos · 2026-2**

Solución completa del parcial: documento de diseño (5.1–5.5), implementación del backend (5.6) e implementación en AWS con ETL, catálogo de datos y costos (5.7).

---

## Documento

| # | Archivo | Contenido | Sección del enunciado |
|---|---|---|---|
| 0 | [`docs/00-enunciado-traducido.md`](docs/00-enunciado-traducido.md) | Enunciado íntegro traducido al español | — |
| I | [`docs/01-contexto-y-requisitos.md`](docs/01-contexto-y-requisitos.md) | Supuestos declarados, actores, riesgos de negocio, **30 RF y 19 RNF** | 5.1, 5.2 |
| II | [`docs/02-modelo-y-arquitectura.md`](docs/02-modelo-y-arquitectura.md) | Modelo E-R con diagrama Crow's Foot, diccionario de datos, arquitectura backend y frontend | 5.3, 5.4, 5.5 |
| III | [`docs/03-aws-etl-catalogo-costos.md`](docs/03-aws-etl-catalogo-costos.md) | ETL, Glue Data Catalog, justificación de servicios, proyección de costos y escenario ×10 | 5.7 |
| IV | [`docs/04-implementacion-backend.md`](docs/04-implementacion-backend.md) | Qué se implementó, evidencia de concurrencia, **desviaciones declaradas** | 5.6 |
| A | [`docs/05-anexo-bitacora-prompts.md`](docs/05-anexo-bitacora-prompts.md) | Bitácora de prompts y correcciones del equipo | 4 |

## Código

```
backend/
  sql/01_schema.sql        Modelo de 5.3 · 26 tablas · la CHECK de no-sobreventa
  sql/02_seed.sql          Red de 6 aeropuertos, 14 vuelos programados, 60 días
  app/services/booking.py  ← EL BLOQUEO PESIMISTA (DEC-4). Lo más importante.
  app/services/search.py   Búsqueda directa y con escalas, con MCT por aeropuerto
  app/main.py              7 endpoints + verificación en vivo del invariante
  tests/                   Suite pytest, 4 escenarios de concurrencia
  scripts/                 Generador de la evidencia de RNF-C1
etl/
  glue_job_oltp_to_olap.py Job de Glue Python Shell · incremental e idempotente
  sql/olap_schema.sql      Esquema en estrella: 5 dimensiones, 3 hechos, 3 vistas
  infra/setup_aws.sh       Provisión reproducible del entorno AWS
```

## Arrancar

```bash
cd backend && docker compose up -d --build
```

```bash
curl http://localhost:8010/health
```

Guía detallada en [`backend/README.md`](backend/README.md).

## Reproducir la evidencia de no-sobreventa

```bash
cd backend && python scripts/concurrency_stress.py --workers 40 --seats 1
```

## Las cuatro decisiones que sostienen todo

| | Decisión | Por qué |
|---|---|---|
| **DEC-1** | `scheduled_flight` ≠ `flight_instance` | La capacidad es de la instancia, no del programado: si cambia la aeronave del 14 de junio, solo cambia ese día. |
| **DEC-2** | `itinerary` es una entidad propia | El pasajero compra **un viaje**, no tres vuelos. Es la unidad de cancelación y de tarifación. |
| **DEC-3** | Inventario por **conteo por cabina**, no por silla | Concentra la contención en **una fila**, que se puede bloquear de forma corta y determinista. |
| **DEC-4** | **Bloqueo pesimista** `SELECT … FOR UPDATE` | Con 40 solicitudes por 1 silla, el bloqueo optimista produce una tormenta de reintentos justo en el momento de mayor valor comercial. |

## Estado verificado (2026-09-02)

Todo lo anterior se ejecutó de verdad, no solo se escribió:

| Verificación | Resultado |
|---|---|
| Esquema y datos de prueba | 854 instancias de vuelo · 1.342 filas de inventario · 48 tarifas |
| Suite de pruebas | **11/11 pasan** en 25,4 s |
| Concurrencia 2 solicitudes / 1 silla | 1 creada · 1 rechazada · **0 sobreventa** |
| Concurrencia 40 solicitudes / 1 silla | 1 creada · 39 rechazadas · **0 sobreventa** |
| Concurrencia 60 solicitudes / 5 sillas | 5 creadas · 55 rechazadas · **0 sobreventa** |
| ETL OLTP → OLAP | 2.435 filas cargadas en el modelo estrella en 0,8 s |
| Las tres preguntas de negocio | Responden con datos reales sobre la base OLAP |

Reportes en [`docs/evidencia/`](docs/evidencia/).

> La API queda expuesta en el **puerto 8010** (el 8000 estaba ocupado por otro proyecto en esta máquina). Si en la tuya está libre, cámbialo en `backend/docker-compose.yml`.

## Pendiente para el equipo

- [ ] Integrar la **restricción confidencial de la sección 3** en los tres puntos preparados (ver `PE-0` en la Parte I).
- [ ] Completar los datos del equipo en la portada de la Parte I.
- [ ] Ampliar la bitácora de prompts con los propios de cada integrante.
- [ ] Publicar el código en GitHub y añadir el enlace al documento (entregable de 5.6).
- [ ] Ejecutar `etl/infra/setup_aws.sh` en el Learner Lab y adjuntar las capturas de la lista de verificación de 5.7.5.
