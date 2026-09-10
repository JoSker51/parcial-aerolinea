# Sistema de Reservas — Aerolínea Regional
## Parte I — Contexto, supuestos y requisitos (secciones 5.1 y 5.2)

**Curso:** Big Data e Ingeniería de Datos · **Entrega:** Parcial 1, 2026-2
**Equipo:** _(completar)_ · **Fecha:** _(completar)_

---

## 0. Cómo leer este documento

Cada decisión de arquitectura de las partes II y III lleva una referencia del tipo `→ RNF-C1`, que apunta al requisito que la origina. Esa cadena de trazabilidad es intencional: es el criterio de "Claridad y trazabilidad" de la rúbrica (10%). El índice inverso completo está en el **Anexo B — Matriz de trazabilidad**.

Convenciones de identificadores:

| Prefijo | Significado |
|---|---|
| `SUP-n` | Supuesto declarado (sección 1) |
| `RF-xx` | Requisito funcional (5.1) |
| `RNF-Xn` | Requisito no funcional (5.2), donde `X` es la inicial de la categoría |
| `DEC-n` | Decisión de arquitectura registrada (partes II y III) |
| `RE-n` | Riesgo de negocio explícito |

---

## 1. Supuestos declarados

El enunciado es deliberadamente incompleto. Un supuesto no declarado es una decisión de arquitectura oculta, así que aquí se fijan explícitamente. **Cada supuesto es una variable, no una verdad**: la columna "Qué se rompe si cambia" es la que se sustenta en la defensa oral.

### 1.1 Escala y volumen

| ID | Supuesto | Justificación | Qué se rompe si cambia |
|---|---|---|---|
| **SUP-1** | Aerolínea **regional** con **35 rutas** origen-destino y **18 aeropuertos** en la red. | "Regional" en el contexto colombiano (p. ej. una operación tipo Satena/EasyFly) está en el orden de decenas de rutas, no de cientos. | Si fueran 500 rutas, la búsqueda de itinerarios con escalas deja de ser un `JOIN` viable y exige un motor de búsqueda precomputado (→ RNF-E1). |
| **SUP-2** | **~120 instancias de vuelo por día**; flota de **12 aeronaves** en **3 configuraciones** (ATR-72 de 68 sillas, A320 de 180, A320neo de 186). | 12 aeronaves × ~10 tramos/día. Tres configuraciones justifican separar `aircraft_model` de `aircraft`. | Con flota homogénea, el mapa de sillas podría colapsarse a una constante y desaparecería la entidad `seat_map`. |
| **SUP-3** | Horizonte de venta: **360 días** hacia adelante. Esto genera ~43.000 instancias de vuelo vivas en cualquier momento. | Estándar de la industria (los GDS suelen abrir 330-360 días). | Es el número que dimensiona la tabla más grande del OLTP y el volumen del ETL (→ 5.7). |
| **SUP-4** | **Tráfico base:** 30 búsquedas/segundo, pico diario 120 búsquedas/s. **Temporada alta** (diciembre, Semana Santa, junio): **×5 → 600 búsquedas/s** durante ventanas de 2-3 horas. Relación búsqueda:reserva ≈ **80:1**. | El enunciado dice "el tráfico se dispara en temporada alta" sin dar números; este es el número que traducimos. La relación 80:1 es típica del *look-to-book* del sector. | Es el insumo directo de RNF-P1 y RNF-E1. Si la relación fuera 10:1, el cuello de botella se movería de la búsqueda al inventario. |
| **SUP-5** | Pico de contención real: **día de apertura de venta de temporada alta**, hasta **40 solicitudes concurrentes sobre el mismo `flight_instance`**. | Es el escenario que hace fallar el control de sobreventa. | Define el mecanismo de concurrencia elegido (→ DEC-4). |

### 1.2 Reglas de negocio asumidas

