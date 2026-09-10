-- =====================================================================
--  Datos de prueba — Aerolínea Regional
--  Red reducida (6 aeropuertos, 8 rutas) suficiente para ejercitar:
--    - búsqueda directa y con 1 escala (RF-01, RF-02)
--    - reserva multi-tramo (RF-08)
--    - el escenario de concurrencia de RNF-C1
-- =====================================================================

SET search_path TO airline, public;

-- --------------------------------------------------------------- Cabinas
INSERT INTO cabin (cabin_code, name, boarding_priority) VALUES
  ('BUS', 'Ejecutiva', 1),
  ('ECO', 'Económica', 2);

-- ----------------------------------------------------------- Aeropuertos
INSERT INTO airport (iata_code, icao_code, name, city, country_code, timezone, min_connection_min) VALUES
  ('BOG', 'SKBO', 'El Dorado',                 'Bogotá',       'CO', 'America/Bogota', 60),
  ('MDE', 'SKRG', 'José María Córdova',        'Rionegro',     'CO', 'America/Bogota', 45),
  ('CTG', 'SKCG', 'Rafael Núñez',              'Cartagena',    'CO', 'America/Bogota', 40),
  ('CLO', 'SKCL', 'Alfonso Bonilla Aragón',    'Cali',         'CO', 'America/Bogota', 45),
  ('BAQ', 'SKBQ', 'Ernesto Cortissoz',         'Barranquilla', 'CO', 'America/Bogota', 40),
  ('SMR', 'SKSM', 'Simón Bolívar',             'Santa Marta',  'CO', 'America/Bogota', 35);

-- ---------------------------------------------------------------- Rutas
INSERT INTO route (origin_iata, destination_iata, distance_km) VALUES
  ('BOG','MDE', 240), ('MDE','BOG', 240),
  ('BOG','CTG', 660), ('CTG','BOG', 660),
  ('MDE','CTG', 480), ('CTG','MDE', 480),
  ('BOG','CLO', 300), ('CLO','BOG', 300),
  ('CTG','SMR', 190), ('SMR','CTG', 190),
  ('BOG','BAQ', 700), ('BAQ','BOG', 700);

-- ---------------------------------------------------------------- Flota
INSERT INTO aircraft_model (model_code, manufacturer, name) VALUES
  ('AT72', 'ATR',     'ATR 72-600'),
  ('A320', 'Airbus',  'A320-200'),
  ('A20N', 'Airbus',  'A320neo');

INSERT INTO seat_map (seat_map_id, model_code, name) OVERRIDING SYSTEM VALUE VALUES
  (1, 'AT72', 'ATR72 monoclase 68Y'),
  (2, 'A320', 'A320 12J/150Y'),
  (3, 'A20N', 'A320neo 12J/162Y');
SELECT setval('seat_map_seat_map_id_seq', 3, true);

-- Generación de mapas de sillas.
-- ATR-72: 17 filas x 4 sillas (A,C,D,F), todo económica = 68.
INSERT INTO seat_map_seat (seat_map_id, seat_number, cabin_code, row_num, seat_letter, is_window, is_aisle, is_exit_row)
SELECT 1, r::text || l, 'ECO', r, l,
       l IN ('A','F'), l IN ('C','D'), r = 9
FROM generate_series(1, 17) r
CROSS JOIN unnest(ARRAY['A','C','D','F']) l;

-- A320: filas 1-3 ejecutiva (4 sillas/fila = 12J), filas 10-34 económica (6/fila = 150Y).
INSERT INTO seat_map_seat (seat_map_id, seat_number, cabin_code, row_num, seat_letter, is_window, is_aisle, is_exit_row)
SELECT 2, r::text || l, 'BUS', r, l, l IN ('A','F'), l IN ('C','D'), FALSE
FROM generate_series(1, 3) r
CROSS JOIN unnest(ARRAY['A','C','D','F']) l;

INSERT INTO seat_map_seat (seat_map_id, seat_number, cabin_code, row_num, seat_letter, is_window, is_aisle, is_exit_row)
SELECT 2, r::text || l, 'ECO', r, l, l IN ('A','F'), l IN ('C','D'), r IN (14, 15)
FROM generate_series(10, 34) r
CROSS JOIN unnest(ARRAY['A','B','C','D','E','F']) l;

-- A320neo: 12J + 162Y.
INSERT INTO seat_map_seat (seat_map_id, seat_number, cabin_code, row_num, seat_letter, is_window, is_aisle, is_exit_row)
SELECT 3, r::text || l, 'BUS', r, l, l IN ('A','F'), l IN ('C','D'), FALSE
FROM generate_series(1, 3) r
CROSS JOIN unnest(ARRAY['A','C','D','F']) l;

