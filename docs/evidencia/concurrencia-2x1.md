# Evidencia — Prueba de concurrencia (RNF-C1)

**Ejecutada:** 2026-09-08 17:44:02 Hora est. Pacífico, Sudamérica  
**Mecanismo bajo prueba:** bloqueo pesimista `SELECT … FOR UPDATE` sobre `flight_inventory` (DEC-4)

## Escenario

| Parámetro | Valor |
|---|---|
| Vuelo | `AV8101` BOG→MDE 2026-09-10 |
| `flight_instance_id` | `113` |
| Cabina | `BUS` |
| Capacidad de la cabina | 12 |
| **Sillas libres al iniciar** | **1** |
| **Solicitudes simultáneas** | **2** |

## Resultado

| Métrica | Esperado | Obtenido | |
|---|---|---|---|
| Reservas creadas (201) | 1 | 1 | ✅ |
| Rechazos (409 SEAT_UNAVAILABLE) | 1 | 1 | ✅ |
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
| 65.23 ms | 70.95 ms | 70.95 ms |

> El bloqueo serializa las solicitudes sobre una sola fila, así que la última en ser atendida espera a todas las anteriores. Con 2 solicitudes y transacciones de pocos milisegundos, la cola completa se drena muy por debajo del `lock_timeout` de 3 s (DEC-4).

## Detalle por solicitud

| # | HTTP | ms | PNR / código |
|---|---|---|---|
| 0 | 201 | 70.95 | `KR9KBV` |
| 1 | 409 | 59.52 | `SEAT_UNAVAILABLE` |

## Veredicto

**PASA** — el mecanismo de bloqueo pesimista previene la sobreventa bajo 2 solicitudes simultáneas por 1 silla(s).