| ID | Supuesto | Justificación | Qué se rompe si cambia |
|---|---|---|---|
| **SUP-6** | **No hay sobreventa deliberada (*overbooking*) en v1.** La restricción del negocio se lee de forma literal: `vendidas + retenidas ≤ capacidad`. | El enunciado dice explícitamente "el sistema no debe vender más sillas de las que tiene el avión". Muchas aerolíneas reales sí sobrevenden, pero eso contradice el texto dado. | Si el negocio pidiera *overbooking*, **no cambia la arquitectura**: solo cambia la constante de capacidad efectiva por `capacity × factor_overbooking` en `flight_inventory`. Se dejó modelado como campo, no como constante, precisamente por esto (→ punto de extensión PE-1). |
| **SUP-7** | Se permite **reservar sin pagar**: la reserva nace en estado `HELD` con una **retención de 20 minutos** sobre el inventario. El pago **no** es atómico con la reserva. | Sin retención, el usuario perdería la silla mientras digita la tarjeta, y la conversión se desploma. 20 min = tiempo típico de un checkout con 3-D Secure más margen. | Si el pago fuera atómico, desaparece el estado `HELD`, desaparece el *job* de expiración, y el bloqueo de inventario tendría que sostenerse durante la llamada al proveedor de pago — lo cual es inaceptable (→ DEC-5). |
| **SUP-8** | **Sí se permiten cambios de fecha/itinerario**, con penalidad definida por la regla tarifaria. El cambio se modela como **cancelación + reemisión** dentro de la misma reserva (mismo PNR), no como edición en sitio. | Preserva el historial: el tramo viejo queda `CANCELLED`, el nuevo nace `CONFIRMED`. Resuelve el RNF de auditabilidad sin lógica adicional. | Si se editara en sitio, se perdería la trazabilidad exigida por RNF-A1. |
| **SUP-9** | **No hay programa de viajero frecuente en v1.** Se deja `passenger.loyalty_id` como campo nulo reservado. | Reduce alcance sin cerrar la puerta. | Un programa de millas agrega una entidad `loyalty_account` y un motor de acumulación/redención; **afecta el cálculo de precio, no el control de inventario**. Es un punto de extensión aislado (PE-2). |
| **SUP-10** | **Equipaje:** una pieza de mano incluida siempre; equipaje en bodega es un **producto auxiliar** (`ancillary`) comprado por tiquete. No se gestiona el equipaje físico (no hay *baggage tracking*). | Separa "vender equipaje" (que sí es del sistema de reservas) de "rastrear maletas" (que es otro sistema, de operaciones). | Sin la entidad `ancillary`, los ingresos por equipaje no aparecerían en el análisis de "ingresos por tarifa" (→ RF-24, 5.7). |
| **SUP-11** | **Asignación de silla:** opcional y **con costo** en económica al momento de la compra; gratuita en ejecutiva; **obligatoria en el check-in** (desde 24 h antes). La silla **no** es parte del control de sobreventa. | Es la decisión clave del modelo: el inventario se controla por **conteo por cabina**, no por silla individual. Ver DEC-3. | Si la silla fuera obligatoria en la compra, el punto de contención pasaría de una fila por cabina a una fila por silla — más filas, más *deadlocks*, sin ganancia de negocio. |
| **SUP-12** | **Monedas:** COP y USD. **Idiomas:** español e inglés. El precio se **almacena** en la moneda de venta más la tasa aplicada; no se reconvierte al consultar. | Un precio reconvertido al vuelo hace que un tiquete "cambie de precio" al consultarlo. Inaceptable contable y legalmente. | Si se agregaran más monedas, no cambia el modelo: cambia el catálogo `currency` y la fuente de tasas. |
| **SUP-13** | **Agencias aliadas:** venden con **tarifa neta** (descuento sobre tarifa pública) y **comisión liquidada mensualmente**. Cada agencia tiene contrato, cupo de crédito y credenciales propias. | El enunciado dice "descuento, comisión" como opciones; se toman ambas porque son el modelo B2B estándar y se excluyen mutuamente poco. | Define la necesidad de una API B2B separada (→ DEC-7) y de la entidad `agency`. |
| **SUP-14** | El **pago se delega totalmente** a un PSP externo (pasarela tipo Stripe/Wompi/PayU) mediante *hosted checkout* + *webhook*. **El sistema nunca recibe, procesa ni almacena el PAN** de la tarjeta. | Reduce el alcance PCI-DSS de SAQ-D a **SAQ-A**, que es la diferencia entre una auditoría de meses y un cuestionario. Es la decisión de seguridad de mayor impacto del proyecto. | Si se procesara la tarjeta en casa, RNF-S1 cambiaría por completo y el costo de cumplimiento haría inviable el proyecto para una aerolínea regional. |
| **SUP-15** | El sistema es la **fuente de verdad de la venta**, no de la operación del vuelo. Retrasos, cancelaciones operativas y cambios de aeronave **entran** desde el sistema de operaciones (fuera de alcance) como eventos. | Evita que el parcial se convierta en un sistema de despacho. | La entidad `flight_instance` tiene estado y aeronave asignada porque debe **recibir** esos eventos, aunque no los origine. |

### 1.3 Punto de extensión para la restricción del equipo (sección 3)

> **PE-0 — Restricción confidencial asignada al equipo.**
> A la fecha de redacción de esta parte, el equipo aún no ha recibido la restricción específica de la sección 3. El documento está construido para absorberla en tres puntos concretos, sin rediseño:
>
> 1. **Reglas de negocio** → se agrega como supuesto `SUP-16` en esta misma tabla, con su columna de impacto.
> 2. **Requisitos** → se agrega como `RF-xx` y/o `RNF-Xn` en las tablas de 5.1/5.2, y se referencia desde la matriz de trazabilidad (Anexo B).
> 3. **Modelo y arquitectura** → se documenta como `DEC-n` con su alternativa descartada.
>
> Ejemplos de cómo aterrizaría según el tipo de restricción, para demostrar que el diseño es robusto a ella:
>
> | Tipo de restricción hipotética | Dónde impacta este diseño |
> |---|---|
> | "Debe permitir *overbooking* del 5%" | Solo `flight_inventory.oversell_factor` (ya previsto en SUP-6). Cero cambios estructurales. |
> | "Los datos deben permanecer en territorio nacional" | RNF-S3 + elección de región AWS en 5.7; afecta costo, no modelo. |
> | "Una agencia no puede ver la disponibilidad real, solo un cupo asignado" | Nueva entidad `agency_allotment` colgando de `flight_inventory`; el bloqueo de DEC-4 pasa a operar sobre dos filas en la misma transacción. |
> | "El sistema debe operar sin conexión en aeropuertos remotos" | Es el caso de mayor impacto: obligaría a un modelo de inventario particionado con reconciliación, y **rompe** la garantía de serialización de DEC-4. Se documentaría como excepción explícita. |