INSERT INTO seat_map_seat (seat_map_id, seat_number, cabin_code, row_num, seat_letter, is_window, is_aisle, is_exit_row)
SELECT 3, r::text || l, 'ECO', r, l, l IN ('A','F'), l IN ('C','D'), r IN (14, 15)
FROM generate_series(10, 36) r
CROSS JOIN unnest(ARRAY['A','B','C','D','E','F']) l;

INSERT INTO aircraft (registration, model_code, seat_map_id) VALUES
  ('HK-4801', 'AT72', 1), ('HK-4802', 'AT72', 1), ('HK-4803', 'AT72', 1),
  ('HK-4810', 'A320', 2), ('HK-4811', 'A320', 2), ('HK-4812', 'A320', 2),
  ('HK-4813', 'A320', 2), ('HK-4814', 'A320', 2),
  ('HK-4820', 'A20N', 3), ('HK-4821', 'A20N', 3), ('HK-4822', 'A20N', 3),
  ('HK-4823', 'A20N', 3);

-- ------------------------------------------------------- Clases y reglas
INSERT INTO fare_rule (fare_rule_id, name, min_advance_days, is_refundable, refund_penalty,
                       is_changeable, change_penalty, baggage_pieces, free_seat_choice)
OVERRIDING SYSTEM VALUE VALUES
  (1, 'Promo anticipada 21d', 21, FALSE,      0, FALSE,      0, 0, FALSE),
  (2, 'Estándar 7d',           7, FALSE,      0, TRUE,  120000, 1, FALSE),
  (3, 'Flexible',              0, TRUE,  180000, TRUE,       0, 1, TRUE),
  (4, 'Ejecutiva flexible',    0, TRUE,       0, TRUE,       0, 2, TRUE);
SELECT setval('fare_rule_fare_rule_id_seq', 4, true);

INSERT INTO fare_class (fare_class_code, cabin_code, name, rank) VALUES
  ('P', 'ECO', 'Económica promo',    1),
  ('Y', 'ECO', 'Económica estándar', 2),
  ('B', 'ECO', 'Económica flexible', 3),
  ('J', 'BUS', 'Ejecutiva',          4);

-- Tarifas: una por (ruta, clase). Precio proporcional a la distancia.
INSERT INTO fare (route_id, fare_class_code, fare_rule_id, currency, base_amount, tax_pct, valid_from, valid_to)
SELECT r.route_id, fc.code, fc.rule, 'COP',
       ROUND((r.distance_km * fc.factor)::numeric, -3),
       19.00, DATE '2026-01-01', DATE '2027-12-31'
FROM route r
CROSS JOIN (VALUES
    ('P'::char(1), 1, 380.0),
    ('Y'::char(1), 2, 620.0),
    ('B'::char(1), 3, 940.0),
    ('J'::char(1), 4, 2100.0)
) AS fc(code, rule, factor);

-- ---------------------------------------------------- Vuelos programados
INSERT INTO scheduled_flight (scheduled_flight_id, flight_number, route_id,
                              departure_time_local, arrival_time_local, arrival_day_offset,
                              days_of_week, valid_from, valid_to, seat_map_id)
OVERRIDING SYSTEM VALUE
SELECT s.id, s.flight_number, r.route_id, s.dep::time, s.arr::time, 0,
       '1234567', DATE '2026-01-01', DATE '2027-12-31', s.seat_map_id
FROM (VALUES
  (1,  'AV8101', 'BOG','MDE', '06:30','07:25', 2),
  (2,  'AV8102', 'BOG','MDE', '14:00','14:55', 2),
  (3,  'AV8103', 'MDE','BOG', '08:15','09:10', 2),
  (4,  'AV8104', 'MDE','BOG', '18:40','19:35', 2),
  (5,  'AV8201', 'BOG','CTG', '07:10','08:40', 3),
  (6,  'AV8202', 'CTG','BOG', '09:30','11:00', 3),
  (7,  'AV8301', 'MDE','CTG', '10:00','11:15', 1),
  (8,  'AV8302', 'CTG','MDE', '12:00','13:15', 1),
  (9,  'AV8401', 'BOG','CLO', '06:00','07:05', 1),
  (10, 'AV8402', 'CLO','BOG', '08:00','09:05', 1),
  (11, 'AV8501', 'CTG','SMR', '15:00','15:45', 1),
  (12, 'AV8502', 'SMR','CTG', '16:30','17:15', 1),
  (13, 'AV8601', 'BOG','BAQ', '11:20','12:50', 3),
  (14, 'AV8602', 'BAQ','BOG', '13:40','15:10', 3)
) AS s(id, flight_number, orig, dest, dep, arr, seat_map_id)
JOIN route r ON r.origin_iata = s.orig AND r.destination_iata = s.dest;
SELECT setval('scheduled_flight_scheduled_flight_id_seq', 14, true);

