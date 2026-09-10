#!/usr/bin/env python
"""Prueba de concurrencia — evidencia de RNF-C1.

Escenario exacto del enunciado, llevado al extremo: N pasajeros intentan
reservar **al mismo tiempo** las últimas K sillas de un vuelo.

    Éxitos esperados = K
    Rechazos (409 SEAT_UNAVAILABLE) esperados = N - K
    Sobreventa tolerada = 0

Por qué la prueba es honesta y no un montaje:

* Las solicitudes salen de **hilos reales del sistema operativo**, no de
  corrutinas cooperativas: hay paralelismo de verdad.
* Se sincronizan con una `Barrier`, así que todas salen en la misma ventana de
  microsegundos. Sin la barrera, la primera terminaría antes de que la segunda
  empezara y la prueba no probaría nada.
* Van por **HTTP contra la API real**, con 4 procesos uvicorn detrás: la
  contención atraviesa procesos distintos, no hilos del mismo intérprete.
* La aserción final no confía en los códigos de respuesta: consulta
  directamente `v_oversell_check` en la base de datos.

Uso:
    python scripts/concurrency_stress.py --workers 40 --seats 1
    python scripts/concurrency_stress.py --workers 60 --seats 5 --cabin ECO
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg

# La consola de Windows usa cp1252 por defecto y no puede codificar los
# símbolos del reporte. Sin esto el script muere por UnicodeEncodeError al
# imprimir, no por un fallo de la prueba.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_API = "http://localhost:8010"
DEFAULT_DSN = "postgresql://airline:airline@localhost:5433/airline"


# ---------------------------------------------------------------------
# Preparación del escenario
# ---------------------------------------------------------------------

def prepare_scarce_flight(dsn: str, *, cabin: str, seats_left: int) -> dict:
    """Deja exactamente `seats_left` sillas libres en un vuelo futuro.

    Se elige un vuelo a >= 2 días para que ninguna regla de anticipación
    (`fare_rule.min_advance_days`) deje el tramo sin tarifa elegible.
    """
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SET search_path TO airline, public")
        cur.execute(
            """
            SELECT fi.flight_instance_id, sf.flight_number, r.origin_iata,
                   r.destination_iata, fi.flight_date, inv.capacity
              FROM flight_instance  fi
              JOIN scheduled_flight sf  ON sf.scheduled_flight_id = fi.scheduled_flight_id
              JOIN route            r   ON r.route_id = sf.route_id
              JOIN flight_inventory inv ON inv.flight_instance_id = fi.flight_instance_id
                                       AND inv.cabin_code = %s
             WHERE fi.flight_date >= CURRENT_DATE + 2
               AND fi.status = 'SCHEDULED'
             ORDER BY fi.flight_date, fi.flight_instance_id
             LIMIT 1
            """,
            (cabin,),
        )
        row = cur.fetchone()
        if row is None:
            sys.exit(f"No hay vuelos con inventario en cabina {cabin}. ¿Corrió el seed?")

        fid, flight_number, origin, destination, flight_date, capacity = row

        # Estado conocido antes de medir: sin reservas, inventario en cero.
        # Se usa TRUNCATE porque `payment`, `ticket` y `reservation_event`
        # referencian `reservation` sin ON DELETE CASCADE (en producción una
        # reserva no se borra, se cancela) y porque `reservation_event` tiene
        # un trigger que prohíbe DELETE (RNF-A2), que TRUNCATE no dispara.
        cur.execute(
            "TRUNCATE reservation_event, payment_event, refund, payment, "
            "seat_assignment, ancillary, ticket, itinerary_segment, itinerary, "
            "reservation_passenger, reservation RESTART IDENTITY CASCADE"
        )
        cur.execute(
            "UPDATE flight_inventory SET seats_sold = 0, seats_held = 0, "
            "version = version + 1"
        )
        cur.execute(
            """
            UPDATE flight_inventory
               SET seats_sold = %s, seats_held = 0, version = version + 1
             WHERE flight_instance_id = %s AND cabin_code = %s
            """,
            (capacity - seats_left, fid, cabin),
        )

    return {
        "flight_instance_id": fid,
        "flight_number": flight_number,
        "origin": origin,
        "destination": destination,
        "flight_date": flight_date.isoformat() if isinstance(flight_date, date) else str(flight_date),
        "capacity": capacity,
        "seats_left": seats_left,
        "cabin": cabin,
    }


def verify_invariant(dsn: str, flight_instance_id: int, cabin: str) -> dict:
    """Aserción dura de RNF-C1, directamente contra el motor."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SET search_path TO airline, public")
        cur.execute("SELECT * FROM v_oversell_check")
        violations = cur.fetchall()

        cur.execute(
            """SELECT capacity, seats_sold, seats_held,
                      FLOOR(capacity * oversell_factor) - seats_sold - seats_held
                 FROM flight_inventory
                WHERE flight_instance_id = %s AND cabin_code = %s""",
            (flight_instance_id, cabin),
        )
        capacity, sold, held, available = cur.fetchone()

        # Contraste contador materializado vs. tramos reales (5.3.2).
        cur.execute(
            """SELECT seats_sold, seats_held, actual_confirmed, actual_held
                 FROM v_inventory_reconciliation
                WHERE flight_instance_id = %s AND cabin_code = %s""",
            (flight_instance_id, cabin),
        )
        rec = cur.fetchone()

    return {
        "oversell_violations": len(violations),
        "capacity": capacity,
        "seats_sold": sold,
        "seats_held": held,
        "seats_available": int(available),
        "reconciliation": {
            "counter_sold": rec[0], "counter_held": rec[1],
            "actual_confirmed": rec[2] or 0, "actual_held": rec[3] or 0,
        } if rec else None,
    }