---

## 2. Actores del sistema

Pregunta guía de 5.1: *¿todos interactúan con el mismo sistema, o con interfaces distintas sobre el mismo backend?*

**Respuesta del equipo: interfaces distintas sobre el mismo backend y el mismo dominio.** Hay cuatro superficies de entrada, pero un solo modelo de reserva y un solo control de inventario. La razón es directamente el RNF de concurrencia: si cada canal tuviera su propia lógica de inventario, la garantía de no-sobreventa habría que probarla cuatro veces y se rompería la primera vez que los canales divergieran.

| Actor | Tipo | Interfaz | Autenticación | Notas |
|---|---|---|---|---|
| **A1 — Pasajero** | Humano, externo | Web pública (SSR + SPA) y móvil | Opcional: compra como invitado o con cuenta. Consulta de reserva por PNR + apellido. | El 100% del tráfico de búsqueda viene de aquí (SUP-4). |
| **A2 — Agente de agencia aliada** | Humano, externo | Consola B2B separada | OAuth2 *client credentials* por agencia + usuario nominal | Ve tarifas netas, cupo de crédito y su liquidación de comisiones (SUP-13). |
| **A3 — Personal de aeropuerto / check-in** | Humano, interno | Consola operativa | SSO corporativo + rol | Asigna sillas, hace check-in, gestiona no-shows. No vende. |
| **A4 — Administrador comercial** | Humano, interno | Back-office | SSO corporativo + rol, con MFA obligatorio | Crea rutas, vuelos programados, tarifas y reglas tarifarias. Sus cambios son los de mayor impacto: por eso RNF-A1 los audita. |
| **A5 — Proveedor de pagos (PSP)** | Sistema, externo | Webhook entrante + API saliente | Firma HMAC del webhook + API key saliente | Actor no humano, pero es quien confirma la venta (SUP-14). |
| **A6 — Sistema de operaciones** | Sistema, externo | API de ingesta de eventos | mTLS / API key interna | Origina cambios de estado del vuelo (SUP-15). |
| **A7 — Analista de negocio** | Humano, interno | BI sobre la base OLAP | SSO + rol de solo lectura | **No toca el OLTP.** Es la razón de ser de 5.7. |

---

## 3. Riesgos de negocio explícitos

La rúbrica exige que las métricas de los RNF sean *"trazables a un riesgo de negocio explícito"*. Los riesgos se nombran aquí y cada RNF apunta a uno.

| ID | Riesgo | Impacto si se materializa |
|---|---|---|
| **RE-1** | **Sobreventa.** Se venden más sillas que la capacidad. | Denegación de embarque: compensación regulatoria (en Colombia, RAC 3.10), reubicación, y daño reputacional. Es el riesgo que el propio enunciado señala. |
| **RE-2** | **Abandono por lentitud.** La búsqueda tarda y el usuario se va a un metabuscador. | Pérdida directa de ingreso. En viajes, cada segundo adicional de búsqueda cuesta conversión de forma medible. |
| **RE-3** | **Caída en temporada alta.** El sistema no está disponible en la ventana de mayor venta del año. | Concentración del ingreso: una hora caída en apertura de temporada vale mucho más que una hora caída en febrero. |
| **RE-4** | **Fuga de datos personales o de pago.** | Sanción de la SIC por Ley 1581 de 2012; pérdida del contrato de adquirencia si es PCI. |
| **RE-5** | **Disputa comercial no reconstruible.** Un pasajero o una agencia reclama sobre una reserva y no se puede probar qué pasó. | Pérdida del reclamo por defecto, y riesgo de fraude interno no detectable. |
| **RE-6** | **Crecimiento que degrada.** Se agregan rutas y el sistema se vuelve más lento en vez de igual. | Freno comercial: el sistema deja de habilitar el crecimiento y pasa a limitarlo. |

---

## 5.1 Requisitos Funcionales

### Decisiones tomadas sobre las preguntas guía

Antes de la tabla, las cinco preguntas del enunciado, respondidas de forma explícita:

**(a) ¿Actores en el mismo sistema o en interfaces distintas?**
Interfaces distintas, backend y dominio únicos. Ver sección 2. → `DEC-7`