-- ---------------------------------------------- Materialización (RF-25)
-- Genera instancias para los próximos 60 días con su inventario por cabina.
-- En producción este bloque es el job diario que cubre el horizonte de 360
-- días (SUP-3); aquí se acorta para que el seed sea rápido.
INSERT INTO flight_instance (scheduled_flight_id, flight_date, departure_utc, arrival_utc,
                             aircraft_id, status)
SELECT
    sf.scheduled_flight_id,
    d::date,
    (d::date + sf.departure_time_local) AT TIME ZONE ap_o.timezone,
    (d::date + sf.arrival_time_local + (sf.arrival_day_offset || ' day')::interval)
        AT TIME ZONE ap_d.timezone,
    -- Asignación redonda de aeronave compatible con el mapa de sillas.
    (SELECT a.aircraft_id FROM aircraft a
      WHERE a.seat_map_id = sf.seat_map_id AND a.status = 'ACTIVE'
      ORDER BY (a.aircraft_id + sf.scheduled_flight_id
                + EXTRACT(DOY FROM d)::int) % 4, a.aircraft_id
      LIMIT 1),
    'SCHEDULED'
FROM scheduled_flight sf
JOIN route   rt   ON rt.route_id = sf.route_id
JOIN airport ap_o ON ap_o.iata_code = rt.origin_iata
JOIN airport ap_d ON ap_d.iata_code = rt.destination_iata
CROSS JOIN generate_series(CURRENT_DATE, CURRENT_DATE + 60, INTERVAL '1 day') d
WHERE strpos(sf.days_of_week, EXTRACT(ISODOW FROM d)::text) > 0
  AND d::date BETWEEN sf.valid_from AND sf.valid_to;

-- Inventario: una fila por (instancia x cabina), con la capacidad derivada
-- del mapa de sillas (DEC-3).
INSERT INTO flight_inventory (flight_instance_id, cabin_code, capacity, seats_sold, seats_held)
SELECT fi.flight_instance_id, cap.cabin_code, cap.capacity, 0, 0
FROM flight_instance fi
JOIN scheduled_flight sf ON sf.scheduled_flight_id = fi.scheduled_flight_id
JOIN v_seat_map_capacity cap ON cap.seat_map_id = sf.seat_map_id;

-- --------------------------------------------------------------- Agencias
INSERT INTO agency (legal_name, tax_id, net_fare_discount_pct, commission_pct,
                    credit_limit, credit_used, currency)
VALUES
  ('Viajes El Dorado S.A.S.', '900123456-1', 8.00,  5.00, 50000000, 0, 'COP'),
  ('Caribe Tours Ltda.',      '900987654-2', 5.00,  4.00, 20000000, 0, 'COP');

-- ============================================================
--  ESCENARIO DE PRUEBA DE CONCURRENCIA (RNF-C1)
--  Deja UNA sola silla libre en ejecutiva del primer vuelo
--  BOG-MDE de mañana. La prueba de 5.6 lanza N solicitudes
--  simultáneas contra él y verifica que solo una gana.
-- ============================================================
UPDATE flight_inventory inv
SET seats_sold = inv.capacity - 1
FROM flight_instance fi
JOIN scheduled_flight sf ON sf.scheduled_flight_id = fi.scheduled_flight_id
WHERE inv.flight_instance_id = fi.flight_instance_id
  AND inv.cabin_code = 'BUS'
  AND sf.flight_number = 'AV8101'
  AND fi.flight_date = CURRENT_DATE + 1;

-- Resumen de lo cargado.
DO $$
DECLARE
    v_instances INT; v_inventory INT; v_scarce INT;
BEGIN
    SELECT COUNT(*) INTO v_instances FROM flight_instance;
    SELECT COUNT(*) INTO v_inventory FROM flight_inventory;
    SELECT COUNT(*) INTO v_scarce FROM v_flight_availability WHERE seats_available = 1;
    RAISE NOTICE 'Seed completo: % instancias de vuelo, % filas de inventario, % con 1 silla libre.',
        v_instances, v_inventory, v_scarce;
END $$;
