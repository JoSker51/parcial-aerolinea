# Evidencia — Prueba de concurrencia (RNF-C1)

**Ejecutada:** 2026-09-08 17:44:15 Hora est. Pacífico, Sudamérica  
**Mecanismo bajo prueba:** bloqueo pesimista `SELECT … FOR UPDATE` sobre `flight_inventory` (DEC-4)

## Escenario

| Parámetro | Valor |
|---|---|
| Vuelo | `AV8101` BOG→MDE 2026-09-10 |
| `flight_instance_id` | `113` |
| Cabina | `BUS` |
| Capacidad de la cabina | 12 |
| **Sillas libres al iniciar** | **1** |
| **Solicitudes simultáneas** | **40** |

## Resultado

| Métrica | Esperado | Obtenido | |
|---|---|---|---|
| Reservas creadas (201) | 1 | 1 | ✅ |
| Rechazos (409 SEAT_UNAVAILABLE) | 39 | 39 | ✅ |
| Errores inesperados | 0 | 0 | ✅ |
| **Violaciones de sobreventa** | **0** | **0** | ✅ |

### Estado final del inventario

| capacity | seats_sold | seats_held | seats_available |
|---|---|---|---|
| 12 | 11 | 1 | 0 |

### Conciliación contador vs. tramos reales

Verifica la denormalización declarada en 5.3.2: el contador materializado debe coincidir con los tramos realmente vendidos.

| | contador | tramos reales | |
|---|---|---|---|
| retenidos | 1 | 1 | ✅ |
| confirmados | 11 | 0 | |

> `seats_sold` incluye las sillas que el escenario preposicionó para dejar el vuelo casi lleno, así que su contraparte `actual_confirmed` es 0 por construcción. La fila que importa es la de retenidos.

### Latencia de las solicitudes concurrentes

| p50 | p95 | máx |
|---|---|---|
| 334.67 ms | 428.99 ms | 444.47 ms |

> El bloqueo serializa las solicitudes sobre una sola fila, así que la última en ser atendida espera a todas las anteriores. Con 40 solicitudes y transacciones de pocos milisegundos, la cola completa se drena muy por debajo del `lock_timeout` de 3 s (DEC-4).

## Detalle por solicitud

| # | HTTP | ms | PNR / código |
|---|---|---|---|
| 18 | 201 | 330.51 | `LCE3CG` |
| 0 | 409 | 282.36 | `SEAT_UNAVAILABLE` |
| 1 | 409 | 283.86 | `SEAT_UNAVAILABLE` |
| 2 | 409 | 412.31 | `SEAT_UNAVAILABLE` |
| 3 | 409 | 379.75 | `SEAT_UNAVAILABLE` |
| 4 | 409 | 306.43 | `SEAT_UNAVAILABLE` |
| 5 | 409 | 264.42 | `SEAT_UNAVAILABLE` |
| 6 | 409 | 375.37 | `SEAT_UNAVAILABLE` |
| 7 | 409 | 444.47 | `SEAT_UNAVAILABLE` |
| 8 | 409 | 265.3 | `SEAT_UNAVAILABLE` |
| 9 | 409 | 338.84 | `SEAT_UNAVAILABLE` |
| 10 | 409 | 397.22 | `SEAT_UNAVAILABLE` |
| 11 | 409 | 303.07 | `SEAT_UNAVAILABLE` |
| 12 | 409 | 312.21 | `SEAT_UNAVAILABLE` |
| 13 | 409 | 285.53 | `SEAT_UNAVAILABLE` |
| 14 | 409 | 417.03 | `SEAT_UNAVAILABLE` |
| 15 | 409 | 268.0 | `SEAT_UNAVAILABLE` |
| 16 | 409 | 410.4 | `SEAT_UNAVAILABLE` |
| 17 | 409 | 361.01 | `SEAT_UNAVAILABLE` |
| 19 | 409 | 312.0 | `SEAT_UNAVAILABLE` |
| 20 | 409 | 362.31 | `SEAT_UNAVAILABLE` |
| 21 | 409 | 419.17 | `SEAT_UNAVAILABLE` |
| 22 | 409 | 314.49 | `SEAT_UNAVAILABLE` |
| 23 | 409 | 271.41 | `SEAT_UNAVAILABLE` |
| 24 | 409 | 390.78 | `SEAT_UNAVAILABLE` |
| 25 | 409 | 428.99 | `SEAT_UNAVAILABLE` |
| 26 | 409 | 265.02 | `SEAT_UNAVAILABLE` |
| 27 | 409 | 410.77 | `SEAT_UNAVAILABLE` |
| 28 | 409 | 324.06 | `SEAT_UNAVAILABLE` |
| 29 | 409 | 327.11 | `SEAT_UNAVAILABLE` |
| 30 | 409 | 311.02 | `SEAT_UNAVAILABLE` |
| 31 | 409 | 395.15 | `SEAT_UNAVAILABLE` |
| 32 | 409 | 302.72 | `SEAT_UNAVAILABLE` |
| 33 | 409 | 369.19 | `SEAT_UNAVAILABLE` |
| 34 | 409 | 278.01 | `SEAT_UNAVAILABLE` |
| 35 | 409 | 407.12 | `SEAT_UNAVAILABLE` |
| 36 | 409 | 285.34 | `SEAT_UNAVAILABLE` |
| 37 | 409 | 412.91 | `SEAT_UNAVAILABLE` |
| 38 | 409 | 388.47 | `SEAT_UNAVAILABLE` |
| 39 | 409 | 395.96 | `SEAT_UNAVAILABLE` |

## Veredicto

**PASA** — el mecanismo de bloqueo pesimista previene la sobreventa bajo 40 solicitudes simultáneas por 1 silla(s).