**(b) ¿Reserva con múltiples trayectos: una reserva con varios vuelos, o varias reservas encadenadas?**
**Una sola reserva (un PNR) con una entidad `itinerary` intermedia.** Razones:
- El pasajero compró **un viaje**, no tres vuelos. Si se cancela el primer tramo, la aerolínea tiene una obligación sobre el itinerario completo, no sobre un tramo suelto. Reservas encadenadas obligarían a inventar y mantener a mano el vínculo entre ellas.
- El pago es uno solo (el enunciado dice "en una sola transacción"). Con reservas separadas habría N pagos y una compensación distribuida entre ellos.
- Una **ida y vuelta** o un **multi-tramo** con precios de ida-y-vuelta no se puede tarifar por tramo suelto: el precio es una propiedad del itinerario.
→ `RF-08`, `RF-09`, `DEC-2`

**(c) ¿Se permite reservar sin pagar?**
Sí: retención de 20 minutos (`HELD`). El pago **no** es atómico con la reserva, pero **la retención sí es atómica** con la verificación de inventario. Esta distinción es el corazón del diseño de concurrencia. → `SUP-7`, `RF-10`, `RF-12`, `DEC-5`

**(d) ¿Qué pasa si el pasajero cancela o cambia la fecha?**
Ambas operan sobre el itinerario, no sobre el tramo aislado. Cancelar libera inventario **inmediatamente** y genera un reembolso según regla tarifaria. Cambiar = cancelar el tramo viejo + emitir uno nuevo dentro del mismo PNR, cobrando penalidad + diferencia tarifaria. Nada se borra: todo queda como transición de estado auditada. → `RF-15` a `RF-18`, `SUP-8`, `RNF-A1`

**(e) ¿Las agencias tienen flujo diferente?**
**Mismo proceso de dominio, distinto contrato de API, distinta tarifa y distinto medio de pago.** La agencia no paga con tarjeta: consume cupo de crédito y se le liquida mensualmente. Reutilizar el flujo de dominio garantiza que la regla de no-sobreventa sea idéntica en ambos canales (mitiga RE-1); separar la API permite versionarla y limitarla sin tocar la web pública. → `RF-19` a `RF-22`, `SUP-13`, `DEC-7`

### Escala de prioridad

`Must` = sin esto no hay producto vendible · `Should` = necesario para operar bien, puede diferirse una iteración · `Could` = deseable, primer candidato a recortar.

### Tabla de requisitos funcionales

#### Búsqueda y catálogo

| ID | Requisito | Actor | Prioridad | Notas / trazabilidad |
|---|---|---|---|---|
| **RF-01** | Buscar vuelos disponibles por origen, destino, fecha, número y tipo de pasajeros (adulto/niño/infante) y cabina. | A1, A2 | Must | Operación de mayor volumen (SUP-4). → RNF-P1 |
| **RF-02** | Devolver, en una misma búsqueda, itinerarios **directos y con escalas** (hasta 2 conexiones), con tiempo total de viaje y tiempo de conexión. | A1, A2 | Must | Resuelve el requisito de escalas del escenario. → RNF-P2, DEC-2 |
| **RF-03** | Filtrar y ordenar resultados por precio, duración, número de escalas y hora de salida. | A1, A2 | Should | Se resuelve en el frontend sobre el conjunto ya devuelto. → 5.5 |
| **RF-04** | Mostrar el precio total desglosado: tarifa base + impuestos + cargos + auxiliares, en la moneda de la sesión. | A1, A2 | Must | SUP-12. El desglose es exigencia regulatoria de publicidad de precios. |
| **RF-05** | Buscar por "fechas flexibles" (±3 días) mostrando la tarifa mínima por día. | A1 | Could | Multiplica ×7 el costo de búsqueda; primer candidato a caché agresivo. → RNF-P3 |
| **RF-06** | Consultar el mapa de sillas de una instancia de vuelo con su estado (libre / ocupada / bloqueada) y el costo de selección. | A1, A2, A3 | Should | SUP-11. Es una consulta, no un bloqueo. |
| **RF-07** | Exponer el catálogo de aeropuertos, rutas y equipajes permitidos. | A1, A2 | Should | Datos casi estáticos → caché de larga duración. |

#### Reserva

| ID | Requisito | Actor | Prioridad | Notas / trazabilidad |
|---|---|---|---|---|
| **RF-08** | Crear una **reserva única (PNR)** que agrupe uno o varios itinerarios, cada uno con uno o varios tramos ordenados, para uno o varios pasajeros, en una sola transacción. | A1, A2 | Must | Decisión (b). → DEC-2 |
| **RF-09** | Validar la coherencia del itinerario antes de retener: continuidad geográfica entre tramos, tiempo mínimo de conexión (MCT) por aeropuerto y ausencia de solapamiento horario. | A1, A2 | Must | Sin esto se venden itinerarios imposibles de volar. |
| **RF-10** | **Retener** el inventario de todos los tramos del itinerario de forma **atómica** (todo o nada) por 20 minutos, garantizando que `vendidas + retenidas ≤ capacidad` por cabina. | A1, A2 | **Must — crítico** | El requisito que sostiene toda la arquitectura. → RNF-C1, RE-1, DEC-4 |
| **RF-11** | Liberar automáticamente las retenciones vencidas y devolver el inventario a disponible. | Sistema | Must | Sin esto, el inventario se "evapora" con cada carrito abandonado. |
| **RF-12** | Confirmar la reserva (`HELD` → `CONFIRMED`) únicamente al recibir la confirmación de pago del PSP. | A5, Sistema | Must | SUP-14. → DEC-5 |
| **RF-13** | Generar un **localizador (PNR) de 6 caracteres alfanuméricos** único, legible y no secuencial. | Sistema | Must | No secuencial: un PNR predecible permite enumerar reservas ajenas. → RNF-S2 |
| **RF-14** | Emitir tiquetes: un tiquete por (pasajero × tramo), con su clase tarifaria y estado propios. | Sistema | Must | Es el grano que necesita el análisis de ingresos por tarifa. → 5.7 |

