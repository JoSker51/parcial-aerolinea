# Evidencia — Prueba de concurrencia (RNF-C1)

**Ejecutada:** 2026-09-08 17:44:34 Hora est. Pacífico, Sudamérica  
**Mecanismo bajo prueba:** bloqueo pesimista `SELECT … FOR UPDATE` sobre `flight_inventory` (DEC-4)

## Escenario

| Parámetro | Valor |
|---|---|
| Vuelo | `AV8101` BOG→MDE 2026-09-10 |
| `flight_instance_id` | `113` |
| Cabina | `ECO` |
| Capacidad de la cabina | 150 |
| **Sillas libres al iniciar** | **5** |
| **Solicitudes simultáneas** | **60** |

## Resultado

| Métrica | Esperado | Obtenido | |
|---|---|---|---|
| Reservas creadas (201) | 5 | 5 | ✅ |
| Rechazos (409 SEAT_UNAVAILABLE) | 55 | 55 | ✅ |
| Errores inesperados | 0 | 0 | ✅ |
| **Violaciones de sobreventa** | **0** | **0** | ✅ |

### Estado final del inventario

| capacity | seats_sold | seats_held | seats_available |
|---|---|---|---|
| 150 | 145 | 5 | 0 |

### Conciliación contador vs. tramos reales

Verifica la denormalización declarada en 5.3.2: el contador materializado debe coincidir con los tramos realmente vendidos.

| | contador | tramos reales | |
|---|---|---|---|
| retenidos | 5 | 5 | ✅ |
| confirmados | 145 | 0 | |

> `seats_sold` incluye las sillas que el escenario preposicionó para dejar el vuelo casi lleno, así que su contraparte `actual_confirmed` es 0 por construcción. La fila que importa es la de retenidos.

### Latencia de las solicitudes concurrentes

| p50 | p95 | máx |
|---|---|---|
| 812.65 ms | 921.0 ms | 926.94 ms |

> El bloqueo serializa las solicitudes sobre una sola fila, así que la última en ser atendida espera a todas las anteriores. Con 60 solicitudes y transacciones de pocos milisegundos, la cola completa se drena muy por debajo del `lock_timeout` de 3 s (DEC-4).

## Detalle por solicitud

| # | HTTP | ms | PNR / código |
|---|---|---|---|
| 7 | 201 | 372.16 | `DW2X33` |
| 13 | 201 | 750.19 | `WJBYWL` |
| 32 | 201 | 288.29 | `PLJPR7` |
| 37 | 201 | 555.5 | `HKJUT3` |
| 49 | 201 | 738.33 | `584CY5` |
| 0 | 409 | 782.76 | `SEAT_UNAVAILABLE` |
| 1 | 409 | 843.57 | `SEAT_UNAVAILABLE` |
| 2 | 409 | 763.52 | `SEAT_UNAVAILABLE` |
| 3 | 409 | 848.75 | `SEAT_UNAVAILABLE` |
| 4 | 409 | 767.54 | `SEAT_UNAVAILABLE` |
| 5 | 409 | 796.89 | `SEAT_UNAVAILABLE` |
| 6 | 409 | 890.78 | `SEAT_UNAVAILABLE` |
| 8 | 409 | 865.37 | `SEAT_UNAVAILABLE` |
| 9 | 409 | 758.16 | `SEAT_UNAVAILABLE` |
| 10 | 409 | 717.37 | `SEAT_UNAVAILABLE` |
| 11 | 409 | 812.42 | `SEAT_UNAVAILABLE` |
| 12 | 409 | 856.06 | `SEAT_UNAVAILABLE` |
| 14 | 409 | 721.85 | `SEAT_UNAVAILABLE` |
| 15 | 409 | 926.94 | `SEAT_UNAVAILABLE` |
| 16 | 409 | 874.02 | `SEAT_UNAVAILABLE` |
| 17 | 409 | 804.59 | `SEAT_UNAVAILABLE` |
| 18 | 409 | 741.38 | `SEAT_UNAVAILABLE` |
| 19 | 409 | 802.32 | `SEAT_UNAVAILABLE` |
| 20 | 409 | 895.66 | `SEAT_UNAVAILABLE` |
| 21 | 409 | 923.45 | `SEAT_UNAVAILABLE` |
| 22 | 409 | 853.32 | `SEAT_UNAVAILABLE` |
| 23 | 409 | 780.6 | `SEAT_UNAVAILABLE` |
| 24 | 409 | 766.93 | `SEAT_UNAVAILABLE` |
| 25 | 409 | 846.27 | `SEAT_UNAVAILABLE` |
| 26 | 409 | 774.13 | `SEAT_UNAVAILABLE` |
| 27 | 409 | 921.0 | `SEAT_UNAVAILABLE` |
| 28 | 409 | 841.24 | `SEAT_UNAVAILABLE` |
| 29 | 409 | 746.83 | `SEAT_UNAVAILABLE` |
| 30 | 409 | 872.16 | `SEAT_UNAVAILABLE` |
| 31 | 409 | 818.04 | `SEAT_UNAVAILABLE` |
| 33 | 409 | 903.66 | `SEAT_UNAVAILABLE` |
| 34 | 409 | 769.16 | `SEAT_UNAVAILABLE` |
| 35 | 409 | 782.8 | `SEAT_UNAVAILABLE` |
| 36 | 409 | 812.89 | `SEAT_UNAVAILABLE` |
| 38 | 409 | 868.1 | `SEAT_UNAVAILABLE` |
| 39 | 409 | 861.04 | `SEAT_UNAVAILABLE` |
| 40 | 409 | 863.41 | `SEAT_UNAVAILABLE` |
| 41 | 409 | 859.97 | `SEAT_UNAVAILABLE` |
| 42 | 409 | 755.07 | `SEAT_UNAVAILABLE` |
| 43 | 409 | 904.2 | `SEAT_UNAVAILABLE` |
| 44 | 409 | 775.55 | `SEAT_UNAVAILABLE` |
| 45 | 409 | 703.67 | `SEAT_UNAVAILABLE` |
| 46 | 409 | 889.13 | `SEAT_UNAVAILABLE` |
| 47 | 409 | 884.97 | `SEAT_UNAVAILABLE` |
| 48 | 409 | 757.09 | `SEAT_UNAVAILABLE` |
| 50 | 409 | 876.66 | `SEAT_UNAVAILABLE` |
| 51 | 409 | 798.07 | `SEAT_UNAVAILABLE` |
| 52 | 409 | 824.28 | `SEAT_UNAVAILABLE` |
| 53 | 409 | 818.02 | `SEAT_UNAVAILABLE` |
| 54 | 409 | 883.75 | `SEAT_UNAVAILABLE` |
| 55 | 409 | 855.48 | `SEAT_UNAVAILABLE` |
| 56 | 409 | 804.11 | `SEAT_UNAVAILABLE` |
| 57 | 409 | 798.06 | `SEAT_UNAVAILABLE` |
| 58 | 409 | 846.29 | `SEAT_UNAVAILABLE` |
| 59 | 409 | 797.82 | `SEAT_UNAVAILABLE` |

## Veredicto

**PASA** — el mecanismo de bloqueo pesimista previene la sobreventa bajo 60 solicitudes simultáneas por 5 silla(s).