# ---------------------------------------------------------------------
# El ataque concurrente
# ---------------------------------------------------------------------

def build_payload(scenario: dict, worker_id: int) -> dict:
    return {
        "channel": "WEB",
        "contact_email": f"stress{worker_id}@example.com",
        "currency": "COP",
        "itineraries": [
            {"segments": [{
                "flight_instance_id": scenario["flight_instance_id"],
                "cabin_code": scenario["cabin"],
            }]}
        ],
        "passengers": [{
            "first_name": f"Test{worker_id}",
            "last_name": "Concurrencia",
            "document_type": "CC",
            # Documento único por worker: si dos compartieran documento, el
            # ON CONFLICT de `passenger` introduciría contención ajena a la
            # que se quiere medir.
            "document_number": f"CC{worker_id:08d}",
            "passenger_type": "ADT",
        }],
    }


def run_attack(api: str, scenario: dict, workers: int, timeout: float) -> list[dict]:
    barrier = threading.Barrier(workers)
    results: list[dict] = [None] * workers  # type: ignore[list-item]

    def attempt(worker_id: int) -> None:
        payload = build_payload(scenario, worker_id)
        client = httpx.Client(timeout=timeout)
        try:
            # Todos los hilos esperan aquí: la salida es simultánea.
            barrier.wait()
            started = time.perf_counter()
            response = client.post(f"{api}/api/v1/reservations", json=payload)
            elapsed = (time.perf_counter() - started) * 1000
            body = response.json() if response.content else {}
            results[worker_id] = {
                "worker": worker_id,
                "status_code": response.status_code,
                "elapsed_ms": round(elapsed, 2),
                "pnr": body.get("pnr"),
                "code": body.get("code"),
                "message": body.get("message"),
            }
        except Exception as exc:  # noqa: BLE001
            results[worker_id] = {
                "worker": worker_id, "status_code": -1,
                "elapsed_ms": None, "error": repr(exc),
            }
        finally:
            client.close()

    threads = [
        threading.Thread(target=attempt, args=(i,), name=f"pax-{i}")
        for i in range(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# ---------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------

def write_report(path: Path, scenario: dict, results: list[dict],
                 invariant: dict, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone()

    lines = [
        "# Evidencia — Prueba de concurrencia (RNF-C1)",
        "",
        f"**Ejecutada:** {now:%Y-%m-%d %H:%M:%S %Z}  ",
        f"**Mecanismo bajo prueba:** bloqueo pesimista `SELECT … FOR UPDATE` "
        f"sobre `flight_inventory` (DEC-4)",
        "",
        "## Escenario",
        "",
        "| Parámetro | Valor |",
        "|---|---|",
        f"| Vuelo | `{scenario['flight_number']}` "
        f"{scenario['origin']}→{scenario['destination']} {scenario['flight_date']} |",
        f"| `flight_instance_id` | `{scenario['flight_instance_id']}` |",
        f"| Cabina | `{scenario['cabin']}` |",
        f"| Capacidad de la cabina | {scenario['capacity']} |",
        f"| **Sillas libres al iniciar** | **{scenario['seats_left']}** |",
        f"| **Solicitudes simultáneas** | **{summary['workers']}** |",
        "",
        "## Resultado",
        "",
        "| Métrica | Esperado | Obtenido | |",
        "|---|---|---|---|",
        f"| Reservas creadas (201) | {scenario['seats_left']} | "
        f"{summary['created']} | {'✅' if summary['created'] == scenario['seats_left'] else '❌'} |",
        f"| Rechazos (409 SEAT_UNAVAILABLE) | "
        f"{summary['workers'] - scenario['seats_left']} | {summary['rejected']} | "
        f"{'✅' if summary['rejected'] == summary['workers'] - scenario['seats_left'] else '❌'} |",
        f"| Errores inesperados | 0 | {summary['errors']} | "
        f"{'✅' if summary['errors'] == 0 else '❌'} |",
        f"| **Violaciones de sobreventa** | **0** | "
        f"**{invariant['oversell_violations']}** | "
        f"{'✅' if invariant['oversell_violations'] == 0 else '❌'} |",
        "",
        "### Estado final del inventario",
        "",
        "| capacity | seats_sold | seats_held | seats_available |",
        "|---|---|---|---|",
        f"| {invariant['capacity']} | {invariant['seats_sold']} | "
        f"{invariant['seats_held']} | {invariant['seats_available']} |",
        "",
    ]

    if invariant.get("reconciliation"):
        rec = invariant["reconciliation"]
        ok = rec["counter_held"] == rec["actual_held"]
        lines += [
            "### Conciliación contador vs. tramos reales",
            "",
            "Verifica la denormalización declarada en 5.3.2: el contador "
            "materializado debe coincidir con los tramos realmente vendidos.",
            "",
            "| | contador | tramos reales | |",
            "|---|---|---|---|",
            f"| retenidos | {rec['counter_held']} | {rec['actual_held']} | "
            f"{'✅' if ok else '❌'} |",
            f"| confirmados | {rec['counter_sold']} | {rec['actual_confirmed']} | |",
            "",
            "> `seats_sold` incluye las sillas que el escenario preposicionó "
            "para dejar el vuelo casi lleno, así que su contraparte "
            "`actual_confirmed` es 0 por construcción. La fila que importa es "
            "la de retenidos.",
            "",
        ]

    lat = summary["latency"]
    lines += [
        "### Latencia de las solicitudes concurrentes",
        "",
        "| p50 | p95 | máx |",
        "|---|---|---|",
        f"| {lat['p50']} ms | {lat['p95']} ms | {lat['max']} ms |",
        "",
        "> El bloqueo serializa las solicitudes sobre una sola fila, así que la "
        "última en ser atendida espera a todas las anteriores. Con "
        f"{summary['workers']} solicitudes y transacciones de pocos "
        "milisegundos, la cola completa se drena muy por debajo del "
        "`lock_timeout` de 3 s (DEC-4).",
        "",
        "## Detalle por solicitud",
        "",
        "| # | HTTP | ms | PNR / código |",
        "|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: (x["status_code"] != 201, x["worker"])):
        detail = r.get("pnr") or r.get("code") or r.get("error", "")
        lines.append(
            f"| {r['worker']} | {r['status_code']} | {r.get('elapsed_ms', '—')} "
            f"| `{detail}` |"
        )

    verdict = (
        "PASA" if (
            summary["created"] == scenario["seats_left"]
            and invariant["oversell_violations"] == 0
            and summary["errors"] == 0
        ) else "FALLA"
    )
    lines += [
        "",
        "## Veredicto",
        "",
        f"**{verdict}** — "
        + (
            "el mecanismo de bloqueo pesimista previene la sobreventa bajo "
            f"{summary['workers']} solicitudes simultáneas por "
            f"{scenario['seats_left']} silla(s)."
            if verdict == "PASA"
            else "revisar el detalle: el invariante de RNF-C1 no se sostuvo."
        ),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--workers", type=int, default=40,
                        help="Solicitudes simultáneas (SUP-5 asume 40).")
    parser.add_argument("--seats", type=int, default=1,
                        help="Sillas libres al iniciar.")
    parser.add_argument("--cabin", default="BUS", choices=["ECO", "BUS"])
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--report", default="../../docs/evidencia/concurrencia.md")
    args = parser.parse_args()

    print(f"→ Preparando escenario: {args.seats} silla(s) libre(s) en {args.cabin}…")
    scenario = prepare_scarce_flight(args.dsn, cabin=args.cabin, seats_left=args.seats)
    print(f"  Vuelo {scenario['flight_number']} "
          f"{scenario['origin']}→{scenario['destination']} "
          f"({scenario['flight_date']}), capacidad {scenario['capacity']}")

    print(f"→ Lanzando {args.workers} solicitudes simultáneas…")
    results = run_attack(args.api, scenario, args.workers, args.timeout)

    created = sum(1 for r in results if r["status_code"] == 201)
    rejected = sum(
        1 for r in results if r["status_code"] == 409 and r.get("code") == "SEAT_UNAVAILABLE"
    )
    errors = sum(1 for r in results if r["status_code"] not in (201, 409))
    latencies = [r["elapsed_ms"] for r in results if r.get("elapsed_ms") is not None]
    latencies.sort()

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(int(len(latencies) * p), len(latencies) - 1)
        return round(latencies[idx], 2)

    summary = {
        "workers": args.workers,
        "created": created,
        "rejected": rejected,
        "errors": errors,
        "latency": {
            "p50": round(statistics.median(latencies), 2) if latencies else 0,
            "p95": pct(0.95),
            "max": round(max(latencies), 2) if latencies else 0,
        },
    }

    invariant = verify_invariant(
        args.dsn, scenario["flight_instance_id"], scenario["cabin"]
    )

    print()
    print(f"  Creadas (201) ......... {created}   (esperado {args.seats})")
    print(f"  Rechazadas (409) ...... {rejected}   "
          f"(esperado {args.workers - args.seats})")
    print(f"  Errores inesperados ... {errors}   (esperado 0)")
    print(f"  Sobreventa ............ {invariant['oversell_violations']}   (esperado 0)")
    print(f"  Inventario final ...... capacity={invariant['capacity']} "
          f"sold={invariant['seats_sold']} held={invariant['seats_held']} "
          f"available={invariant['seats_available']}")

    report_path = (Path(__file__).parent / args.report).resolve()
    write_report(report_path, scenario, results, invariant, summary)
    json_path = report_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {"scenario": scenario, "summary": summary,
             "invariant": invariant, "results": results},
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n→ Evidencia escrita en {report_path}")

    ok = created == args.seats and errors == 0 and invariant["oversell_violations"] == 0
    print("\n" + ("✅ RNF-C1 SE CUMPLE" if ok else "❌ RNF-C1 VIOLADO"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