#### Post-venta

| ID | Requisito | Actor | Prioridad | Notas / trazabilidad |
|---|---|---|---|---|
| **RF-15** | Consultar una reserva por PNR + apellido (sin necesidad de cuenta), o listar las reservas de una cuenta autenticada. | A1, A2 | Must | El par PNR+apellido es el estándar del sector y es también el control de acceso. → RNF-S2 |
| **RF-16** | Cancelar la reserva completa o un itinerario, liberando el inventario **inmediatamente** y calculando el reembolso según la regla tarifaria aplicada. | A1, A2 | Must | Liberación inmediata: el inventario retenido por una reserva muerta es ingreso perdido. |
| **RF-17** | Cambiar la fecha de un itinerario como cancelación + reemisión dentro del mismo PNR, cobrando penalidad y diferencia tarifaria. | A1, A2 | Should | SUP-8. La reemisión pasa por el **mismo** camino de retención de RF-10. |
| **RF-18** | Registrar **toda** transición de estado de una reserva (quién, cuándo, desde qué estado, hacia cuál, por qué canal) de forma **inmutable**. | Sistema | Must | → RNF-A1, RE-5 |
| **RF-19** | Comprar productos auxiliares (equipaje en bodega, selección de silla) asociados a un tiquete. | A1, A2 | Should | SUP-10, SUP-11. |

#### Canal B2B (agencias aliadas)

| ID | Requisito | Actor | Prioridad | Notas / trazabilidad |
|---|---|---|---|---|
| **RF-20** | Autenticar agencias mediante credenciales de cliente propias, con cuotas de uso independientes por agencia. | A2 | Must | SUP-13. Aísla el canal B2B del tráfico público. → RNF-E2 |
| **RF-21** | Aplicar **tarifa neta** (con descuento contractual por agencia) y registrar la **comisión** devengada en cada venta. | A2 | Must | SUP-13. La comisión se registra en la venta, se liquida después. |
| **RF-22** | Permitir a la agencia reservar contra **cupo de crédito**, sin pago con tarjeta, validando el cupo disponible antes de confirmar. | A2 | Must | Es la diferencia real de flujo frente al pasajero: cambia el paso de pago, no el de inventario. |
| **RF-23** | Generar la liquidación mensual por agencia: ventas, comisiones, cancelaciones y saldo. | A4 | Should | Consumidor natural de la base OLAP. → 5.7 |

#### Operación y administración

| ID | Requisito | Actor | Prioridad | Notas / trazabilidad |
|---|---|---|---|---|
| **RF-24** | Administrar el catálogo: aeropuertos, rutas, aeronaves, mapas de silla, vuelos programados y sus reglas de recurrencia. | A4 | Must | → DEC-1 |
| **RF-25** | **Materializar** instancias de vuelo a partir de los vuelos programados dentro del horizonte de venta, creando su inventario por cabina. | Sistema | Must | Es el proceso que hace real la separación programado/instancia. → DEC-1 |
| **RF-26** | Administrar tarifas y reglas tarifarias: clase, cabina, condiciones de anticipación, penalidades, reembolsabilidad y vigencia. | A4 | Must | Cubre "tarifas según anticipación y tipo de silla" del enunciado. |
| **RF-27** | Asignar sillas y realizar el check-in desde 24 h antes de la salida. | A1, A3 | Should | SUP-11. |
| **RF-28** | Recibir eventos operativos (retraso, cancelación de vuelo, cambio de aeronave) y actualizar la instancia de vuelo y su inventario. | A6 | Should | SUP-15. Un cambio de aeronave **reduce capacidad** → puede requerir reacomodación. |
| **RF-29** | Notificar al pasajero por correo la confirmación, la cancelación y los cambios operativos de su itinerario. | Sistema | Should | |
| **RF-30** | Exponer al pipeline analítico los datos de reservas, tiquetes, vuelos y pagos sin impactar el rendimiento transaccional. | A7 | Must | Enlace explícito entre 5.4 y 5.7. → RNF-P4 |

**Cobertura:** 30 requisitos funcionales, de los cuales 17 son `Must`. Los tres del enunciado que se evalúan de forma específica en la rúbrica — distinguir **reserva de compra** (RF-10 vs RF-12), manejar **itinerarios multi-tramo** (RF-08, RF-09) y prevenir **sobreventa** (RF-10) — están cubiertos de forma explícita y separada.

