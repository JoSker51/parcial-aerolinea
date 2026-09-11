# Parcial 1 — Sistema de Reservas de una Aerolínea

**Big Data e Ingeniería de Datos · 2026-2**
José Santiago González · Luis Díaz

Solución completa del parcial: requisitos y diseño (5.1–5.5), implementación funcional del backend (5.6) e implementación real en AWS con ETL, catálogo de datos y proyección de costos (5.7).

---

## La entrega

| Qué | Dónde |
|---|---|
| **Documento final** | [`docs/DOCUMENTO-FINAL.docx`](docs/DOCUMENTO-FINAL.docx) — cubre las secciones 5.1 a 5.7, con la bitácora de prompts como anexo |
| **Código fuente** | Este repositorio (entregable de 5.6) |

## Documentos de trabajo

| # | Archivo | Contenido | Sección |
|---|---|---|---|
| 0 | [`00-enunciado-traducido.md`](docs/00-enunciado-traducido.md) | Enunciado íntegro traducido al español | — |
| I | [`01-contexto-y-requisitos.md`](docs/01-contexto-y-requisitos.md) | Supuestos declarados, actores, riesgos de negocio, **30 RF y 19 RNF** | 5.1, 5.2 |
| II | [`02-modelo-y-arquitectura.md`](docs/02-modelo-y-arquitectura.md) | Modelo E-R, diccionario de datos, arquitectura backend y frontend | 5.3, 5.4, 5.5 |
| III | [`03-aws-etl-catalogo-costos.md`](docs/03-aws-etl-catalogo-costos.md) | ETL, Glue Data Catalog, justificación de servicios, costos y escenario ×10 | 5.7 |
| IV | [`04-implementacion-backend.md`](docs/04-implementacion-backend.md) | Qué se implementó, evidencia de concurrencia, desviaciones declaradas | 5.6 |
| A | [`05-anexo-bitacora-prompts.md`](docs/05-anexo-bitacora-prompts.md) | Bitácora de prompts: 17 entradas de dos sesiones, con las correcciones del equipo | 4 |

Y tres documentos explicativos: [decisiones de diseño](docs/07-decisiones-de-diseno.md) (el porqué de cada elección y la alternativa descartada), [diccionario de datos](docs/08-diccionario-de-datos.md) (las 26 tablas y 4 vistas, una por una) y [arquitectura e implementación](docs/10-arquitectura-e-implementacion.md).

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

Guía detallada en [`backend/README.md`](backend/README.md). La API queda en el **puerto 8010**; para cambiarlo, `backend/docker-compose.yml`.

## Reproducir la evidencia de no-sobreventa

40 solicitudes simultáneas por la última silla:

```bash
cd backend && python scripts/concurrency_stress.py --workers 40 --seats 1
```

Y la verificación del invariante en vivo, que debe devolver siempre `violations: []`:

```bash
curl http://localhost:8010/internal/oversell-check
```

## Las cuatro decisiones que sostienen todo

| | Decisión | Por qué |
|---|---|---|
| **DEC-1** | `scheduled_flight` ≠ `flight_instance` | La capacidad es de la instancia, no del programado: si cambia la aeronave del 14 de junio, solo cambia ese día. |
| **DEC-2** | `itinerary` es una entidad propia | El pasajero compra **un viaje**, no tres vuelos. Es la unidad de cancelación y de tarifación. |
| **DEC-3** | Inventario por **conteo por cabina**, no por silla | Concentra la contención en **una fila**, que se puede bloquear de forma corta y determinista. |
| **DEC-4** | **Bloqueo pesimista** `SELECT … FOR UPDATE` | Con 40 solicitudes por 1 silla, el bloqueo optimista produce una tormenta de reintentos justo en el momento de mayor valor comercial. |

## Estado verificado

Todo lo anterior se ejecutó de verdad, no solo se escribió.

### Backend y concurrencia (local)

| Verificación | Resultado |
|---|---|
| Esquema y datos de prueba | 854 instancias de vuelo · 1.342 filas de inventario · 48 tarifas |
| Suite de pruebas | **11/11 pasan** |
| Concurrencia 2 solicitudes / 1 silla | 1 creada · 1 rechazada · **0 sobreventa** |
| Concurrencia 40 solicitudes / 1 silla | 1 creada · 39 rechazadas · **0 sobreventa** |
| Concurrencia 60 solicitudes / 5 sillas | 5 creadas · 55 rechazadas · **0 sobreventa** |

### AWS (Academy Learner Lab, `us-east-1`)

| Verificación | Resultado |
|---|---|
| Instancias RDS PostgreSQL 16.15 | `airline-oltp` (db.t3.small) y `airline-olap` (db.t3.micro) |
| **Tablas del OLTP en el Glue Data Catalog** | **30 objetos** (26 tablas + 4 vistas) |
| **Tablas del OLAP en el Glue Data Catalog** | **13 objetos** (10 tablas + 3 vistas) |
| Corrida del ETL | **`SUCCEEDED`** en 112 s con 0,0625 DPU |
| Datos en la base analítica | 157 tiquetes · 1.342 filas de ocupación · 122 de ciclo de vida |
| Las tres preguntas de negocio | Responden con datos reales sobre la base OLAP |

Reportes y capturas de consola en [`docs/evidencia/`](docs/evidencia/). Los seis obstáculos que aparecieron al desplegar —y que no son visibles leyendo el diseño— están documentados en la sección 13.8 del documento final.

> **Un resultado que vale la pena señalar:** la política tarifaria por anticipación no solo está modelada, se comporta como debe en los datos reales. La clase `P` (promo, exige 21 días de anticipación) sale con una anticipación media de compra de **38,1 días**; la `Y` (estándar, 7 días) con **16,3**; y la `B` (flexible, sin mínimo) con **3,4**. El orden esperado, medido sobre las ventas generadas.
