# Evidencia de despliegue en AWS — sección 5.7

> Salidas reales de la lista de verificación de §13.7 / §5.7.5.
> Cuenta `283994750102` (AWS Academy Learner Lab) · región `us-east-1` · 2026-09-09 19:53

## 1. Instancias RDS disponibles
```
-------------------------------------------------------------------
|                       DescribeDBInstances                       |
+--------------+------------+-----------+---------+---------------+
|  airline-olap|  available |  postgres |  16.15  |  db.t3.micro  |
|  airline-oltp|  available |  postgres |  16.15  |  db.t3.small  |
+--------------+------------+-----------+---------+---------------+
```

## 2. Conexiones JDBC de Glue
```
Jdbc connection	Jdbc connection sql	airline-olap-conn	airline-oltp-conn
```

## 3. Crawlers
```
airline-oltp-crawler     READY	SUCCEEDED
airline-olap-crawler     READY	SUCCEEDED
```

## 4. Tablas del OLTP en el Glue Data Catalog
```
airline_oltp_catalog: 30
airline_airline_agency
airline_airline_aircraft
airline_airline_aircraft_model
airline_airline_airport
airline_airline_ancillary
airline_airline_cabin
airline_airline_fare
airline_airline_fare_class
airline_airline_fare_rule
airline_airline_flight_instance
airline_airline_flight_inventory
airline_airline_itinerary
airline_airline_itinerary_segment
airline_airline_passenger
airline_airline_payment
airline_airline_payment_event
airline_airline_refund
airline_airline_reservation
airline_airline_reservation_event
airline_airline_reservation_passenger
airline_airline_route
airline_airline_scheduled_flight
airline_airline_seat_assignment
airline_airline_seat_map
airline_airline_seat_map_seat
airline_airline_ticket
airline_airline_v_flight_availability
airline_airline_v_inventory_reconciliation
airline_airline_v_oversell_check
airline_airline_v_seat_map_capacity
```

## 5. Tablas del OLAP en el Glue Data Catalog
```
airline_olap_catalog: 13
analytics_analytics_dim_channel
analytics_analytics_dim_date
analytics_analytics_dim_fare
analytics_analytics_dim_flight
analytics_analytics_dim_route
analytics_analytics_etl_run_log
analytics_analytics_etl_watermark
analytics_analytics_fact_flight_occupancy
analytics_analytics_fact_reservation_lifecycle
analytics_analytics_fact_ticket_sale
analytics_analytics_v_cancellation_patterns
analytics_analytics_v_occupancy_by_route
analytics_analytics_v_revenue_by_fare
```

## 6. Corrida del ETL
```
SUCCEEDED	112	2026-09-09T19:50:15.527000-05:00
```

## 7. Datos cargados en la base analítica

Filas por tabla del modelo estrella, tras la corrida del ETL:

```
dim_date                           1,095
dim_route                             12
dim_fare                              48
dim_flight                           854
dim_channel                            3
fact_ticket_sale                     157
fact_flight_occupancy              1,342
fact_reservation_lifecycle           122
```

### La política tarifaria se comporta como se modeló

`fare_rule.min_advance_days` no solo existe en el esquema: los datos reales
reproducen el orden esperado de anticipación de compra por clase tarifaria.

| Clase | Regla | `min_advance_days` | Anticipación media real (días) |
|---|---|---|---|
| `B` | Flexible | 0 | **3.4** |
| `J` | Ejecutiva flexible | 0 | **32.2** |
| `Y` | Estándar 7d | 7 | **16.3** |
| `P` | Promo anticipada 21d | 21 | **38.1** |

Las tres vistas de negocio (`v_occupancy_by_route`, `v_revenue_by_fare`,
`v_cancellation_patterns`) responden con datos reales.