---

## 5.2 Requisitos No Funcionales

Formato: cada RNF tiene **métrica**, **umbral**, **cómo se mide** y **riesgo de negocio** al que responde. Un RNF que no se puede medir no es un requisito, es un deseo.

### Rendimiento

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo |
|---|---|---|---|---|
| **RNF-P1** | La búsqueda de **vuelos directos** responde rápido bajo carga normal. | **p95 ≤ 400 ms**, **p99 ≤ 800 ms**, medido en el servidor, con 120 búsquedas/s (pico diario, SUP-4). | Prueba de carga con k6 sobre el escenario "búsqueda directa"; métricas de latencia por percentil en CloudWatch. | RE-2 |
| **RNF-P2** | La búsqueda **con escalas** puede tardar más, pero acotado. | **p95 ≤ 1.200 ms** para itinerarios de hasta 2 conexiones. | Igual, escenario "búsqueda con conexiones". | RE-2 |
| **RNF-P3** | En **temporada alta** (600 búsquedas/s, SUP-4) el sistema degrada de forma controlada, no cae. | p95 ≤ 1.000 ms para directos; tasa de error < 0,5%; ninguna respuesta 5xx por saturación de conexiones a base de datos. | Prueba de carga al ×5 antes de cada temporada alta, con el plan de escalado activo. | RE-3 |
| **RNF-P4** | La carga analítica **no degrada** al transaccional. | El p95 de RNF-P1 no se deteriora más de un **10%** mientras corre el ETL. | Se mide el p95 de búsqueda durante y fuera de la ventana de ETL. | RE-2, RE-6 |

> **Respuesta a la pregunta guía:** sí, el umbral cambia con escalas, y por una razón estructural: buscar un vuelo directo es una consulta indexada sobre `flight_instance`; buscar con escalas es una búsqueda de caminos de longitud 2-3 en el grafo de la red, con validación de MCT en cada nodo. Fijar el mismo umbral para ambos obligaría a precomputar todos los itinerarios posibles — costo que a 35 rutas (SUP-1) no se justifica.

### Consistencia y concurrencia

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo |
|---|---|---|---|---|
| **RNF-C1** | **Nunca se confirma más inventario que la capacidad.** Ante N solicitudes simultáneas por las últimas K sillas de una cabina, exactamente **K** tienen éxito y **N−K** reciben un error de "sin disponibilidad" claro. **Cero sobreventa, en cualquier nivel de concurrencia.** | `vendidas + retenidas ≤ capacidad` se cumple **siempre**; violaciones = **0**. Verificado con **40 solicitudes concurrentes** (SUP-5) sobre la última silla. | Prueba automatizada de concurrencia con N hilos reales contra la API (entregada en 5.6), más una **restricción `CHECK` en la base de datos** como última línea de defensa. | **RE-1** |
| **RNF-C2** | La retención de un itinerario multi-tramo es **atómica**: o se retienen todos los tramos, o ninguno. | Cero reservas con tramos parcialmente retenidos. Verificable por consulta de integridad. | Prueba de fallo inyectado en el tramo 2 de 3; se comprueba que el tramo 1 quedó liberado. | RE-1 |
| **RNF-C3** | Una retención vencida libera inventario en **≤ 60 s** después de su expiración. | Diferencia entre `expires_at` y la liberación efectiva ≤ 60 s en p99. | Monitor sobre la antigüedad de retenciones vencidas no liberadas. | RE-1 (invertido: inventario fantasma) |
| **RNF-C4** | La confirmación de pago es **idempotente**: un webhook reentregado no duplica tiquetes ni cobros. | Reenviar el mismo evento 10 veces produce exactamente 1 confirmación. | Prueba automatizada de reentrega. | RE-5 |

> **Respuesta a la pregunta guía:** si dos pasajeros piden la última silla al tiempo, el sistema debe garantizar que **uno gane de forma determinista y el otro reciba un rechazo inmediato y claro** — no un error genérico, no una confirmación optimista que luego se revierte, y en ningún caso dos confirmaciones. Se prefiere explícitamente **rechazar rápido a confirmar y luego revertir**: revertir una venta confirmada tiene costo regulatorio (RE-1) que un rechazo no tiene. Esta preferencia es la que justifica el bloqueo pesimista de `DEC-4` frente al optimista.

### Disponibilidad

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo |
|---|---|---|---|---|
| **RNF-D1** | Disponibilidad de los flujos de **búsqueda y reserva**: **99,9% mensual** en operación normal, y **99,95%** en las ventanas declaradas de temporada alta. | 99,9% = **43,2 min** de indisponibilidad/mes. 99,95% = **21,6 min**/mes. Medido con *health checks* externos cada 30 s. | Reporte mensual de SLO con presupuesto de error consumido. | RE-3 |
| **RNF-D2** | Los flujos de **post-venta** (consulta, check-in) toleran más: **99,5% mensual**. | 3,6 h/mes. | Igual, por endpoint. | — |
| **RNF-D3** | RPO ≤ **5 min**, RTO ≤ **1 h** para la base transaccional. | Verificado con una prueba de restauración semestral documentada. | Simulacro de restauración. | RE-3, RE-5 |

> **Respuesta a la pregunta guía — por qué 99,9% y no 99,99%:**
> "No se puede caer en temporada alta" es una frase, no un número. Traducirla exige decidir qué se paga por ella.
>
> | Nivel | Caída/mes | Qué exige | Costo relativo |
> |---|---|---|---|
> | 99% | 7,2 h | Una instancia, respaldo diario. | ×1 |
> | **99,9%** | **43,2 min** | **Multi-AZ, despliegue sin caída, autoescalado, guardia.** | **≈ ×2** |
> | 99,99% | 4,3 min | Multi-región activo-activo, conmutación automática, réplica de escritura entre regiones. | ≈ ×5-8 |
>
> **Se elige 99,9%** porque el salto a 99,99% obliga a **multi-región activa**, y eso choca de frente con RNF-C1: una base de datos escribible en dos regiones no puede garantizar de forma barata la serialización del inventario. Se tendría que elegir entre disponibilidad extrema y no-sobreventa, y el enunciado es explícito en que la sobreventa es inaceptable. **Se prioriza la corrección del inventario sobre los 39 minutos adicionales de disponibilidad.** El refuerzo a 99,95% en temporada alta se logra con capacidad preaprovisionada y congelamiento de despliegues, no con más arquitectura. Esta es la decisión más defendible de toda la sección y hay que llevarla clara a la sustentación.

### Seguridad

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo |
|---|---|---|---|---|
| **RNF-S1** | El sistema **nunca** almacena, registra ni procesa datos completos de tarjeta (PAN, CVV). Alcance PCI-DSS limitado a **SAQ-A**. | **Cero** ocurrencias de PAN en base de datos, logs o trazas. | Escaneo automatizado de patrones de tarjeta en logs y respaldos, en cada despliegue. | **RE-4** |
| **RNF-S2** | Acceso a una reserva solo con PNR **+** apellido coincidente, con límite de **5 intentos fallidos por IP cada 15 min**. | Tasa de bloqueo y alerta ante enumeración. | Prueba de enumeración en la revisión de seguridad. | RE-4 |
| **RNF-S3** | Datos personales cifrados **en tránsito** (TLS 1.2+) y **en reposo** (AES-256). Retención de datos de pasajero de **5 años** tras el viaje; luego se anonimizan. | Auditoría de configuración; *job* de anonimización con reporte. | Revisión trimestral. | RE-4 |
| **RNF-S4** | Toda acción de A4 (administrador) exige **MFA** y queda registrada con identidad nominal. | Cero acciones administrativas sin identidad atribuible. | Revisión del log de auditoría. | RE-5 |
| **RNF-S5** | Cada agencia solo puede leer y modificar **sus propias** reservas. | Cero accesos entre agencias. Prueba automatizada de aislamiento. | Prueba de autorización en CI. | RE-4 |

> **Respuesta a la pregunta guía — regulación aplicable:** sí, tres marcos concurrentes.
> **(1) PCI-DSS**, por procesar pagos con tarjeta. La decisión de SUP-14 (delegar todo al PSP con *hosted checkout*) reduce el alcance a **SAQ-A**, el nivel más bajo. Es una decisión de arquitectura tomada por una razón regulatoria, y así hay que sustentarla.
> **(2) Ley 1581 de 2012 (Habeas Data, Colombia)** y su decreto reglamentario 1377 de 2013: exigen autorización previa e informada, finalidad declarada, derecho de supresión y registro de bases de datos ante la SIC. De ahí la retención acotada y la anonimización de RNF-S3.
> **(3) RGPD**, aplicable si se venden tiquetes a residentes de la UE — muy plausible en una aerolínea aunque sea regional. Impone derecho al olvido y portabilidad; se satisface con el mismo mecanismo de anonimización, por eso no se lista como requisito aparte.

### Escalabilidad

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo |
|---|---|---|---|---|
| **RNF-E1** | Agregar **20 rutas** (de 35 a 55, +57%, SUP-1) **no degrada** la latencia de búsqueda más allá de los umbrales de RNF-P1/P2, sin rediseño. | p95 se mantiene bajo umbral con el catálogo ampliado y volumen proporcional. | Prueba de carga con catálogo sintético de 55 rutas **antes** de comprometer las rutas comercialmente. | **RE-6** |
| **RNF-E2** | El tráfico B2B de una agencia no puede degradar el canal público. | Cuota por agencia; ninguna agencia consume más del **20%** de la capacidad de búsqueda. | Límite de tasa por credencial + alerta. | RE-2 |
| **RNF-E3** | La capa de aplicación escala **horizontalmente**: duplicar instancias aproximadamente duplica el rendimiento de búsqueda (sin estado en el servidor). | Eficiencia de escalado ≥ 0,8 al pasar de 2 a 4 instancias. | Prueba de carga comparativa. | RE-3 |

> **Respuesta a la pregunta guía — ¿cuál es el primer cuello de botella con 20 rutas nuevas?**
> **No es el volumen de datos, es la explosión combinatoria de la búsqueda con escalas.** Los tramos directos crecen de forma lineal (35 → 55 rutas ≈ +57% de filas en `flight_instance`, algo que un índice absorbe sin problema). Pero los itinerarios *con conexión* crecen con el número de **pares** de rutas que comparten un aeropuerto de conexión: aproximadamente cuadrático. Con 35 rutas hay del orden de 10² caminos de 2 tramos a evaluar; con 55, del orden de 3×10². El primer componente en saturarse es la **CPU de la base de datos durante la búsqueda de itinerarios**, no el almacenamiento ni el motor de reservas.
> **Mitigación por etapas, en este orden:** (1) caché de resultados de búsqueda por (origen, destino, fecha) con TTL corto — el 90% del tráfico se concentra en pocas rutas; (2) réplicas de lectura para separar búsqueda de reserva; (3) si y solo si se superan ~150 rutas, precomputar itinerarios en un almacén de solo lectura. **No se hace (3) desde el día uno**: sería optimizar para un problema que no se tiene (→ pilar de optimización de costos).

### Auditabilidad

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo |
|---|---|---|---|---|
| **RNF-A1** | Toda transición de estado de una reserva, tiquete o pago se registra de forma **inmutable** (solo inserción), con actor, marca de tiempo, canal, estado anterior y posterior. | **100%** de las transiciones auditadas. Reconstruir el estado de cualquier reserva en cualquier instante pasado debe ser posible **solo con el log**. | Prueba de reconstrucción: se toma una reserva con ≥ 5 transiciones y se reconstruye su historia. | **RE-5** |
| **RNF-A2** | El log de auditoría se conserva **7 años** y es inalterable (sin `UPDATE` ni `DELETE`, ni siquiera por el administrador). | Permisos verificados; intento de modificación falla y genera alerta. | Revisión de permisos en cada despliegue. | RE-5 |
| **RNF-A3** | Toda operación de venta es trazable a su origen: canal, agencia (si aplica), usuario y dirección IP. | 100% de las ventas con origen atribuible. | Consulta de completitud sobre el log. | RE-5 |

> **Respuesta a la pregunta guía — ¿qué tan importante es reconstruir el historial?**
> Es **crítico**, y por tres razones distintas que conviene separar: **(1) comercial** — las disputas por cancelaciones y cambios son frecuentes y sin historial se pierden por defecto; **(2) financiera** — la liquidación de comisiones a agencias (RF-23) es dinero real y debe ser auditable; **(3) analítica** — los "patrones de cancelación" que pide la gerencia comercial en el enunciado **no se pueden calcular sin el historial de transiciones**: una reserva cancelada que solo guarda su estado final no dice ni cuándo se canceló ni cuánto tiempo estuvo viva. Este tercer punto es el que conecta directamente RNF-A1 con la sección 5.7: **el log de auditoría es una fuente del pipeline analítico, no solo un artefacto de cumplimiento.**

---

## Anexo A — Resumen de compromisos asumidos

Toda arquitectura es un conjunto de renuncias. Estas son las del equipo, declaradas:

| Se prioriza | Sobre | Por qué | Dónde se paga |
|---|---|---|---|
| Corrección del inventario (RNF-C1) | Disponibilidad extrema (99,99%) | La sobreventa tiene costo regulatorio; 39 min extra de caída al mes, no. | RNF-D1 se queda en 99,9%. |
| Rechazo rápido | Confirmar y revertir | Revertir una venta confirmada es peor que negarla. | Bloqueo pesimista: menor rendimiento en el punto de contención (DEC-4). |
| Delegar el pago (SUP-14) | Control del flujo de pago | Reduce PCI a SAQ-A. | Dependencia de un tercero; se mitiga con reintentos y conciliación. |
| Un PNR por viaje (DEC-2) | Simplicidad del modelo | El pasajero compra un viaje, no tramos. | Una entidad adicional (`itinerary`) y transacciones más largas. |
| Monolito modular (DEC-6) | Microservicios | La garantía de inventario cabe en una transacción de base de datos. | Escalado menos granular; se acepta a esta escala. |

## Anexo B — Matriz de trazabilidad

_(Se completa en la Parte II con las decisiones DEC-n; aquí queda el esqueleto RF/RNF → riesgo.)_

| Requisito | Riesgo que mitiga | Decisión que lo implementa |
|---|---|---|
| RF-10, RNF-C1, RNF-C2 | RE-1 | DEC-3, DEC-4 |
| RF-01, RF-02, RNF-P1, RNF-P2, RNF-P3 | RE-2, RE-3 | DEC-6, DEC-8 |
| RNF-D1, RNF-D3 | RE-3 | DEC-9 |
| RF-12, RNF-S1, RNF-S4 | RE-4 | DEC-5 |
| RF-18, RNF-A1, RNF-A2 | RE-5 | DEC-10 |
| RNF-E1, RNF-E3 | RE-6 | DEC-8 |
