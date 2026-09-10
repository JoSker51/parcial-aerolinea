---
title: "Sistema de Reservas de una Aerolínea Regional"
subtitle: "Parcial 1 — Big Data e Ingeniería de Datos · 2026-2"
lang: es
---

**Big Data e Ingeniería de Datos · Parcial 1 · 2026-2**

**Equipo:** _(completar)_
**Integrantes:** _(completar)_
**Fecha de entrega:** _(completar)_
**Repositorio:** _(completar enlace de GitHub)_

---

## 0. Cómo leer este documento

Este documento cubre las secciones 5.1 a 5.7 del enunciado. Está organizado para que **cualquier decisión de arquitectura se pueda rastrear hasta el requisito que la origina**, que es el criterio de "Claridad y trazabilidad" de la rúbrica.

### Convenciones de identificadores

| Prefijo | Significado | Dónde se define |
|---|---|---|
| `SUP-n` | Supuesto declarado | §2 |
| `RE-n` | Riesgo de negocio explícito | §5 |
| `RF-nn` | Requisito funcional | §6 |
| `RNF-Xn` | Requisito no funcional (`X` = inicial de la categoría) | §7 |
| `DEC-n` | Decisión de arquitectura | §8 a §13 |
| `D-n` | Desviación declarada entre diseño e implementación | Anexo C |
| `PE-n` | Punto de extensión previsto | §2.3 |

### Cadena de trazabilidad

Todo el documento se sostiene sobre esta cadena, que se recorre completa en la **matriz de trazabilidad de §14**:

```
Riesgo de negocio  →  Requisito (RF/RNF)  →  Decisión (DEC)  →  Código  →  Prueba que lo verifica
```

### Estado de verificación

Todo lo que este documento afirma sobre la implementación fue **ejecutado**, no solo escrito. Los resultados de §12 provienen de una corrida real del 8 de septiembre de 2026 sobre el stack levantado con `docker compose up -d`.

---

## 1. Contexto y alcance

Una aerolínea regional contrata al equipo para construir desde cero su sistema de reservas. Los lineamientos de negocio son deliberadamente incompletos: se conocen las capacidades esperadas (buscar, reservar, pagar, cancelar; venta directa y por agencias; tarifas por anticipación y cabina; itinerarios con escalas; análisis comercial posterior) pero **no hay un solo número**: ni de rutas, ni de tráfico, ni de disponibilidad exigida.

El enunciado es explícito en que esa incompletitud es parte del ejercicio:

> *"Parte del ejercicio es decidir con qué supuestos se trabaja y documentarlos explícitamente: un supuesto no declarado es una decisión de arquitectura oculta."*

De ahí que este documento empiece por los supuestos y no por la tecnología.

### Alcance entregado

| Sección | Alcance | Estado |
|---|---|---|
| 5.1 Requisitos funcionales | 30 RF con actor y prioridad | ✅ |
| 5.2 Requisitos no funcionales | 19 RNF con métrica, umbral y método de verificación | ✅ |
| 5.3 Modelo entidad-relación | 26 tablas, diagrama Crow's Foot, diccionario de datos | ✅ |
| 5.4 Arquitectura backend | 6 decisiones con alternativas descartadas | ✅ |
| 5.5 Arquitectura frontend | 4 decisiones con alternativas descartadas | ✅ (diseño; no se implementa, ver §11.1) |
| 5.6 Implementación del backend | API FastAPI + PostgreSQL, funcionando y probada | ✅ |
| 5.7 AWS: ETL, catálogo y costos | Job de Glue, modelo estrella, 6 pilares, proyección de costos | ✅ |

---

## 2. Supuestos declarados

> **Responde a:** *¿qué cosas asumiste y cómo las resolviste?*

La columna **"Qué se rompe si cambia"** es tan importante como el supuesto mismo: convierte cada supuesto en algo sustentable. En la defensa oral el docente puede variar el escenario, y esa columna es literalmente la respuesta preparada.

### 2.1 Escala y volumen

| ID | Supuesto | Cómo se resolvió | Qué se rompe si cambia |
|---|---|---|---|
| **SUP-1** | Aerolínea regional con **35 rutas** y **18 aeropuertos** | "Regional" en Colombia (tipo Satena/EasyFly) está en el orden de decenas de rutas, no de cientos | Con 500 rutas, la búsqueda con escalas deja de ser un `JOIN` viable y exige un motor de búsqueda precomputado (→ RNF-E1) |
| **SUP-2** | **~120 vuelos/día**; **12 aeronaves** en **3 configuraciones** (ATR-72 de 68 sillas, A320 de 180, A320neo de 186) | 12 aeronaves × ~10 tramos/día. Tres configuraciones justifican separar `aircraft_model` de `aircraft` | Con flota homogénea, el mapa de sillas colapsa a una constante y desaparece la entidad `seat_map` |
| **SUP-3** | Horizonte de venta de **360 días** ⇒ ~43.000 instancias de vuelo vivas | Estándar del sector (los GDS abren 330-360 días) | Dimensiona la tabla más grande del OLTP y el volumen del ETL de §13 |
| **SUP-4** | **30 búsquedas/s** base, **120/s** pico diario, **×5 = 600/s** en temporada alta. Relación búsqueda:reserva ≈ **80:1** | Es la traducción numérica de *"el tráfico se dispara en temporada alta"*. La relación 80:1 es el *look-to-book* típico del sector | Insumo directo de RNF-P1 y RNF-E1. Con una relación 10:1 el cuello de botella se movería de la búsqueda al inventario |
| **SUP-5** | Pico de contención real: **40 solicitudes concurrentes sobre el mismo vuelo** (día de apertura de venta de temporada alta) | Es el escenario que hace fallar el control de sobreventa | Define el mecanismo de concurrencia de DEC-4, y es el número que se prueba en §12 |

### 2.2 Reglas de negocio

| ID | Supuesto | Cómo se resolvió | Qué se rompe si cambia |
|---|---|---|---|
| **SUP-6** | **Sin sobreventa deliberada.** `vendidas + retenidas ≤ capacidad` | Lectura literal del enunciado: *"el sistema no debe vender más sillas de las que tiene el avión"* | **Nada estructural.** Se modeló `oversell_factor` como **columna** y no como constante: permitir 5% de *overbooking* es un `UPDATE`, no un rediseño (→ PE-1) |
| **SUP-7** | **Se permite reservar sin pagar**: estado `HELD` con retención de **20 minutos** | Sin retención el usuario pierde la silla mientras digita la tarjeta. 20 min = checkout con 3-D Secure más margen | Si el pago fuera atómico desaparece `HELD`, desaparece el *job* de expiración, y el bloqueo de inventario tendría que sostenerse durante la llamada al PSP — inaceptable (→ DEC-5) |
| **SUP-8** | **Sí hay cambios de fecha**, modelados como **cancelación + reemisión** en el mismo PNR | Preserva el historial: el tramo viejo queda `CANCELLED`, el nuevo nace `CONFIRMED`. Resuelve la auditabilidad sin lógica adicional | Editar en sitio perdería la trazabilidad exigida por RNF-A1 |
| **SUP-9** | **Sin programa de viajero frecuente en v1**; `passenger.loyalty_id` reservado y nulo | Reduce alcance sin cerrar la puerta | Un programa de millas agrega `loyalty_account` y afecta el **cálculo de precio, no el control de inventario**. Punto de extensión aislado (→ PE-2) |
| **SUP-10** | **Equipaje**: mano incluida; bodega es producto auxiliar (`ancillary`). No se rastrean maletas | Separa "vender equipaje" (del sistema de reservas) de "rastrear maletas" (de operaciones) | Sin `ancillary`, los ingresos por equipaje no aparecerían en el análisis de ingresos por tarifa (→ §13) |
| **SUP-11** | **Silla**: opcional y con costo en económica al comprar, gratis en ejecutiva, **obligatoria en el check-in**. **No** participa del control de sobreventa | Es la decisión clave: el inventario se controla por **conteo por cabina** (→ DEC-3) | Si la silla fuera obligatoria al comprar, el punto de contención pasa de una fila por cabina a una fila por silla: más filas, más bloqueos, sin ganancia de negocio |
| **SUP-12** | **Monedas** COP y USD; **idiomas** ES y EN. El precio se almacena en la moneda de venta, no se reconvierte | Un precio reconvertido al vuelo hace que un tiquete "cambie de precio" al consultarlo: inaceptable contable y legalmente | Más monedas no cambian el modelo, solo el catálogo y la fuente de tasas |
| **SUP-13** | **Agencias**: tarifa neta (descuento) **y** comisión liquidada mensualmente. Contrato, cupo de crédito y credenciales propias | El enunciado ofrece "descuento, comisión" como opciones; se toman ambas porque son el modelo B2B estándar | Define la necesidad de la API B2B separada (→ DEC-7) y de la entidad `agency` |
| **SUP-14** | **Pago totalmente delegado** a un PSP externo (*hosted checkout* + webhook). El sistema **nunca** ve el número de tarjeta | Reduce el alcance PCI-DSS de **SAQ-D a SAQ-A**: la diferencia entre una auditoría de meses y un cuestionario | Procesar la tarjeta en casa cambiaría RNF-S1 por completo y haría inviable el proyecto para una aerolínea regional |
| **SUP-15** | El sistema es **fuente de verdad de la venta, no de la operación**. Retrasos y cambios de aeronave *entran* desde el sistema de operaciones | Evita que el parcial se convierta en un sistema de despacho | `flight_instance` tiene estado y aeronave porque debe **recibir** esos eventos, aunque no los origine |

### 2.3 Punto de extensión para la restricción del equipo (sección 3 del enunciado)

> **PE-0.** A la fecha de redacción el equipo aún no había recibido la restricción confidencial. El documento está construido para absorberla en **tres puntos concretos, sin rediseño**:
>
> 1. **Supuestos** → se agrega como `SUP-16` en §2, con su columna de impacto.
> 2. **Requisitos** → se agrega como `RF-xx` y/o `RNF-Xn` en §6/§7 y se referencia desde la matriz de §14.
> 3. **Decisiones** → se documenta como `DEC-n` con su alternativa descartada.
>
> Ejemplos de cómo aterrizaría, para demostrar que el diseño es robusto a ella:
>
> | Restricción hipotética | Dónde impacta |
> |---|---|
> | "Permitir *overbooking* del 5%" | Solo `flight_inventory.oversell_factor` (ya previsto en SUP-6). **Cero cambios estructurales.** |
> | "Los datos deben permanecer en territorio nacional" | RNF-S3 + elección de región AWS en §13. Afecta costo, no modelo. |
> | "Una agencia solo ve un cupo asignado, no la disponibilidad real" | Nueva entidad `agency_allotment` colgando de `flight_inventory`; el bloqueo de DEC-4 pasa a operar sobre dos filas en la misma transacción. |
> | "Debe operar sin conexión en aeropuertos remotos" | **El caso de mayor impacto**: obliga a inventario particionado con reconciliación y **rompe** la garantía de serialización de DEC-4. Se documentaría como excepción explícita. |

---

## 3. Respuestas a las preguntas guía del enunciado

> **Responde a:** *¿cómo resolviste las preguntas guía?*
>
> El enunciado plantea 22 preguntas guía repartidas entre 5.1 y 5.7. Aquí están **todas**, respondidas de forma directa, con el enlace a la sección donde se desarrolla el argumento. Esta sección existe para que el lector pueda verificar de un vistazo que ninguna quedó sin resolver.

### 3.1 Sobre requisitos funcionales (5.1)

| # | Pregunta del enunciado | Respuesta |
|---|---|---|
| 1 | ¿Quiénes son los actores? ¿Interactúan con el mismo sistema o con interfaces distintas? | **Siete actores** (§4). **Interfaces distintas sobre el mismo backend y el mismo dominio.** Si cada canal tuviera su propia lógica de inventario, la garantía de no-sobreventa habría que probarla cuatro veces y se rompería la primera vez que los canales divergieran → DEC-6, DEC-7 |
| 2 | ¿Reserva multi-tramo: una reserva con varios vuelos, o varias reservas encadenadas? | **Una sola reserva (un PNR) con `itinerary` como entidad intermedia.** El pasajero compró **un viaje**, no tres vuelos: si se cancela el primer tramo la aerolínea tiene una obligación sobre el itinerario completo. Además el pago es uno solo, y una ida-y-vuelta no se puede tarifar por tramo suelto → DEC-2, RF-08 |
| 3 | ¿Se permite reservar sin pagar de inmediato? | **Sí: retención de 20 minutos.** El pago **no** es atómico con la reserva, pero **la retención sí es atómica** con la verificación de inventario. Esa distinción es el corazón del diseño de concurrencia → SUP-7, RF-10, DEC-5 |
| 4 | ¿Qué pasa si el pasajero cancela o cambia la fecha? | Ambas operan sobre el **itinerario**, no sobre el tramo aislado. Cancelar libera inventario **inmediatamente** y calcula reembolso según regla tarifaria. Cambiar = cancelar + reemitir en el mismo PNR con penalidad y diferencia. **Nada se borra**: todo queda como transición auditada → RF-16, RF-17, SUP-8 |
| 5 | ¿Las agencias tienen flujo diferente? | **Mismo proceso de dominio; distinto contrato de API, distinta tarifa y distinto medio de pago.** La agencia no paga con tarjeta: consume cupo de crédito y se le liquida mensualmente. Reutilizar el dominio garantiza que la regla de no-sobreventa sea idéntica en ambos canales → DEC-7, RF-20 a RF-23 |

### 3.2 Sobre requisitos no funcionales (5.2)

| # | Pregunta del enunciado | Respuesta |
|---|---|---|
| 6 | ¿Qué tiempo de respuesta es aceptable para una búsqueda? ¿Cambia con escalas? | **p95 ≤ 400 ms** directos, **p95 ≤ 1.200 ms** con escalas. **Sí cambia, y por una razón estructural**: buscar un vuelo directo es una consulta indexada; buscar con escalas es una búsqueda de caminos de longitud 2-3 en el grafo de la red con validación de tiempo mínimo de conexión en cada nodo. Igualar los umbrales obligaría a precomputar todos los itinerarios, costo que a 35 rutas no se justifica → RNF-P1, RNF-P2 |
| 7 | Si dos pasajeros piden la última silla al tiempo, ¿qué debe garantizar el sistema? | Que **uno gane de forma determinista y el otro reciba un rechazo inmediato y claro** — no un error genérico, no una confirmación optimista que luego se revierte, y en ningún caso dos confirmaciones. **Se prefiere explícitamente rechazar rápido a confirmar y revertir**: revertir una venta confirmada tiene costo regulatorio (RAC 3.10) que un rechazo no tiene. Esa preferencia es la que justifica el bloqueo pesimista → RNF-C1, DEC-4 |
| 8 | ¿Qué número traduce "no se puede caer en temporada alta"? ¿Qué implica en costo? | **99,9% mensual** (43,2 min), reforzado a **99,95%** en ventanas declaradas de temporada alta. **No 99,99%**, porque exige multi-región activa, y una base escribible en dos regiones no puede garantizar barato la serialización del inventario: habría que elegir entre disponibilidad extrema y no-sobreventa. **Se prioriza la corrección.** Costo cuantificado del salto Single-AZ → Multi-AZ: **+28,58 USD/mes (+63%)** → RNF-D1, §13.4 |
| 9 | ¿Cómo se protegen los datos de pago y personales? ¿Aplica regulación? | **Tres marcos concurrentes.** (1) **PCI-DSS**: delegar todo al PSP reduce el alcance a **SAQ-A**, el nivel más bajo — es una decisión de arquitectura tomada por una razón regulatoria. (2) **Ley 1581 de 2012 (Habeas Data)** y Decreto 1377 de 2013: autorización previa, finalidad declarada, derecho de supresión; de ahí la retención acotada a 5 años y la anonimización. (3) **RGPD**, aplicable si se vende a residentes de la UE; se satisface con el mismo mecanismo → RNF-S1 a RNF-S5 |
| 10 | Con 20 rutas nuevas, ¿cuál sería el primer cuello de botella? | **No el volumen de datos: la explosión combinatoria de la búsqueda con escalas.** Los tramos directos crecen linealmente (35→55 rutas ≈ +57% de filas, que un índice absorbe). Pero los itinerarios con conexión crecen con el número de **pares** de rutas que comparten aeropuerto: aproximadamente cuadrático (de ~10² a ~3×10² caminos). El primer componente en saturarse es la **CPU de la base durante la búsqueda de itinerarios** → RNF-E1 |
| 11 | ¿Qué tan importante es reconstruir el historial de una reserva? | **Crítico, por tres razones distintas.** (1) *Comercial*: sin historial las disputas por cancelaciones se pierden por defecto. (2) *Financiera*: la liquidación de comisiones a agencias es dinero real y debe ser auditable. (3) *Analítica*: **los patrones de cancelación que pide la gerencia no se pueden calcular sin el historial de transiciones** — una reserva cancelada que solo guarda su estado final no dice ni cuándo se canceló ni cuánto vivió. Este tercer punto es el que conecta RNF-A1 con §13 → RNF-A1 a RNF-A3, DEC-10 |

### 3.3 Sobre el modelo entidad-relación (5.3)

| # | Pregunta del enunciado | Respuesta |
|---|---|---|
| 12 | ¿Cómo se modela un itinerario multi-tramo? ¿La reserva tiene muchos vuelos, o el itinerario es entidad propia? | **Entidad propia.** Cadena `reservation → itinerary → itinerary_segment → flight_instance`. **El itinerario es la unidad de cancelación y tarifación; el tramo es la unidad de inventario.** Son granos distintos y necesitan entidades distintas → DEC-2 |
| 13 | ¿La silla es parte de la reserva o un proceso separado (check-in)? | **Proceso separado.** Y no es un detalle de conveniencia: **desacoplar la silla de la venta es lo que permite que el control de capacidad sea barato** → DEC-3, SUP-11 |
| 14 | ¿Cómo se representa que el mismo vuelo se repite todos los días? | **Dos entidades**: `scheduled_flight` (plantilla recurrente) e `flight_instance` (fecha concreta). Sin la separación, cambiar un horario obliga a un `UPDATE` sobre ~250 filas con riesgo de tocar fechas vendidas; y **la capacidad no es del programado sino de la instancia** → DEC-1 |
| 15 | ¿Dónde vive el control de inventario para evitar la sobreventa? | En **`flight_inventory`**, una fila por **(vuelo × cabina)**, con `capacity`, `seats_sold` y `seats_held`. Ahí y solo ahí. Es la fila que se bloquea → DEC-3, DEC-4 |
| 16 | ¿Qué normalización se justifica, dado que alimenta el OLTP y el pipeline de Big Data? | **3FN estricta en el OLTP con tres excepciones nombradas; el OLAP es otro modelo (estrella) construido en el ETL.** Un solo modelo para ambos fines queda **mal normalizado para transaccionar y mal desnormalizado para analizar** → §8.2 |

### 3.4 Sobre la arquitectura backend (5.4)

| # | Pregunta del enunciado | Respuesta |
|---|---|---|
| 17 | ¿Monolito o servicios separados? ¿Qué RNF respalda la elección? | **Monolito modular** (4 módulos, un despliegue, una base) con réplicas de lectura. **El RNF que decide es RNF-C1**: retener un itinerario multi-tramo exige actualizar N filas atómicamente, lo que con una base única **es una transacción ACID**. Con servicios separados sería una **saga con compensaciones**, y durante la ventana de compensación el sistema está temporalmente sobrevendido → DEC-6 |
| 18 | ¿Cómo se evita la sobreventa con múltiples solicitudes simultáneas? | **Bloqueo pesimista** `SELECT … FOR UPDATE` sobre la fila de `flight_inventory`, en **orden canónico** para evitar abrazos mortales, con `lock_timeout` de 3 s y una **restricción `CHECK` en la base** como última línea de defensa. Las tres alternativas evaluadas y por qué se descartaron: §9.2 → DEC-4 |
| 19 | ¿Cómo se integra el pago? ¿Delegado o propio? | **Se delega el manejo del instrumento de pago, pero el sistema conserva la máquina de estados de la venta.** No es lo mismo: el PSP no sabe nada de inventario ni de retenciones. Si el webhook llega después de expirar la retención, el sistema debe reintentar retener y, si el vuelo se llenó, **reembolsar automáticamente** → DEC-5 |
| 20 | ¿Misma API para agencias o API B2B separada? | **Dos superficies de API sobre los mismos servicios de dominio.** Separadas porque son contratos con ciclos de vida distintos (la web se cambia el martes; un integrador B2B necesita meses de aviso). Dominio compartido porque duplicarlo rompería la garantía de RNF-C1 → DEC-7 |
| 21 | ¿Cómo se relaciona la arquitectura transaccional con el pipeline de Big Data? | **Regla dura: el pipeline nunca consulta el primario transaccional.** El ETL lee de la **réplica de lectura** en ventana nocturna, con extracción incremental por marca de agua. Es la traducción arquitectónica de RNF-P4 → DEC-11, §13 |

### 3.5 Sobre la arquitectura frontend (5.5)

| # | Pregunta del enunciado | Respuesta |
|---|---|---|
| 22 | ¿SPA o renderizado en servidor? ¿Qué RNF pesa? | **Híbrido.** SSR para descubrimiento (SEO = adquisición gratuita) y SPA para el flujo de compra. **El tiempo que importa comercialmente no es el de la API, es el tiempo hasta el primer resultado visible**: con SPA pura hay tres saltos en serie. Elegir un solo modo optimiza la mitad del recorrido → DEC-12 |
| 23 | ¿Cómo se notifica en tiempo real que la silla ya no está disponible? | **Tres capas**, de menor a mayor costo: reloj de retención (el caso mayoritario: si ya retuvo, **no puede perderla**), revalidación antes de retener, y **SSE** solo en el mapa de sillas. La mejor forma de no dar una mala noticia es que no ocurra → DEC-13 |
| 24 | ¿Interfaz separada para agentes de agencia? | **Sí, aplicación separada.** El agente no es un pasajero con permisos extra, es **otro oficio**: vende 40 tiquetes al día para terceros, necesita cupo de crédito visible, liquidación de comisiones y atajos de teclado → DEC-14 |
| 25 | ¿Qué implica manejar múltiples monedas o idiomas? | Más que traducir textos: rutas con prefijo por idioma (indexables), **moneda fija durante la reserva** (el frontend nunca convierte), formateo con `Intl`, horarios **siempre en hora local del aeropuerto** (convención del sector: mostrar la hora local del usuario hace perder vuelos), y **nombres de aeropuertos traducidos en la base, no en archivos del cliente** → DEC-15 |

### 3.6 Sobre AWS, ETL y costos (5.7)

| # | Pregunta del enunciado | Respuesta |
|---|---|---|
| 26 | ¿Qué tablas copiar a la analítica? ¿Tal cual o transformadas? | **Transformadas.** Tres hechos, uno por pregunta de negocio. Copiar `ticket` tal cual reproduciría el problema del OLTP: seis `JOIN` en cada consulta de BI → §13.2 |
| 27 | ¿El esquema analítico debe ser idéntico al transaccional? | **No.** 3FN para escribir con integridad vs. estrella para leer agregando. El ETL **es** la traducción entre ambos mundos → §13.2 |
| 28 | ¿Qué servicio implementa el ETL? ¿Qué opciones hay en el Learner Lab? | **Glue Python Shell (0,0625 DPU).** Descartados: Glue Spark (mínimo 2 DPU, ~16× más caro), Lambda (límite duro de 15 min), DMS (replica pero no transforma, e instancia siempre encendida) → §13.2 |
| 29 | ¿Cómo se conectan ambas bases al Glue Data Catalog? | Conexión JDBC + rol IAM + red + crawler. **La pieza que más falla es el Security Group auto-referenciado**, y el despliegue real precisó el detalle: Glue exige que el grupo se abra a sí mismo en **todos los puertos**, no solo en el 5432, o el crawler falla con `At least one security group must open all ingress ports` → §13.2 |
| 30 | ¿El ETL corre una vez, periódicamente o por eventos? | **Diario a las 03:00 COT, incremental.** Las tres preguntas de gerencia son **tácticas, no operativas**: nadie cambia una ruta por lo que pasó hace diez minutos → §13.2 |

---

## 4. Actores del sistema

**Cuatro superficies de entrada, un solo modelo de reserva y un solo control de inventario.**

| Actor | Tipo | Interfaz | Autenticación | Nota |
|---|---|---|---|---|
| **A1 — Pasajero** | Humano, externo | Web pública (SSR + SPA), móvil | Opcional: compra como invitado o con cuenta; consulta por PNR + apellido | El 100% del tráfico de búsqueda viene de aquí (SUP-4) |
| **A2 — Agente de agencia** | Humano, externo | Consola B2B separada | OAuth2 *client credentials* por agencia + usuario nominal | Ve tarifas netas, cupo de crédito y liquidación (SUP-13) |
| **A3 — Personal de aeropuerto** | Humano, interno | Consola operativa | SSO corporativo + rol | Asigna sillas y hace check-in. **No vende** |
| **A4 — Administrador comercial** | Humano, interno | Back-office | SSO + rol, **MFA obligatorio** | Crea rutas, vuelos y tarifas. Sus cambios son los de mayor impacto: por eso RNF-A1 los audita |
| **A5 — Proveedor de pagos (PSP)** | Sistema, externo | Webhook entrante + API saliente | Firma HMAC del webhook | Actor no humano, pero es **quien confirma la venta** (SUP-14) |
| **A6 — Sistema de operaciones** | Sistema, externo | API de ingesta de eventos | mTLS / API key interna | Origina retrasos y cambios de aeronave (SUP-15) |
| **A7 — Analista de negocio** | Humano, interno | BI sobre la base OLAP | SSO + rol de solo lectura | **No toca el OLTP.** Es la razón de ser de §13 |

---

## 5. Riesgos de negocio

> **Responde a:** *¿qué riesgos podrían haber?*
>
> La rúbrica exige que las métricas de los RNF sean *"trazables a un riesgo de negocio explícito"*. Los riesgos se nombran aquí y **cada RNF de §7 apunta a uno**.

| ID | Riesgo | Impacto si se materializa | Probabilidad sin mitigación | Mitigado por |
|---|---|---|---|---|
| **RE-1** | **Sobreventa.** Se venden más sillas que la capacidad | Denegación de embarque: compensación regulatoria (RAC 3.10 en Colombia), reubicación, daño reputacional. **Es el riesgo que el propio enunciado señala** | **Alta** — se materializa en la primera venta concurrente | DEC-3, DEC-4, RNF-C1, RNF-C2 |
| **RE-2** | **Abandono por lentitud.** La búsqueda tarda y el usuario se va a un metabuscador | Pérdida directa de ingreso; en viajes cada segundo adicional cuesta conversión de forma medible | Media-alta en pico | DEC-8, DEC-12, RNF-P1 a RNF-P3 |
| **RE-3** | **Caída en temporada alta** | Concentración del ingreso: una hora caída en apertura de temporada vale mucho más que una hora en febrero | Media | DEC-9, RNF-D1, RNF-D3 |
| **RE-4** | **Fuga de datos personales o de pago** | Sanción de la SIC (Ley 1581); pérdida del contrato de adquirencia si es PCI | Baja, impacto extremo | SUP-14, RNF-S1 a RNF-S5 |
| **RE-5** | **Disputa comercial no reconstruible.** Un pasajero o agencia reclama y no se puede probar qué pasó | Pérdida del reclamo por defecto; fraude interno no detectable | Media | DEC-10, RNF-A1 a RNF-A3 |
| **RE-6** | **Crecimiento que degrada.** Se agregan rutas y el sistema se vuelve más lento | El sistema deja de habilitar el crecimiento y pasa a limitarlo | Media a 12 meses | DEC-8, RNF-E1 a RNF-E3 |

---

## 6. Requisitos Funcionales (5.1)

> **Responde a:** *dinos dónde se reflejan.*
>
> La columna **"Dónde se refleja"** apunta al artefacto concreto —endpoint, tabla, función o prueba— que implementa el requisito. Un requisito sin esa columna es una intención; con ella es un compromiso verificable.

**Escala de prioridad:** `Must` = sin esto no hay producto vendible · `Should` = necesario para operar bien, puede diferirse · `Could` = deseable, primer candidato a recortar.

**Estado:** ✅ implementado y probado · ◐ implementado parcialmente · ○ diseñado, fuera del alcance de 5.6 (declarado en §11.1)

### 6.1 Búsqueda y catálogo

| ID | Requisito | Actor | Prior. | Dónde se refleja | Estado |
|---|---|---|---|---|---|
| **RF-01** | Buscar vuelos por origen, destino, fecha, número y tipo de pasajeros y cabina | A1, A2 | Must | `GET /api/v1/flights/search` · `search.py::_DIRECT_SQL` · prueba `test_search_returns_direct_flights` | ✅ |
| **RF-02** | Devolver itinerarios **directos y con escalas** (hasta 2 conexiones) con tiempo total y de conexión | A1, A2 | Must | `search.py::_ONE_STOP_SQL` · prueba `test_search_returns_connections` | ✅ |
| **RF-03** | Filtrar y ordenar por precio, duración, escalas y hora | A1, A2 | Should | Se resuelve en el frontend sobre el conjunto devuelto (DEC-12) | ○ |
| **RF-04** | Precio total desglosado: tarifa + impuestos + cargos + auxiliares, en la moneda de la sesión | A1, A2 | Must | `schemas.py::ItineraryOffer` (`fare_amount`, `tax_amount`, `total_amount`, `price_per_passenger`) | ✅ |
| **RF-05** | Búsqueda por fechas flexibles (±3 días) con tarifa mínima por día | A1 | Could | Multiplica ×7 el costo de búsqueda: primer candidato a caché agresivo (DEC-8) | ○ |
| **RF-06** | Consultar el mapa de sillas con su estado y costo de selección | A1, A2, A3 | Should | Tablas `seat_map`, `seat_map_seat`, `seat_assignment` | ◐ |
| **RF-07** | Exponer catálogo de aeropuertos, rutas y equipajes | A1, A2 | Should | Tablas `airport`, `route`, `fare_rule.baggage_pieces` | ◐ |

### 6.2 Reserva

| ID | Requisito | Actor | Prior. | Dónde se refleja | Estado |
|---|---|---|---|---|---|
| **RF-08** | Crear una **reserva única (PNR)** que agrupe itinerarios, tramos ordenados y pasajeros en una transacción | A1, A2 | Must | `POST /api/v1/reservations` · `booking.py::create_reservation` · tablas `reservation`/`itinerary`/`itinerary_segment` | ✅ |
| **RF-09** | Validar coherencia del itinerario: continuidad geográfica, tiempo mínimo de conexión y no solapamiento | A1, A2 | Must | `booking.py::_validate_itinerary` · `airport.min_connection_min` · prueba `test_invalid_connection_is_rejected` | ✅ |
| **RF-10** | **Retener inventario de todos los tramos de forma atómica** por 20 min garantizando `vendidas + retenidas ≤ capacidad` | A1, A2 | **Must — crítico** | `booking.py::_LOCK_INVENTORY_SQL` + `ck_inventory_no_oversell` · pruebas `test_no_oversell_under_concurrency` (4 casos) | ✅ |
| **RF-11** | Liberar automáticamente las retenciones vencidas | Sistema | Must | `POST /internal/jobs/expire-holds` · `booking.py::expire_holds` con `SKIP LOCKED` · prueba `test_expired_hold_releases_inventory` | ✅ |
| **RF-12** | Confirmar la reserva (`HELD` → `CONFIRMED`) solo al recibir confirmación de pago | A5 | Must | `POST /api/v1/reservations/{pnr}/confirm` · `booking.py::confirm_reservation` | ✅ |
| **RF-13** | Generar PNR de 6 caracteres, único, legible y **no secuencial** | Sistema | Must | `booking.py::_PNR_ALPHABET` sin `I`,`O`,`0`,`1` + `secrets.choice` · `UNIQUE (pnr)` | ✅ |
| **RF-14** | Emitir un tiquete por **(pasajero × tramo)** con su clase y estado propios | Sistema | Must | Tabla `ticket` con `UNIQUE (segment_id, reservation_id, passenger_ref)` · prueba `test_reservation_lifecycle` | ✅ |
| **RF-19** | Comprar productos auxiliares (equipaje, selección de silla) | A1, A2 | Should | Tabla `ancillary`; el ETL ya suma `ancillary_amount` al hecho | ◐ |

### 6.3 Post-venta

| ID | Requisito | Actor | Prior. | Dónde se refleja | Estado |
|---|---|---|---|---|---|
| **RF-15** | Consultar reserva por PNR + apellido, o listar las de una cuenta | A1, A2 | Must | `GET /api/v1/reservations/{pnr}?last_name=` · `booking.py::get_reservation` | ✅ |
| **RF-16** | Cancelar reserva o itinerario liberando inventario **inmediatamente** y calculando reembolso | A1, A2 | Must | `POST /api/v1/reservations/{pnr}/cancel` · `booking.py::cancel_reservation` | ✅ |
| **RF-17** | Cambiar fecha como cancelación + reemisión en el mismo PNR, con penalidad y diferencia | A1, A2 | Should | Reutiliza el mismo camino de retención de RF-10 (SUP-8) | ◐ |
| **RF-18** | Registrar **toda** transición de estado de forma **inmutable** | Sistema | Must | Tabla `reservation_event` + trigger `trg_reservation_event_immutable` · `booking.py::_audit` | ✅ |

### 6.4 Canal B2B (agencias)

| ID | Requisito | Actor | Prior. | Dónde se refleja | Estado |
|---|---|---|---|---|---|
| **RF-20** | Autenticar agencias con credenciales propias y cuotas independientes | A2 | Must | Tabla `agency`; el contrato de API B2B se diseña en DEC-7 | ◐ |
| **RF-21** | Aplicar **tarifa neta** con descuento contractual y registrar la **comisión** devengada | A2 | Must | `create_reservation` (`discount_pct`) y `confirm_reservation` (`commission_amount` en `ticket`) | ✅ |
| **RF-22** | Reservar contra **cupo de crédito** validando disponible antes de confirmar | A2 | Must | `agency.credit_used/credit_limit` + `CHECK (credit_used <= credit_limit)`, debitado **en la misma transacción** que la retención | ✅ |
| **RF-23** | Generar liquidación mensual por agencia | A4 | Should | Consumidor de la base OLAP: `dim_channel.agency_name` + `fact_ticket_sale.commission_amount` | ◐ |

### 6.5 Operación y administración

| ID | Requisito | Actor | Prior. | Dónde se refleja | Estado |
|---|---|---|---|---|---|
| **RF-24** | Administrar catálogo: aeropuertos, rutas, aeronaves, mapas de silla, vuelos programados | A4 | Must | Tablas de los bloques 1 y 2 de `sql/01_schema.sql` | ◐ |
| **RF-25** | **Materializar** instancias de vuelo desde los programados y crear su inventario | Sistema | Must | `sql/02_seed.sql`, bloque "Materialización". En producción, *job* diario | ✅ |
| **RF-26** | Administrar tarifas y reglas: clase, cabina, anticipación, penalidades, reembolsabilidad | A4 | Must | Tablas `fare`, `fare_class`, `fare_rule` · usadas por `_ELIGIBLE_FARE_SQL` | ✅ |
| **RF-27** | Asignar sillas y hacer check-in desde 24 h antes | A1, A3 | Should | Tabla `seat_assignment` con `UNIQUE (flight_instance_id, seat_number)` | ◐ |
| **RF-28** | Recibir eventos operativos y actualizar la instancia y su inventario | A6 | Should | `flight_instance.status` y `aircraft_id` preparados para recibirlos (SUP-15) | ○ |
| **RF-29** | Notificar por correo confirmación, cancelación y cambios | Sistema | Should | — | ○ |
| **RF-30** | Exponer al pipeline analítico los datos sin impactar el rendimiento transaccional | A7 | Must | `etl/glue_job_oltp_to_olap.py` · lectura desde réplica (DEC-11) | ✅ |

**Cobertura: 30 RF, de los cuales 17 son `Must`.** Los tres que la rúbrica evalúa de forma específica —distinguir **reserva de compra** (RF-10 vs RF-12), manejar **itinerarios multi-tramo** (RF-08, RF-09) y prevenir **sobreventa** (RF-10)— están cubiertos de forma explícita, separada y **probada**.

---

## 7. Requisitos No Funcionales (5.2)

> **Responde a:** *dinos las métricas concretas trazables.*
>
> Cada RNF lleva **métrica**, **umbral**, **cómo se verifica**, **riesgo de negocio** que mitiga y **dónde se refleja**. Un RNF que no se puede medir no es un requisito, es un deseo.

### 7.1 Rendimiento

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo | Dónde se refleja |
|---|---|---|---|---|---|
| **RNF-P1** | Búsqueda de vuelos **directos** rápida bajo carga normal | **p95 ≤ 400 ms**, **p99 ≤ 800 ms** con 120 búsquedas/s (SUP-4) | Prueba de carga k6 sobre "búsqueda directa"; percentiles en CloudWatch | RE-2 | Índice `ix_flight_instance_search` · vista `v_flight_availability` · caché TTL 60 s (DEC-8) |
| **RNF-P2** | Búsqueda **con escalas** acotada | **p95 ≤ 1.200 ms** hasta 2 conexiones | Igual, escenario "con conexiones" | RE-2 | `search.py::_ONE_STOP_SQL` con ventana máxima de conexión |
| **RNF-P3** | En temporada alta (600 búsquedas/s) degrada de forma controlada, no cae | p95 ≤ 1.000 ms directos; **error < 0,5%**; cero 5xx por saturación de conexiones | Prueba de carga ×5 **antes** de cada temporada alta | RE-3 | Réplicas de lectura, autoescalado y capacidad preaprovisionada (DEC-8, DEC-9) |
| **RNF-P4** | La carga analítica **no degrada** al transaccional | El p95 de RNF-P1 no se deteriora más del **10%** mientras corre el ETL | Medir p95 durante y fuera de la ventana de ETL | RE-2, RE-6 | ETL lee de **réplica**, no del primario (DEC-11) · ventana 03:00 |

### 7.2 Consistencia y concurrencia

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo | Dónde se refleja |
|---|---|---|---|---|---|
| **RNF-C1** | **Nunca se confirma más inventario que la capacidad.** Ante N solicitudes por K sillas, exactamente **K** ganan y **N−K** reciben rechazo claro | `seats_sold + seats_held ≤ capacity` **siempre**; violaciones = **0**, verificado con **40 solicitudes concurrentes** (SUP-5) | Prueba automatizada con N hilos reales contra la API **más** restricción `CHECK` en la base | **RE-1** | `booking.py::_LOCK_INVENTORY_SQL` · `ck_inventory_no_oversell` · vista `v_oversell_check` · **§12** |
| **RNF-C2** | La retención multi-tramo es **atómica**: todos los tramos o ninguno | Cero reservas con tramos parcialmente retenidos | Fallo inyectado en un tramo; se comprueba que los anteriores quedaron liberados | RE-1 | Verificación de **todos** los tramos antes de escribir · prueba `test_multi_segment_hold_is_atomic` |
| **RNF-C3** | Retención vencida libera inventario en **≤ 60 s** | Diferencia entre `expires_at` y liberación efectiva ≤ 60 s en p99 | Monitor de antigüedad de retenciones vencidas no liberadas | RE-1 (invertido: inventario fantasma) | `expire_holds` con `SKIP LOCKED` · índice `ix_reservation_expiry` |
| **RNF-C4** | La confirmación de pago es **idempotente** | Reenviar el mismo evento **10 veces** produce exactamente **1** confirmación | Prueba automatizada de reentrega | RE-5 | `UNIQUE (payment_event.psp_event_id)` · prueba `test_confirm_is_idempotent` |

### 7.3 Disponibilidad

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo | Dónde se refleja |
|---|---|---|---|---|---|
| **RNF-D1** | Disponibilidad de búsqueda y reserva: **99,9% mensual**, **99,95%** en temporada alta | 99,9% = **43,2 min**/mes · 99,95% = **21,6 min**/mes. *Health checks* externos cada 30 s | Reporte mensual de SLO con presupuesto de error consumido | RE-3 | RDS **Multi-AZ** · ≥2 instancias en AZ distintas · despliegue azul-verde (DEC-9) |
| **RNF-D2** | Post-venta (consulta, check-in) tolera más: **99,5%** | 3,6 h/mes | Igual, por endpoint | — | Endpoints separados del camino crítico |
| **RNF-D3** | **RPO ≤ 5 min**, **RTO ≤ 1 h** para la base transaccional | Prueba de restauración semestral documentada | Simulacro de restauración | RE-3, RE-5 | Respaldos automáticos + PITR (DEC-9) |

### 7.4 Seguridad

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo | Dónde se refleja |
|---|---|---|---|---|---|
| **RNF-S1** | **Nunca** se almacena, registra ni procesa el número de tarjeta. Alcance PCI-DSS = **SAQ-A** | **Cero** ocurrencias de PAN en base, logs o trazas | Escaneo automatizado de patrones de tarjeta en cada despliegue | **RE-4** | Tabla `payment` guarda solo `psp_intent_id` opaco (SUP-14, DEC-5) |
| **RNF-S2** | Acceso a reserva solo con PNR **+** apellido; **5 intentos fallidos por IP cada 15 min** | Tasa de bloqueo y alerta ante enumeración | Prueba de enumeración en la revisión de seguridad | RE-4 | `get_reservation` devuelve **404** (no 403) ante apellido incorrecto, para no confirmar que el PNR existe · PNR aleatorio (RF-13) |
| **RNF-S3** | Cifrado **en tránsito** (TLS 1.2+) y **en reposo** (AES-256). Retención de datos de pasajero **5 años**, luego anonimización | Auditoría de configuración; *job* de anonimización con reporte | Revisión trimestral | RE-4 | RDS cifrado · `JDBC_ENFORCE_SSL=true` en las conexiones Glue · tabla `passenger` marcada como PII |
| **RNF-S4** | Toda acción de A4 exige **MFA** y queda con identidad nominal | Cero acciones administrativas sin identidad atribuible | Revisión del log de auditoría | RE-5 | `reservation_event.actor_type` / `actor_id` |
| **RNF-S5** | Cada agencia solo lee y modifica **sus propias** reservas | Cero accesos entre agencias | Prueba de autorización en CI | RE-4 | `reservation.agency_id` + índice `ix_reservation_agency` · API B2B separada (DEC-7) |

### 7.5 Escalabilidad

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo | Dónde se refleja |
|---|---|---|---|---|---|
| **RNF-E1** | Agregar **20 rutas** (35→55, +57%) **no degrada** más allá de RNF-P1/P2, sin rediseño | p95 bajo umbral con catálogo ampliado | Prueba de carga con catálogo sintético de 55 rutas **antes** de comprometer las rutas comercialmente | **RE-6** | Caché por (origen, destino, fecha) · réplicas de lectura · plan de mitigación por etapas en §10.3 |
| **RNF-E2** | El tráfico B2B de una agencia no degrada el canal público | Ninguna agencia consume más del **20%** de la capacidad de búsqueda | Límite de tasa por credencial + alerta | RE-2 | Cuota por credencial en la API B2B (DEC-7) |
| **RNF-E3** | La capa de aplicación escala **horizontalmente** | **Eficiencia de escalado ≥ 0,8** al pasar de 2 a 4 instancias | Prueba de carga comparativa | RE-3 | Aplicación **sin estado**: sesión en JWT, caché en Redis, nada en memoria del proceso |

### 7.6 Auditabilidad

| ID | Requisito | Métrica y umbral | Cómo se verifica | Riesgo | Dónde se refleja |
|---|---|---|---|---|---|
| **RNF-A1** | Toda transición de reserva, tiquete o pago se registra de forma **inmutable** con actor, marca de tiempo, canal y estados | **100%** de las transiciones auditadas. Reconstruir el estado de cualquier reserva en cualquier instante pasado **solo con el log** | Prueba de reconstrucción sobre una reserva con ≥5 transiciones | **RE-5** | `booking.py::_audit` **dentro de la misma transacción** que la operación (DEC-10) |
| **RNF-A2** | El log se conserva **7 años** y es inalterable (sin `UPDATE` ni `DELETE`, ni para el administrador) | Intento de modificación falla y genera alerta | Revisión de permisos en cada despliegue | RE-5 | Trigger `trg_reservation_event_immutable`. **Verificado de forma accidental**: las pruebas no pudieron borrar el log y hubo que usar `TRUNCATE` (ver D-7) |
| **RNF-A3** | Toda venta es trazable a su origen: canal, agencia, usuario e IP | 100% de las ventas con origen atribuible | Consulta de completitud sobre el log | RE-5 | `reservation.channel` y `agency_id` · `reservation_event.channel` |

---

## 8. Modelo entidad-relación (5.3)

> **Responde a:** *modelo entidad relación.*

El enunciado sugiere una lista de entidades y advierte que no hay que asumir que sea correcta ni completa. **No lo es**: le faltan cuatro entidades imprescindibles y una de las que nombra —"Vuelo"— es ambigua y hay que partirla en dos.

### 8.1 Las cinco decisiones estructurales

#### DEC-1 — "Vuelo" son dos entidades: `scheduled_flight` e `flight_instance`

| | |
|---|---|
| **Decisión** | Separar el **vuelo programado** (el AV-8101 sale 06:30 BOG→MDE, lun-dom, del 1-ene al 31-dic) de la **instancia de vuelo** (el AV-8101 del 4 de septiembre, con la aeronave HK-4812 asignada) |
| **Alternativa descartada** | Una sola tabla `flight` con una fila por fecha y las reglas de recurrencia repetidas |
| **Por qué** | **(1)** Cambiar el horario de un vuelo recurrente obligaría a un `UPDATE` masivo sobre ~250 filas, con riesgo de tocar fechas ya vendidas. **(2)** **La capacidad no es del vuelo programado sino de la instancia**: si el 4 de septiembre se cambia el A320 (180 sillas) por un ATR-72 (68), esa instancia y solo esa cambia de capacidad; con una sola tabla no hay dónde poner esa realidad. **(3)** El pipeline de §13 necesita el programado como **dimensión** y la instancia como **hecho**; mezclados, el modelo estrella se vuelve un problema |
| **Costo aceptado** | Un proceso de materialización (RF-25) que genera instancias dentro del horizonte de 360 días. Es un *job* diario, no una complicación estructural |
| **Traza** | RF-24, RF-25, RF-28, SUP-2, SUP-15 |

#### DEC-2 — El itinerario es una entidad propia entre la reserva y el vuelo

| | |
|---|---|
| **Decisión** | Cadena de tres niveles: `reservation` (1) → `itinerary` (N) → `itinerary_segment` (N) → `flight_instance` (1) |
| **Alternativa descartada** | Relación directa `reservation N—N flight_instance` con tabla puente plana |
| **Por qué** | Con la tabla puente plana **se pierde la agrupación de viaje**. Tres preguntas que el modelo debe responder y que el puente plano no responde: ¿estos dos tramos son una conexión o dos vuelos independientes comprados el mismo día? ¿Si se cancela el regreso, sobrevive la ida? ¿Cuál es el origen-destino del viaje para reportar ocupación por ruta comercial? **El itinerario es la unidad de cancelación y tarifación; el tramo es la unidad de inventario.** Granos distintos, entidades distintas |
| **Costo aceptado** | Un nivel más de anidamiento en consultas y API |
| **Traza** | RF-08, RF-09, RF-16, RF-17, RNF-C2 |

#### DEC-3 — El inventario se controla por conteo por cabina, no por silla individual

**Es la decisión de mayor impacto de todo el diseño.**

| | |
|---|---|
| **Decisión** | `flight_inventory`, con **una fila por (instancia de vuelo × cabina)**, guarda `capacity`, `seats_sold` y `seats_held`. Ahí y solo ahí vive el control de sobreventa. La **silla física** (`seat_assignment`) es una entidad **separada y opcional** que no participa en el control de capacidad |
| **Alternativa descartada** | Controlar el inventario marcando sillas individuales como ocupadas en un mapa de sillas por instancia |
| **Por qué** | **(1) Es el modelo real del negocio.** Se vende "un asiento en económica", no "el 14C"; el pasajero que no elige silla igual consume inventario. **(2) Concurrencia.** El punto de contención pasa a ser **una sola fila** por cabina, bloqueable de forma corta y determinista. Con sillas individuales, dos compradores del mismo vuelo compiten por filas distintas: nadie bloquea a nadie, pero **nadie controla el total** — habría que contar sillas libres en cada compra, y ese `COUNT` es exactamente la condición de carrera que hay que evitar. **(3) Cambio de aeronave.** Con conteos se compara `seats_sold` contra la nueva capacidad y se sabe al instante a cuántos reacomodar; con sillas asignadas hay que reasignar 180 filas y resolver colisiones |
| **Consecuencia coherente** | Por eso la silla se asigna en el check-in (SUP-11): **desacoplar la silla de la venta es lo que permite que el control de capacidad sea barato.** La unicidad de silla se protege aparte con `UNIQUE (flight_instance_id, seat_number)` — eso es integridad, no capacidad |
| **Confirmado en la implementación** | Fue lo que permitió que la sección crítica cupiera en **una sola consulta de bloqueo**. Si el inventario hubiera vivido en el mapa de sillas, esta parte habría sido considerablemente más compleja |
| **Traza** | RF-10, RF-27, RNF-C1, SUP-11, RE-1 |

#### DEC-4 — Bloqueo pesimista sobre la fila de inventario

El análisis completo de las tres alternativas está en **§9.2**, porque es una decisión de arquitectura tanto como de modelo. Aquí queda su efecto sobre el esquema: la **granularidad** de `flight_inventory` (DEC-3), una columna `version` que permitiría migrar a bloqueo optimista sin cambiar el esquema, y una **restricción a nivel de motor**:

```sql
CONSTRAINT ck_inventory_no_oversell
  CHECK (seats_sold + seats_held <= FLOOR(capacity * oversell_factor))
```

Esta restricción es **deliberadamente redundante** con la lógica de la aplicación. Es la última línea de defensa de RNF-C1: si un error de programación, una migración o una consulta manual intentaran sobrevender, la transacción falla en el motor. **La regla de negocio más importante del sistema no debe vivir únicamente en el código de aplicación.**

`oversell_factor` vale `1.00` por SUP-6. Existe como **columna** y no como constante para que la restricción hipotética "permitir *overbooking* del 5%" sea un `UPDATE` y no un rediseño (→ PE-0).

#### DEC-5 — El precio y las condiciones se congelan en el tiquete

| | |
|---|---|
| **Decisión** | `ticket` almacena `fare_amount`, `tax_amount`, `discount_amount`, `commission_amount`, `currency` y la referencia a la regla tarifaria **vigente al emitir**, en vez de calcular el precio al consultar |
| **Alternativa descartada** | Guardar solo `fare_id` y recomponer el precio con un `JOIN` a la tarifa vigente |
| **Por qué** | Un tiquete es un **contrato**. Si mañana cambia la tarifa o la penalidad, el tiquete vendido ayer no puede cambiar de precio ni de condiciones al consultarlo. Es una **denormalización deliberada de naturaleza temporal**, no un descuido: el dato no es "el precio de la tarifa", es "el precio que este pasajero pagó". Además hace que el análisis de ingresos por tarifa de §13 sea una lectura directa |
| **Corregido al implementar** | Hay que congelar al **retener**, no al emitir: entre el `HELD` y el pago pasan 20 minutos. Ver desviación **D-1** en el Anexo C |
| **Traza** | RF-04, RF-14, RF-21, SUP-12, RNF-A1 |

### 8.2 Nivel de normalización

**3FN estricta en el OLTP con tres excepciones nombradas; el modelo analítico es otro y se construye en el ETL.** El error a evitar es diseñar una sola base "que sirva para todo": queda mal normalizada para transaccionar y mal desnormalizada para analizar.

| Excepción a 3FN | Qué se duplica | Por qué se acepta |
|---|---|---|
| `flight_inventory.seats_sold` / `seats_held` | Agregados derivables de `ticket` e `itinerary_segment` | Calcularlos con `COUNT` en cada reserva sería el **peor punto de contención posible**: un agregado sobre miles de filas dentro de la transacción crítica. Materializarlos convierte el control de RNF-C1 en leer y escribir **una fila**. La consistencia se garantiza porque **solo** el servicio de inventario los modifica, siempre en la misma transacción que crea o cancela el tramo. La vista `v_inventory_reconciliation` permite auditar que el contador y los tramos reales coinciden |
| Montos en `ticket` (DEC-5) | Precio y condiciones del catálogo | Denormalización temporal: es un valor histórico, no una copia |
| `flight_instance.departure_utc` / `arrival_utc` | Derivables de `scheduled_flight` + fecha + zona horaria | La aritmética de zonas horarias con horario de verano dentro de una consulta de búsqueda es cara y propensa a error. Se calcula una vez al materializar y se indexa: habilita el índice que sostiene RNF-P1 |

**Por qué el OLTP no se desnormaliza para analítica:** las consultas de gerencia (ocupación por ruta, ingresos por tarifa, patrones de cancelación) son escaneos agregados sobre millones de filas. Ejecutarlas contra el transaccional competiría por CPU y buffers con la ruta crítica de reserva y **violaría RNF-P4**.

### 8.3 Diagrama entidad-relación — OLTP

Notación **Pata de Gallo (Crow's Foot)**, consistente en todo el diagrama.
Cardinalidad: `||` exactamente uno · `o|` cero o uno · `}|` uno o muchos · `}o` cero o muchos.

![Modelo entidad-relación del sistema transaccional (26 tablas)](diagramas/er-oltp.png)

*Figura 1 — Modelo E-R completo del OLTP. `AIRPORT` aparece dos veces conectado a `ROUTE` porque la ruta tiene dos claves foráneas hacia aeropuerto (origen y destino): es una relación recursiva a través de una entidad asociativa, no un error del diagrama.*

### 8.4 Diagrama del núcleo de concurrencia

La cadena que sostiene RNF-C1, con sus atributos. `flight_inventory` es **la fila que se bloquea**.

![Núcleo de concurrencia con atributos](diagramas/er-nucleo-concurrencia.png)

*Figura 2 — Núcleo del control de inventario. `seats_sold` y `seats_held` son los contadores protegidos por el bloqueo pesimista de DEC-4. `quoted_fare_id` y `quoted_fare_amount` en `itinerary_segment` son la desviación D-1.*

### 8.5 Diccionario de datos

**26 tablas y 4 vistas.** El DDL completo está en `backend/sql/01_schema.sql`.

#### Catálogo de red y flota

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| `airport` | Aeropuertos de la red | `iata_code` (PK), `timezone`, `min_connection_min` | `min_connection_min` es el tiempo mínimo de conexión y lo usa RF-09. **Es por aeropuerto, no global**: conectar en BOG toma más que en SMR |
| `route` | Par origen-destino comercializable | `route_id` (PK), `origin_iata`, `destination_iata` | `UNIQUE (origin, destination)`. **Dirigida**: BOG→MDE y MDE→BOG son dos rutas |
| `aircraft_model` | Modelo de aeronave | `model_code` (PK) | Separado de `aircraft` porque hay 12 aeronaves y 3 modelos (SUP-2) |
| `seat_map` / `seat_map_seat` | Configuración de cabina de un modelo | `seat_map_id`, `seat_number`, `cabin_code` | La **capacidad por cabina se deriva contando** aquí y se copia a `flight_inventory` al materializar |
| `aircraft` | Aeronave física | `aircraft_id` (PK), `registration` (UK) | La matrícula es la identidad ante la autoridad aeronáutica |
| `cabin` | Catálogo: `ECO`, `BUS` | `cabin_code` (PK) | **Entidad, no `ENUM`**: agregar `PREMIUM_ECO` debe ser un `INSERT`, no una migración de tipo |

#### Programación y operación

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| `scheduled_flight` | Plantilla recurrente (DEC-1) | `flight_number`, `route_id`, `departure_time_local`, `days_of_week`, `valid_from`/`valid_to` | `days_of_week` es una cadena de 7 posiciones (`1234500` = lun-vie). `arrival_day_offset` ∈ {0,1} para vuelos que cruzan medianoche |
| `flight_instance` | Vuelo en una fecha concreta (DEC-1) | `flight_instance_id`, `flight_date`, `departure_utc`, `aircraft_id`, `status` | `UNIQUE (scheduled_flight_id, flight_date)` impide materializar dos veces. `status` ∈ `SCHEDULED/DELAYED/DEPARTED/ARRIVED/CANCELLED`. `aircraft_id` es nulo hasta que operaciones lo asigna |
| **`flight_inventory`** | **Control de sobreventa (DEC-3, DEC-4)** | PK `(flight_instance_id, cabin_code)`, `capacity`, `seats_sold`, `seats_held`, `oversell_factor`, `version` | `CHECK (seats_sold + seats_held <= FLOOR(capacity * oversell_factor))` y `CHECK (seats_sold >= 0 AND seats_held >= 0)`. **Es la fila que se bloquea con `SELECT … FOR UPDATE`. La tabla más importante del sistema** |

#### Tarifas

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| `fare_class` | Clase tarifaria (`P`,`Y`,`B` en económica; `J` en ejecutiva) | `fare_class_code` (PK), `cabin_code` | Conecta "tipo de silla" con "precio" |
| `fare_rule` | Condiciones comerciales | `min_advance_days`, `is_refundable`, `refund_penalty`, `is_changeable`, `change_penalty`, `baggage_pieces` | **`min_advance_days` implementa la tarifa por anticipación** del enunciado. Alimenta el reembolso de RF-16 y la penalidad de RF-17 |
| `fare` | Precio de una clase en una ruta, con vigencia | `route_id`, `fare_class_code`, `fare_rule_id`, `currency`, `base_amount`, `valid_from`/`valid_to` | Las vigencias no se solapan para el mismo `(route, fare_class, currency)` |

#### Reserva

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| `reservation` | El PNR: unidad de compra y pago (DEC-2) | `pnr` (UK, 6 car.), `channel`, `agency_id`, `status`, `hold_expires_at`, `total_amount` | `status` ∈ `HELD/CONFIRMED/CANCELLED/EXPIRED`. `hold_expires_at` no nulo solo en `HELD`. PNR aleatorio, sin caracteres ambiguos (RF-13, RNF-S2) |
| `itinerary` | Un viaje dentro del PNR (ida / regreso) | `reservation_id`, `sequence_no`, `origin_iata`, `destination_iata` | **Unidad de cancelación y tarifación** (DEC-2). Origen y destino son los del viaje completo, no los del primer tramo |
| `itinerary_segment` | Un tramo volado (DEC-2) | `itinerary_id`, `sequence_no`, `flight_instance_id`, `cabin_code`, `fare_class_code`, `quoted_fare_*`, `status` | `UNIQUE (itinerary_id, sequence_no)`. **Unidad de inventario**: cada tramo activo consume una silla en su `flight_inventory`. FK compuesta a `flight_inventory (flight_instance_id, cabin_code)` |
| `reservation_passenger` | Pasajeros del PNR | PK `(reservation_id, passenger_ref)` | `passenger_ref` (1,2,3…) es el identificador **local** del pasajero dentro del PNR: permite emitir tiquetes sin exponer el `passenger_id` global. `passenger_type` ∈ `ADT/CHD/INF`; **el infante no consume inventario** |
| `passenger` | Datos personales | `document_type` + `document_number` (UK), `email`, `loyalty_id` | Tabla con **PII**: sujeta a RNF-S3. `loyalty_id` reservado y nulo (SUP-9) |
| `agency` | Agencia aliada (SUP-13) | `net_fare_discount_pct`, `commission_pct`, `credit_limit`, `credit_used` | `CHECK (credit_used <= credit_limit)` respalda RF-22 |

#### Emisión, pago y auditoría

| Entidad | Propósito | Atributos clave | Reglas |
|---|---|---|---|
| `ticket` | Documento de valor: **un tiquete por pasajero × tramo** (RF-14) | `ticket_number` (UK), `segment_id`, `passenger_ref`, `fare_id`, montos congelados, `status` | `UNIQUE (segment_id, reservation_id, passenger_ref)`. Montos congelados (DEC-5). **Es el grano del hecho analítico de §13** |
| `seat_assignment` | Silla física (SUP-11, DEC-3) | PK `ticket_id`, `flight_instance_id`, `seat_number` | `UNIQUE (flight_instance_id, seat_number)`: impide dos pasajeros en la misma silla. **No participa en el control de capacidad** |
| `ancillary` | Producto auxiliar: equipaje, silla (SUP-10) | `ticket_id`, `ancillary_type`, `amount` | Su ingreso entra en el análisis de §13 |
| `payment` | Intento de cobro | `psp_intent_id` (UK), `idempotency_key` (UK), `method`, `status` | `method` ∈ `CARD/AGENCY_CREDIT`. **Sin datos de tarjeta** (RNF-S1): solo la referencia opaca del PSP |
| `payment_event` | Webhooks recibidos del PSP | `psp_event_id` (UK), `payload` (jsonb) | **La unicidad de `psp_event_id` es lo que hace idempotente la confirmación** (RNF-C4): un evento reentregado choca contra el índice y se descarta |
| `refund` | Reembolsos | `payment_id`, `amount`, `status` | Generado por RF-16 según regla tarifaria |
| **`reservation_event`** | **Log de auditoría, solo inserción** (RNF-A1) | `occurred_at`, `actor_type`, `actor_id`, `channel`, `event_type`, `from_status`, `to_status`, `payload` | Trigger que **prohíbe `UPDATE` y `DELETE`** (RNF-A2). Es **fuente del pipeline analítico**: los patrones de cancelación se calculan aquí |

#### Vistas de apoyo

| Vista | Para qué |
|---|---|
| `v_flight_availability` | Disponibilidad expuesta a la búsqueda (RF-01), sin bloqueo |
| `v_seat_map_capacity` | Capacidad por cabina derivada del mapa de sillas, usada al materializar (RF-25) |
| **`v_oversell_check`** | **Debe devolver 0 filas SIEMPRE.** Es la aserción de la prueba de concurrencia de §12 |
| `v_inventory_reconciliation` | Contrasta el contador materializado con los tramos reales: audita la denormalización de §8.2 |

---

## 9. Arquitectura del backend (5.4)

### 9.1 DEC-6 — Monolito modular con lectura separada, no microservicios

**Decisión: un solo despliegue con cuatro módulos de frontera explícita** — `search`, `booking` (que contiene inventario), `payments` y `catalog` — sobre una base PostgreSQL única, más **réplicas de lectura** para la búsqueda.

**El RNF que decide es RNF-C1**, y este es el razonamiento que hay que sostener:

> Retener un itinerario multi-tramo exige verificar y actualizar **N filas de `flight_inventory`** (una por tramo) de forma **atómica** (RNF-C2). Con una base única, eso es **una transacción ACID** y el problema lo resuelve el motor. Si `search`, `booking` e `inventory` fueran servicios con bases separadas, la misma garantía exigiría una **saga con compensaciones**: retener el tramo 1, retener el 2, fallar en el 3 y compensar los dos primeros. Durante la ventana de compensación el sistema está **temporalmente inconsistente** — es decir, temporalmente sobrevendido. Se puede construir, pero se estaría **pagando complejidad distribuida para debilitar la garantía que el enunciado señala como innegociable.**

| Aspecto | Decisión | Justificación |
|---|---|---|
| Separación de lectura | `search` consulta **réplicas**; `booking` va siempre al primario | Es el 98,7% del tráfico (relación 80:1, SUP-4) y tolera segundos de desfase: mostrar disponibilidad ligeramente vieja es aceptable porque **la verdad se verifica al retener** → RNF-P1, RNF-P3 |
| Sin estado | Sesión en JWT, caché en Redis, nada en memoria del proceso | Habilita el escalado horizontal → RNF-E3 |
| Fronteras internas reales | Cada módulo expone una interfaz; nada de consultas cruzadas a tablas de otro módulo | Permite extraer `search` o `payments` **cuando haya evidencia de que hace falta**, sin reescribir el dominio |
| Cuándo dividir | Umbral declarado: más de 8 instancias en pico sostenido, o equipo mayor a ~15 personas | Un criterio explícito evita la discusión ideológica monolito-vs-microservicios |

**Lo que se paga:** escalado menos granular y despliegue acoplado. A la escala de SUP-1/SUP-4, ambos costos son menores que operar una saga distribuida sobre la ruta crítica.

### 9.2 DEC-4 completa — Control de sobreventa: por qué bloqueo pesimista

> **Responde a:** *el diseño de concurrencia.*

Las tres alternativas, evaluadas contra el escenario real de SUP-5: **40 solicitudes concurrentes sobre la última silla**.

| Mecanismo | Cómo funciona | Bajo alta contención | Veredicto |
|---|---|---|---|
| **Optimista con reintentos** | Lee `version`, calcula, escribe con `WHERE version = leída`; si falla, reintenta | Con 40 solicitudes por 1 silla, **39 fallan y reintentan**; en el reintento vuelven a chocar. Tormenta de reintentos justo en el momento de máximo valor comercial. Latencia p99 impredecible | ✗ Es óptimo cuando los conflictos son **raros**. Aquí el conflicto **es** el caso de uso |
| **Cola serializadora por vuelo** | Una cola por `flight_instance`; un consumidor procesa en orden | Serialización perfecta y sin bloqueos, pero la reserva pasa a ser **asíncrona**: el usuario recibe "procesando" y hay que sondear o notificar. Añade broker, consumidores, orden y reintentos | ✗ Sobre-ingeniería a esta escala. Es la respuesta correcta a 10.000 solicitudes/s por vuelo (entradas de concierto), no a 40 |
| **Pesimista `SELECT … FOR UPDATE`** ✅ | La transacción bloquea la fila de `flight_inventory` de esa cabina; las demás **esperan en el motor** y se atienden en orden | Las 40 se serializan sobre **una fila**, con transacción de pocos milisegundos. La primera gana; las 39 siguientes leen el estado ya actualizado y reciben un `409` limpio. **Determinista, síncrono, sin reintentos** | ✅ **Elegido** |

**El argumento en una frase:** la contención está concentrada en **una sola fila corta** (DEC-3), la transacción es **breve y sin E/S externa** (el pago vive fuera, DEC-5), y el RNF prefiere **rechazar rápido a confirmar y revertir** (RNF-C1). Bajo esas tres condiciones, el bloqueo pesimista da la garantía más fuerte al menor costo de complejidad.

**Protocolo de la transacción crítica:**

```
BEGIN;
  SET LOCAL lock_timeout = '3s';

  -- 1. Bloquear TODOS los tramos en orden canónico
  SELECT ... FROM flight_inventory
   WHERE (flight_instance_id, cabin_code) IN (...)
   ORDER BY flight_instance_id, cabin_code
     FOR UPDATE;

  -- 2. Verificar disponibilidad de TODOS los tramos (atomicidad, RNF-C2)
  -- 3. UPDATE seats_held = seats_held + n, version = version + 1
  --    -> la CHECK constraint es la última línea de defensa
  -- 4. INSERT reservation (HELD, hold_expires_at = now() + 20 min)
  -- 5. INSERT itinerary, itinerary_segment, reservation_passenger
  -- 6. INSERT reservation_event (auditoría, RNF-A1)
COMMIT;
```

**Tres detalles que hacen la diferencia:**

1. **Orden canónico de bloqueo.** Sin `ORDER BY flight_instance_id`, dos itinerarios que comparten tramos en sentido inverso (BOG→MDE→CTG y CTG→MDE→BOG) producen un abrazo mortal: cada uno tiene la fila que el otro necesita. Con orden fijo es **imposible por construcción**, no improbable. PostgreSQL coloca el nodo `LockRows` por encima del `Sort`, así que las filas se bloquean en orden ordenado y no en orden de llegada.
2. **Ninguna llamada externa dentro de la transacción.** Si el PSP demorara 3 s con la fila bloqueada, se bloquearía el vuelo entero durante 3 s. Por eso el pago está fuera (DEC-5) y las tarifas se cotizan **antes** de tomar el bloqueo (D-2).
3. **`lock_timeout` explícito de 3 s.** Antes que dejar que una espera se acumule sin límite, se falla rápido con `503 INVENTORY_BUSY`. Un fallo limpio es mejor que una degradación silenciosa.

### 9.3 DEC-5 — Flujo de pago: delegado, con máquina de estados propia

**Se delega el *manejo del instrumento de pago*, pero el sistema conserva la *máquina de estados de la venta*.** No es lo mismo, y confundirlo es un error común.

```
  Retención (síncrona, transaccional)          Pago (asíncrono, externo)
+-----------------------------------+   +------------------------------------+
| POST /reservations                |   | El usuario paga en el checkout     |
|  -> BEGIN ... FOR UPDATE ... COMMIT|-->|  alojado por el PSP                |
|  -> reservation = HELD (20 min)   |   |  (el sistema NUNCA ve el PAN)      |
|  -> devuelve PNR + payment_intent |   |                                    |
+-----------------------------------+   +------------------+-----------------+
                                                           | webhook firmado
                                        +------------------v-----------------+
                                        | POST /webhooks/psp                 |
                                        |  -> verifica firma HMAC            |
                                        |  -> INSERT payment_event           |
                                        |     (UNIQUE psp_event_id => idemp.)|
                                        |  -> HELD -> CONFIRMED              |
                                        |  -> seats_held-n, seats_sold+n     |
                                        |  -> emite tickets + audita         |
                                        +------------------------------------+
```

| Qué delega | Qué conserva |
|---|---|
| Captura y almacenamiento del número de tarjeta, 3-D Secure, antifraude del instrumento, tokenización, cumplimiento PCI del dato | Estado de la reserva, retención de inventario, reglas de reembolso, **idempotencia**, conciliación diaria y decisión final de confirmar |

**Por qué así:** delegar el instrumento reduce el alcance PCI a SAQ-A (RNF-S1) — la decisión de seguridad de mayor impacto del proyecto. Pero **no se puede delegar la máquina de estados**: el PSP no sabe nada de inventario ni de retenciones.

**Casos límite resueltos explícitamente:**

| Caso | Comportamiento |
|---|---|
| Webhook duplicado | `UNIQUE (psp_event_id)` lo descarta → RNF-C4 |
| Webhook tras expirar la retención | Se reintenta retener. Si hay cupo, confirma; si no, **reembolso automático** y notificación |
| Pago exitoso, webhook nunca llega | *Job* de conciliación cada 15 min contra la API del PSP sobre reservas `HELD` vencidas con intento de pago |
| Usuario paga dos veces | Segundo intento rechazado por `idempotency_key` de la reserva |
| Agencia (SUP-13) | No hay PSP: se debita `credit_used` **dentro de la misma transacción** que la retención. Es la única diferencia real de flujo entre canales |

### 9.4 DEC-7 — API B2B separada, dominio compartido

**Dos superficies de API** (`/api/v1/**` pública y `/b2b/v1/**` para agencias) **sobre los mismos servicios de dominio**.

| | API pública | API B2B |
|---|---|---|
| Autenticación | Sesión de usuario / invitado | OAuth2 *client credentials* por agencia + usuario nominal |
| Tarifas | Públicas | **Netas** con descuento contractual (RF-21) |
| Pago | PSP con tarjeta | **Cupo de crédito** (RF-22) |
| Límite de tasa | Por IP | **Cuota por agencia** (RNF-E2) |
| Versionado | Cambia con el sitio web | **Contrato estable** |
| Datos visibles | Solo la reserva del propio usuario | Solo las reservas de **esa** agencia (RNF-S5) |

**Por qué separadas:** son contratos con ciclos de vida distintos. La web pública se puede cambiar el martes; un integrador B2B necesita meses de aviso. Mezclarlas obligaría a congelar la evolución de la web al ritmo del socio más lento.

**Por qué el dominio es compartido:** si el canal B2B tuviera su propia lógica de inventario, **la garantía de RNF-C1 habría que probarla dos veces y se rompería la primera vez que los canales divergieran**.

### 9.5 DEC-8, DEC-9, DEC-10, DEC-11 — Rendimiento, disponibilidad, auditoría y analítica

| Dec | Decisión | Detalle | RNF |
|---|---|---|---|
| **DEC-8** | Caché y réplicas | Catálogo en Redis TTL 24 h · resultados de búsqueda TTL **60 s** (acierto esperado >70% en rutas de alta demanda) · réplicas de lectura con autoescalado · **la reserva nunca lee de caché**. El desfase solo puede producir un falso positivo de disponibilidad, corregido al retener; cachear la **decisión** de retención violaría RNF-C1 y está prohibido | RNF-P1, P3, E1 |
| **DEC-9** | Multi-AZ y degradación | RDS Multi-AZ con conmutación automática · ≥2 instancias de aplicación en AZ distintas · PITR · despliegue azul-verde · **degradación**: si el PSP cae, búsqueda y retención siguen operando y solo se bloquea la confirmación, extendiendo la retención · cortacircuitos con retroceso exponencial | RNF-D1, D3 |
| **DEC-10** | Auditoría transaccional | Toda transición escribe en `reservation_event` **dentro de la misma transacción**. Si fuera asíncrona, un fallo entre la operación y su registro dejaría una transición sin rastro, y RNF-A1 exige el **100%**. Permisos revocan `UPDATE`/`DELETE` incluso al rol de aplicación | RNF-A1, A2 |
| **DEC-11** | ETL desde réplica | **Regla dura: el pipeline nunca consulta el primario.** Ventana nocturna, extracción incremental por marca de agua, grano del hecho = tiquete, `reservation_event` como fuente | RNF-P4, RF-30 |

---

## 10. Arquitectura del frontend (5.5)

> El frontend está **diseñado, no implementado**: la sección 5.6 del enunciado pide una API FastAPI y una base de datos, no una interfaz. Se declara explícitamente en §11.1.

### 10.1 DEC-12 — Híbrido: SSR para descubrimiento, SPA para el flujo de reserva

**Next.js (React) con renderizado en servidor para las páginas de entrada y aplicación cliente para el flujo de compra.**

| Superficie | Modo | Por qué |
|---|---|---|
| Inicio, páginas de ruta ("vuelos Bogotá-Medellín"), contenido | **SSR / estático** | SEO: estas páginas capturan tráfico orgánico, que es adquisición gratuita. Una SPA pura las hace invisibles |
| Resultados de búsqueda | **SSR inicial + hidratación** | El primer resultado debe verse rápido (RE-2). Filtros y orden ya son cliente (RF-03) |
| Selección → pasajeros → sillas → pago | **SPA** | Flujo con mucho estado y un reloj de retención corriendo; recargar en cada paso lo rompería |
| Gestión de reserva y check-in | **SPA** | Aplicación autenticada, sin valor SEO |

**El RNF que pesa es RNF-P1/P2 más RE-2:** el tiempo que importa comercialmente no es el de la API, es **el tiempo hasta que el usuario ve el primer resultado**. Con SPA pura hay tres saltos en serie (HTML → JS → API). **Elegir un solo modo optimizaría la mitad del recorrido.**

### 10.2 DEC-13 — Notificación en tiempo real de pérdida de disponibilidad

**Tres capas, de menor a mayor costo.** No hace falta websockets para todo.

| Capa | Mecanismo | Qué cubre |
|---|---|---|
| **1. Reloj de retención** | Cuenta regresiva de 20 min alimentada por `hold_expires_at` (autoridad del servidor, no del reloj del navegador) | El caso mayoritario: el usuario **ya tiene** el inventario retenido, así que **no puede perderlo**. La mejor forma de no notificar una mala noticia es hacer que no ocurra |
| **2. Revalidación antes de retener** | Al pulsar "continuar" se revalida disponibilidad contra el servidor | Cubre el desfase de la caché de 60 s. **Falla temprano**, cuando el usuario aún no ha invertido esfuerzo |
| **3. SSE en el mapa de sillas** | *Server-Sent Events* sobre `flight_instance`, solo mientras el mapa está abierto | La única pantalla donde hay competencia visible por un recurso concreto. SSE y no WebSocket: el flujo es unidireccional y SSE reconecta solo |

**Manejo del error cuando ocurre igual:** ante `409 SEAT_UNAVAILABLE` la interfaz **no muestra un error genérico**: recarga la disponibilidad de ese tramo y propone alternativas cercanas conservando el resto del itinerario ya elegido. **Un rechazo bien manejado recupera la venta; un `Error 409` en rojo la pierde.** Por eso los errores de dominio llevan código estable (ver D-5).

### 10.3 DEC-14 y DEC-15 — Consola B2B e internacionalización

**DEC-14 — Consola B2B separada** (`agents.aerolinea.com`), consumiendo la API B2B de DEC-7. **Por qué no reutilizar la web pública con un rol distinto:** el agente no es un pasajero con permisos extra, es **otro oficio**. Vende 40 tiquetes al día para clientes que no son él: necesita búsqueda multi-pasajero, cupo de crédito visible, liquidación de comisiones, reservas en nombre de terceros y atajos de teclado. Meter eso en la web pública tras condicionales de rol produce una interfaz peor para ambos y una superficie de riesgo mayor (un error de rol expondría datos de agencia — RNF-S5).

**DEC-15 — i18n y multi-moneda.** Implicaciones estructurales más allá de traducir textos:

| Dimensión | Decisión | Implicación |
|---|---|---|
| Idioma | Rutas con prefijo (`/es/…`, `/en/…`), no detección por navegador | Cada idioma es una URL indexable, coherente con el SSR. La detección automática rompe el SEO y confunde a quien comparte un enlace |
| Moneda | **Se fija al inicio de la reserva y no cambia**; el precio se guarda en la moneda de venta (SUP-12) | Impide el "precio que se mueve": el frontend **nunca** convierte |
| Formato | `Intl.NumberFormat` / `Intl.DateTimeFormat` | El separador decimal cambia (COP `1.250.000` vs USD `1,250.00`); formatear a mano produce errores de tres órdenes de magnitud |
| Zonas horarias | Horarios **siempre en hora local del aeropuerto**, con etiqueta explícita | Convención del sector, no capricho: mostrar la salida en la hora local del usuario **hace perder vuelos**. Requiere `airport.timezone` en el modelo |
| Contenido de negocio | Nombres de aeropuertos y ciudades **se traducen en la base**, no en el frontend | Un catálogo de datos no puede vivir en archivos de traducción: cambiaría con cada despliegue en vez de con cada edición del catálogo |

**Sobre el cuello de botella de RNF-E1 (plan por etapas):** ante 20 rutas nuevas, la mitigación se aplica **en este orden**: (1) caché de resultados por (origen, destino, fecha) — el 90% del tráfico se concentra en pocas rutas; (2) réplicas de lectura para separar búsqueda de reserva; (3) **solo si se superan ~150 rutas**, precomputar itinerarios en un almacén de solo lectura. **No se hace (3) desde el día uno**: sería optimizar para un problema que no se tiene, lo que contradice el pilar de optimización de costos.

---

## 11. Implementación funcional del backend (5.6)

> **Responde a:** *necesitamos la implementación funcional que siga el diseño.*

### 11.1 Qué se implementó y qué no

El enunciado exige una implementación **funcional aunque parcial**, cuyo propósito **no es construir el sistema completo** sino *validar que las decisiones de diseño son implementables y consistentes entre sí*.

**Stack:** PostgreSQL 16 · FastAPI · SQLAlchemy 2.0 · psycopg 3 · 4 procesos uvicorn · Docker Compose.

| Operación | Endpoint | Requisito | Estado |
|---|---|---|---|
| Buscar vuelos disponibles | `GET /api/v1/flights/search` | RF-01, RF-02 | ✅ Directos y con 1 escala, con tiempo mínimo de conexión por aeropuerto |
| Crear reserva | `POST /api/v1/reservations` | RF-08, RF-09, RF-10 | ✅ Multi-tramo, multi-pasajero, **con bloqueo pesimista** |
| Consultar reserva | `GET /api/v1/reservations/{pnr}` | RF-15 | ✅ Con control PNR + apellido |
| Confirmar pago | `POST /api/v1/reservations/{pnr}/confirm` | RF-12 | ✅ Idempotente (webhook simulado) |
| Cancelar | `POST /api/v1/reservations/{pnr}/cancel` | RF-16 | ✅ Libera inventario de inmediato |
| Expirar retenciones | `POST /internal/jobs/expire-holds` | RF-11 | ✅ Con `SKIP LOCKED` |
| Verificar invariante | `GET /internal/oversell-check` | RNF-C1 | ✅ Aserción en vivo |

**Los tres primeros son exactamente el alcance mínimo que exige el enunciado.** Los otros cuatro se agregaron porque una API que solo retiene pero nunca confirma ni cancela **no permite verificar que los contadores de inventario cierran** — sin ellos, la evidencia de §12 sería incompleta.

**Fuera de alcance en esta iteración, declarado explícitamente:**

| Fuera de alcance | Motivo |
|---|---|
| **Frontend** | La sección 5.6 pide una API FastAPI y una base de datos. El frontend se diseña en 5.5 (§10) pero no se implementa |
| API B2B como superficie separada (DEC-7) | El descuento de agencia, la comisión y el cupo de crédito **sí** están implementados en el flujo de dominio; lo que falta es exponerlos en un contrato aparte |
| Check-in y asignación de sillas (RF-27) | Modelado en el esquema, sin endpoint |
| Productos auxiliares (RF-19) | Tabla creada y consumida por el ETL, sin endpoint de compra |
| Notificaciones (RF-29) | Sin implementar |
| Materialización programada (RF-25) | Se ejecuta en el *seed*; en producción sería un *job* diario |

**Base de datos:** esquema `airline`, **26 tablas** en 3FN con las tres denormalizaciones declaradas en §8.2, más 4 vistas. Datos de prueba: 6 aeropuertos, 12 rutas, 14 vuelos programados, **854 instancias de vuelo**, **1.342 filas de inventario**, 48 tarifas, 404 sillas mapeadas.

### 11.2 Cómo el código refleja el diseño

| Decisión de diseño | Dónde se ve en el código |
|---|---|
| **DEC-1** programado vs. instancia | `sql/01_schema.sql`: tablas `scheduled_flight` y `flight_instance`; materialización en `sql/02_seed.sql` |
| **DEC-2** itinerario como entidad | `reservation` → `itinerary` → `itinerary_segment`; `create_reservation` los crea anidados |
| **DEC-3** inventario por cabina | `flight_inventory` con PK `(flight_instance_id, cabin_code)`; `seat_assignment` aparte, sin tocar capacidad |
| **DEC-4** bloqueo pesimista | `booking.py::_LOCK_INVENTORY_SQL` + `apply_lock_timeout` + `ck_inventory_no_oversell` |
| **DEC-5** precio congelado, pago delegado | `itinerary_segment.quoted_*` (D-1), `confirm_reservation`, `UNIQUE (payment_event.psp_event_id)` |
| **DEC-8** caché y réplicas | `search.py` documenta que va a réplicas; el camino de reserva **nunca** lee de caché |
| **DEC-10** auditoría transaccional | `booking.py::_audit` en la misma transacción; trigger `trg_reservation_event_immutable` |
| **DEC-13** error accionable | `errors.py` con códigos estables y objeto `details` |
| **RNF-S2** control de acceso | `get_reservation`: apellido incorrecto devuelve **404**, no 403, para no confirmar que el PNR existe |

**La sección crítica**, tal como está en `booking.py::create_reservation`:

```sql
SELECT flight_instance_id, cabin_code, capacity, seats_sold, seats_held,
       oversell_factor, version,
       FLOOR(capacity * oversell_factor) - seats_sold - seats_held AS seats_available
FROM flight_inventory
WHERE (flight_instance_id, cabin_code)
      IN (SELECT * FROM unnest(CAST(:ids AS bigint[]), CAST(:cabins AS varchar[])))
ORDER BY flight_instance_id, cabin_code
FOR UPDATE
```

Tres propiedades que el código sostiene y que hay que poder defender línea por línea:

1. **Orden canónico** — el `ORDER BY` antes del `FOR UPDATE` no es cosmético: fija el orden de adquisición y hace imposible el abrazo mortal.
2. **Verificar todo antes de escribir nada** — RNF-C2 exige atomicidad; el código recorre *todos* los tramos comprobando disponibilidad y solo después ejecuta los `UPDATE`.
3. **Pool dimensionado por encima de la concurrencia de prueba** (20 + 40 de desborde) — si el pool fuera más pequeño, la prueba mediría la cola del pool en vez del mecanismo de bloqueo, y **no probaría nada sobre RNF-C1**.

---

## 12. Diseño de concurrencia y pruebas

> **Responde a:** *el diseño de concurrencia y test · tiene que estar funcionando.*

### 12.1 Cómo se probó

Dos formas independientes, porque una sola no bastaría: **una prueba que solo mira códigos HTTP podría pasar con una base sobrevendida.**

**a) Suite automatizada** — `backend/tests/test_concurrency.py`, 11 pruebas:

| Prueba | Qué verifica | Requisito |
|---|---|---|
| `test_search_returns_direct_flights` | Búsqueda directa devuelve ofertas con precio | RF-01 |
| `test_search_returns_connections` | Itinerarios con escala respetando el tiempo mínimo de conexión | RF-02, RF-09 |
| `test_reservation_lifecycle` | Ciclo completo: retener → consultar → confirmar → cancelar | RF-08, RF-12, RF-15, RF-16 |
| `test_confirm_is_idempotent` | 10 reentregas del mismo webhook → 1 confirmación, 1 pago, 1 tiquete | RNF-C4 |
| `test_invalid_connection_is_rejected` | Itinerario sin continuidad geográfica → `422` | RF-09 |
| `test_no_oversell_under_concurrency` **(4 casos)** | N solicitudes por K sillas → exactamente K ganan, 0 sobreventa | **RNF-C1** |
| `test_multi_segment_hold_is_atomic` | Si un tramo no tiene cupo, ningún tramo queda retenido | RNF-C2 |
| `test_expired_hold_releases_inventory` | La retención vencida devuelve el inventario | RF-11, RNF-C3 |

**b) Script de estrés** — `backend/scripts/concurrency_stress.py`, que prepara el escenario, lanza N solicitudes y **genera un reporte con evidencia**. Sale con código distinto de cero si hay sobreventa, así que funciona tal cual en CI.

### 12.2 Por qué la prueba es honesta y no un montaje

Este es el punto que hay que poder defender: una prueba de concurrencia mal construida da un falso positivo con toda facilidad.

| Decisión de la prueba | Qué evita |
|---|---|
| **Hilos reales del sistema operativo**, no corrutinas | El paralelismo cooperativo serializaría las solicitudes por sí solo: **la prueba pasaría incluso sin ningún mecanismo de bloqueo** |
| **`threading.Barrier`** antes de cada `POST` | Sin ella, la primera solicitud terminaría antes de que la segunda empezara: no habría contención que medir |
| **HTTP contra la API real**, con **4 procesos uvicorn** | Un solo worker podría serializar en la capa de aplicación y ocultar un fallo de la capa de datos. Con 4 procesos, la contención está **obligada** a resolverse en PostgreSQL |
| **Aserción contra `v_oversell_check`**, no contra los códigos HTTP | Es la diferencia entre "la API respondió bien" y "la base quedó consistente" |
| **Documento único por worker** | Documentos repetidos introducirían contención en el `ON CONFLICT` de `passenger`, ajena a la que se quiere medir |
| **Conciliación** contra `v_inventory_reconciliation` | Verifica que el contador materializado coincide con los tramos realmente vendidos |
| **Reinicio de estado** entre pruebas (`reset_bookings`) | Sin él las pruebas no son independientes y fallan por motivos ajenos a lo que verifican (ver D-7) |

### 12.3 Resultados — ejecución real del 8 de septiembre de 2026

Stack levantado con `docker compose up -d`. Reportes completos, con el detalle solicitud por solicitud, en `docs/evidencia/`.

| Escenario | Solicitudes | Sillas | Creadas | Rechazos `409` | Errores | **Sobreventa** | Inventario final |
|---|---|---|---|---|---|---|---|
| **Literal del enunciado** | 2 | 1 | **1** ✅ | **1** ✅ | 0 ✅ | **0** ✅ | 11 + 1 = 12/12 |
| **SUP-5, pico de contención** | 40 | 1 | **1** ✅ | **39** ✅ | 0 ✅ | **0** ✅ | 11 + 1 = 12/12 |
| **K > 1** | 60 | 5 | **5** ✅ | **55** ✅ | 0 ✅ | **0** ✅ | 145 + 5 = 150/150 |

En los tres casos la cabina queda **exactamente llena** (`seats_sold + seats_held = capacity`), la vista `v_oversell_check` devuelve **cero filas**, y la conciliación entre contador y tramos reales cuadra.

**Suite completa: 11/11 pruebas pasan.**

```
tests/test_concurrency.py ...........                          [100%]
11 passed in 52.61s
```

### 12.4 Latencias y hasta dónde aguanta

| Escenario | p50 | p95 | máx | `lock_timeout` |
|---|---|---|---|---|
| 40 solicitudes / 1 silla | 119 ms | 139 ms | 140 ms | 3.000 ms |
| 60 solicitudes / 5 sillas | 255 ms | 294 ms | 303 ms | 3.000 ms |

El bloqueo serializa las solicitudes sobre una sola fila, así que la última en ser atendida espera a todas las anteriores: **la latencia crece linealmente con la cola**. Con 60 solicitudes la cola completa se drena en ~300 ms, **un orden de magnitud por debajo del `lock_timeout`**. Extrapolando esa pendiente (~5 ms por solicitud en cola), harían falta del orden de **600 solicitudes simultáneas sobre el mismo vuelo y cabina** para empezar a agotar el tiempo de espera: **quince veces el pico asumido en SUP-5**.

**Esto es una propiedad del diseño, no un defecto.** Es exactamente el compromiso aceptado en DEC-4 al preferir rechazar rápido antes que confirmar y revertir. Si la aerolínea creciera hasta niveles de contención de venta de entradas de concierto, la respuesta sería migrar a la cola serializadora ya evaluada — y **el modelo de datos no cambiaría**, porque el punto de contención seguiría siendo la misma fila.

---

## 13. Implementación en AWS: ETL, catálogo y costos (5.7)

> **Responde a:** *la arquitectura AWS, que tenga en cuenta esos pilares.*

### 13.1 Arquitectura implementada

```
        +------------------- VPC ---------------------------------------+
        |                                                               |
        |  +-------------------+         +-------------------+          |
  API   |  |  RDS PostgreSQL   |         |  RDS PostgreSQL   |          |
 (5.6)--+->|  airline-oltp     |         |  airline-olap     |<-- BI    |
        |  |  db.t3.small      |         |  db.t3.micro      |   (A7)   |
        |  |  esquema 3FN      |         |  esquema estrella |          |
        |  +---------+---------+         +---------^---------+          |
        |            |                             |                    |
        |            |   +---------------------+   |                    |
        |            +-->|  AWS Glue           |---+                    |
        |   lectura      |  Python Shell job   |   escritura            |
        |   incremental  |  0.0625 DPU         |   idempotente          |
        |   (watermark)  |  diario 03:00 COT   |                        |
        |                +---------------------+                        |
        |                                                               |
        |  +---------------+            +---------------+               |
        |  | oltp-crawler  |            | olap-crawler  |               |
        |  +-------+-------+            +-------+-------+               |
        |          |        conexiones JDBC     |                       |
        |          v                            v                       |
        |      +----------------------------------------+               |
        |      |       AWS Glue Data Catalog            |               |
        |      |  airline_oltp_catalog (30 objetos)     |               |
        |      |  airline_olap_catalog (13 tablas)      |               |
        |      +----------------------------------------+               |
        |                                                               |
        |  Security Group airline-db-sg (auto-ref., todos los puertos)   |
        +---------------------------------------------------------------+
                              Rol IAM: LabRole
```

Provisión reproducible en `etl/infra/setup_aws.sh`; código del ETL en `etl/glue_job_oltp_to_olap.py`.

### 13.2 Decisiones justificadas

#### Qué se copia, y transformado

**Se transforma, no se copia tal cual.** Copiar `ticket` reproduciría en la analítica el problema del OLTP: responder "ingresos por tarifa" exigiría seis `JOIN` en cada consulta de BI. Se cargan **tres hechos**, uno por pregunta de la gerencia:

| Hecho | Grano | Pregunta que responde | Origen en el OLTP |
|---|---|---|---|
| `fact_ticket_sale` | Un tiquete = pasajero × tramo | **Ingresos por tarifa** | `ticket` ⋈ `itinerary_segment` ⋈ `reservation` ⋈ `ancillary` |
| `fact_flight_occupancy` | Una cabina de un vuelo | **Ocupación por ruta** | `flight_inventory` ⋈ `flight_instance` + ingreso agregado |
| `fact_reservation_lifecycle` | Una reserva | **Patrones de cancelación** | **`reservation_event`** ⋈ `reservation` ⋈ `itinerary` |

Cuatro decisiones de transformación defendibles una por una:

1. **El grano del hecho principal es el tiquete, no la reserva.** Es el grano más fino con valor monetario: desde ahí se agrega a ruta, tarifa, vuelo o fecha; al revés no. Con grano de reserva sería imposible responder "ingresos por tarifa" en un itinerario con clases distintas por tramo.
2. **`days_before_departure` se precalcula.** Es el eje de "ingresos por tarifa **según anticipación**", que es la política tarifaria del enunciado.
3. **`load_factor` se materializa.** Es una métrica **semiaditiva**: se promedia por ruta, no se suma. Dejarla al analista garantiza que alguien la sume y reporte una ocupación del 4.300%.
4. **`fact_flight_occupancy` se agrega en el origen**, no fila por fila. Reduce el volumen que viaja por la red, que es donde está el costo del ETL en Glue.

> **El punto no obvio, y el más defendible de esta sección:** `reservation_event` —el log de auditoría creado para cumplir RNF-A1— resulta ser **la única fuente posible** de los patrones de cancelación. Una reserva cancelada que solo guarda su estado final no dice ni cuándo se canceló, ni cuánto vivió, ni a cuántos días de la salida. **Un requisito de cumplimiento terminó habilitando una capacidad analítica.** Esa conexión entre RNF-A1 y esta sección es la respuesta a "¿cómo se relaciona la arquitectura transaccional con el pipeline de Big Data?".

#### Por qué el esquema analítico es distinto

| | OLTP (`airline`) | OLAP (`analytics`) |
|---|---|---|
| Modelo | 3FN, 26 tablas | Estrella, 5 dimensiones + 3 hechos |
| Optimizado para | Escribir con integridad, transacciones cortas | Leer agregando, escaneos amplios |
| Redundancia | Mínima (3 excepciones declaradas) | Deliberada: la ruta se repite en cada fila de hecho |
| Claves | Naturales y sustitutas con FK estrictas | Sustitutas, sin integridad referencial estricta |
| Historia | Estado actual + log de eventos | *Snapshot* por corrida, con `loaded_at` |

La 3FN del OLTP existe para que `flight_inventory` sea el único lugar donde vive el conteo de sillas (DEC-3), condición de RNF-C1. La estrella existe para que "ocupación por ruta en diciembre" sea un `GROUP BY` sobre una tabla. **El ETL es la traducción entre ambos mundos, y por eso es un componente de diseño, no una tubería trivial.**

#### Qué servicio implementa el ETL

| Opción | A favor | En contra | Veredicto |
|---|---|---|---|
| **Glue ETL (Spark)** | Escala a terabytes | Mínimo **2 DPU** y ~1 min de arranque de cluster: **≈16× más caro** y más lento para mover decenas de miles de filas | ✗ Sobredimensionado |
| **Glue Python Shell** ✅ | **0,0625 DPU** (el mínimo facturable de AWS). Arranca en segundos. Integrado con el catálogo, las conexiones JDBC y los *triggers* | No escala más allá de un proceso | ✅ **Elegido** |
| **Lambda + psycopg** | Aún más barato | **Límite duro de 15 min**: una recarga completa lo excedería y el job fallaría justo cuando más se necesita | ✗ Riesgo operativo real |
| **DMS** | Replicación continua, CDC | Replica **esquemas**, no los transforma: haría falta un segundo paso igual. Instancia **siempre encendida**: ~25 USD/mes | ✗ Resuelve otro problema |
| **Step Functions + Lambda** | Orquestación fina | Complejidad para un pipeline de un solo paso | ✗ Sin beneficio |

**En una frase:** el volumen del ETL cabe holgadamente en un proceso Python; pagar el mínimo de 2 DPU de Spark sería pagar 16 veces más por arrancar un cluster para mover lo que cabe en memoria. Si el volumen creciera ×100 —no ×10— la migración a Spark sería un cambio de `--command`, sin tocar el modelo.

#### Cómo se conectan ambas bases al catálogo

Cuatro piezas, y las cuatro tienen que estar bien o el crawler se queda colgado sin mensaje útil:

| Pieza | Configuración | Por qué |
|---|---|---|
| **Conexión JDBC** | `jdbc:postgresql://<host>:5432/<db>`, `JDBC_ENFORCE_SSL=true`. Una por base | Es el mismo objeto que usan el crawler (metadatos) y el job (datos) |
| **Rol IAM** | `LabRole` — el Learner Lab no permite crear roles | Restricción del entorno, declarada como A-2 |
| **Red** | Subredes de la VPC + **Security Group auto-referenciado en TODOS los puertos** (`IpProtocol=-1`, origen el propio grupo) | **La causa número uno de fallos.** Glue crea sus ENIs *dentro* de ese grupo y necesita que se hable consigo mismo. **Verificado en el despliegue real:** limitar la auto-referencia al 5432 **no basta** — el crawler falla con `At least one security group must open all ingress ports`. La regla sigue cerrada al exterior: el origen es el grupo, no un CIDR |
| **Crawler** | Uno por base, con `Path = <db>/<schema>/%` | Sin el comodín no descubre tablas. Registra `airline_oltp_catalog` (**30 objetos**: 26 tablas + 4 vistas) y `airline_olap_catalog` (**13**: 10 tablas + 3 vistas): el requisito explícito del enunciado. **Y el crawler debe correr DESPUÉS de cargar los esquemas**: sobre una base vacía termina en `SUCCEEDED` y no registra nada |

#### Frecuencia del ETL

**Diario, 03:00 hora de Colombia (08:00 UTC), incremental.**

| Opción | Frescura | Costo/mes | Cuándo se justifica |
|---|---|---|---|
| Una vez | Congelado | ~0 USD | Solo carga histórica inicial |
| **Diario (elegido)** | hasta 24 h | **~0,06 USD** | Preguntas tácticas de gerencia |
| Cada hora | hasta 1 h | ~1,32 USD | Si el *pricing* dinámico consumiera la analítica |
| Por eventos (CDC) | segundos | ~25 USD+ | Solo si una decisión operativa dependiera del dato en vivo |

Las tres preguntas del enunciado son **tácticas, no operativas**: nadie cambia una ruta en respuesta a lo que pasó hace diez minutos.

**Por qué a las 03:00:** es el valle de tráfico (SUP-4). Pero **la garantía de RNF-P4 es la réplica de lectura, no el horario**; el horario es el cinturón sobre los tirantes.

**Extracción incremental** por marca de agua (`etl_watermark`), con ventana de reproceso de 60 min para no perder filas escritas por transacciones abiertas cuando la corrida anterior tomó su instantánea. Sin esto, el escaneo completo diario crecería linealmente con la historia y el job se encarecería para siempre.

### 13.3 Justificación de servicios contra los seis pilares Well-Architected

**OE** Excelencia Operativa · **SEG** Seguridad · **FIA** Fiabilidad · **EFI** Eficiencia del Rendimiento · **COS** Optimización de Costos · **SOS** Sostenibilidad

| Servicio AWS | Alternativa considerada | Requisito que respalda la elección | Pilar(es) y por qué |
|---|---|---|---|
| **RDS PostgreSQL `airline-oltp`** (db.t3.small, gp3 20 GB, Multi-AZ en prod.) | Aurora PostgreSQL; PostgreSQL en EC2 | **RNF-C1**: se necesita `SELECT … FOR UPDATE` con ACID estricto sobre una fila (DEC-4). **RNF-D1** (99,9%): Multi-AZ da conmutación automática. **RNF-D3**: PITR cubre RPO ≤ 5 min | **FIA** — Multi-AZ y PITR son la implementación literal del SLO. **OE** — parches y respaldos gestionados liberan al equipo. **COS** — Aurora cuesta ~2,5× y su ventaja (escalado de lectura masivo) no se necesita a 120 búsquedas/s |
| **RDS PostgreSQL `airline-olap`** (db.t3.micro, Single-AZ) | Redshift Serverless; Athena sobre S3 | **RNF-P4**: aísla la carga analítica. **RF-30**: expone datos a A7 sin tocar el OLTP. Single-AZ **a propósito**: una analítica caída no detiene la venta y el dato se reconstruye reejecutando el ETL | **EFI** — separación de cargas por perfil de acceso. **COS** — Redshift factura por RPU con un mínimo que a este volumen es desperdicio; además el enunciado exige PostgreSQL. **SOS** — instancia dimensionada al volumen real, no al hipotético |
| **AWS Glue — job Python Shell** (0,0625 DPU) | Glue Spark; Lambda; DMS | **RF-30** y **RNF-P4**: mueve el dato sin tocar el primario | **COS** — 0,0625 DPU es el mínimo facturable de AWS: ~16× más barato que el mínimo de Spark. **SOS** — se consume el cómputo estrictamente necesario, nada de arrancar un cluster para mover megabytes. **EFI** — sin arranque en frío, el job termina en menos de un minuto |
| **AWS Glue Data Catalog** | Catálogo propio; documentación manual | Requisito explícito del enunciado. Sirve a **RF-30** dando descubribilidad del esquema a A7 | **OE** — un catálogo vivo evita la deriva entre el esquema real y su documentación. **EFI** — habilita Athena/QuickSight sin trabajo adicional. Sin costo: los primeros 1M de objetos y peticiones son gratuitos |
| **Glue Crawlers** (2, semanales) | Registro manual de tablas | Mantienen el catálogo sincronizado cuando el esquema evoluciona | **OE** — detección automática de cambios; con `UpdateBehavior=UPDATE_IN_DATABASE` y `DeleteBehavior=LOG` los cambios se registran sin destruir metadatos. **COS** — semanales y no diarios: el esquema cambia con cada despliegue, no cada noche |
| **Glue Connections (JDBC)** | Credenciales en el código del job | **RNF-S3**: credenciales fuera del código, tránsito cifrado | **SEG** — sin secretos en el repositorio; `JDBC_ENFORCE_SSL=true` |
| **Rol IAM `LabRole`** | Rol dedicado con privilegio mínimo | **RNF-S4**: identidad atribuible para acciones automatizadas | **SEG** — *(desviación A-2: en producción correspondería un rol propio por componente. El Learner Lab no permite crear roles, así que se usa `LabRole`, más permisivo de lo que el principio de mínimo privilegio recomienda. Se declara en vez de presentarlo como diseño intencional)* |
| **VPC + Security Group auto-referenciado** | Bases con acceso público | **RNF-S3**: ninguna base accesible desde internet | **SEG** — superficie de exposición nula. **FIA** — aísla el fallo de red |
| **EventBridge / Glue Trigger** (cron diario) | Ejecución manual; continua | Frescura de 24 h acordada arriba | **OE** — el pipeline no depende de que alguien lo lance. **COS/SOS** — cómputo solo cuando hay trabajo |
| **S3** (scripts del job) | Empaquetar en la imagen | Requisito de Glue | **OE** — versionado del artefacto desplegado. <0,01 USD/mes |
| **CloudWatch Logs** | Sin registro centralizado | **RNF-A3** y diagnóstico de corridas fallidas | **OE** — sin logs no hay operación posible. Retención 30 días para acotar costo |

### 13.4 Proyección de costos

#### Supuestos declarados

Un costo sin supuestos no es una proyección, es un número.

| # | Supuesto | Valor |
|---|---|---|
| C-1 | Región | `us-east-1` |
| C-2 | Precios | Bajo demanda, públicos. **Verificar en la calculadora de AWS antes de presentar**: los precios cambian |
| C-3 | Horas/mes | 730 |
| C-4 | Volumen base | SUP-1 a SUP-4: 35 rutas, ~3.600 instancias/mes, ~15.000 tiquetes/mes |
| C-5 | Almacenamiento | 20 GB gp3 por instancia (el OLTP crece ~1,5 GB/mes) |
| C-6 | Duración del ETL | ~4 min por corrida diaria |
| C-7 | Crawlers | 2 DPU × 10 min mínimo, ejecución **semanal** |
| C-8 | Multi-AZ | **Solo el OLTP** en producción. El Learner Lab no lo permite: se muestran ambos escenarios |
| C-9 | Transferencia | Todo dentro de la misma VPC y AZ ⇒ sin cargo de salida |
| C-10 | Sin NAT Gateway | Se evita deliberadamente: costaría **~33 USD/mes**, más que todo el resto del pipeline junto |

#### Escenario base (Learner Lab / desarrollo)

| Concepto | Cálculo | USD/mes |
|---|---|---|
| RDS OLTP `db.t3.small` Single-AZ | 0,036 USD/h × 730 h | **26,28** |
| RDS OLTP almacenamiento gp3 20 GB | 0,115 USD/GB-mes × 20 | 2,30 |
| RDS OLAP `db.t3.micro` Single-AZ | 0,018 USD/h × 730 h | **13,14** |
| RDS OLAP almacenamiento gp3 20 GB | 0,115 USD/GB-mes × 20 | 2,30 |
| Respaldos automáticos | Gratis hasta el 100% del almacenamiento aprovisionado | 0,00 |
| Glue job Python Shell | 0,0625 DPU × (4/60) h × 0,44 USD × 30 corridas | **0,06** |
| Glue crawlers (2, semanales) | 2 DPU × (10/60) h × 0,44 USD × 4,3 × 2 | **1,26** |
| Glue Data Catalog | 39 tablas, ~2.600 peticiones/mes → capa gratuita | 0,00 |
| S3 (scripts) | < 1 MB | 0,01 |
| CloudWatch Logs | ~0,5 GB, retención 30 días | 0,30 |
| **TOTAL** | | **≈ 45,65** |

#### Escenario de producción (Multi-AZ en el OLTP, RNF-D1)

| Concepto | USD/mes |
|---|---|
| RDS OLTP `db.t3.small` **Multi-AZ** (0,072 USD/h × 730) | 52,56 |
| RDS OLTP almacenamiento 20 GB (se replica) | 4,60 |
| RDS OLAP `db.t3.micro` + 20 GB | 15,44 |
| Glue (job + crawlers + catálogo) | 1,32 |
| S3 + CloudWatch | 0,31 |
| **TOTAL** | **≈ 74,23** |

> **El costo de RNF-D1 es explícito y cuantificado: +28,58 USD/mes (+63%)** por pasar de Single-AZ a Multi-AZ. Ese es el precio numérico de la decisión de disponibilidad. Poder decir esta cifra separa "elegimos 99,9%" de "elegimos 99,9% y sabemos lo que cuesta".

#### Proyección ×10

Volumen y frecuencia son **dos ejes distintos** y se comportan de forma muy diferente.

**Eje A — Volumen ×10** (350 rutas, ~150.000 tiquetes/mes):

| Concepto | Base | ×10 | Δ |
|---|---|---|---|
| RDS OLTP: `db.t3.small` → `db.t3.medium` | 26,28 | 52,56 | +26,28 |
| Almacenamiento OLTP: 20 → 200 GB | 2,30 | 23,00 | +20,70 |
| RDS OLAP: `db.t3.micro` → `db.t3.small` | 13,14 | 26,28 | +13,14 |
| Almacenamiento OLAP: 20 → 200 GB | 2,30 | 23,00 | +20,70 |
| Glue job (4 → ~18 min por corrida) | 0,06 | 0,26 | +0,20 |
| Glue crawlers | 1,26 | 1,40 | +0,14 |
| CloudWatch | 0,30 | 2,00 | +1,70 |
| **TOTAL** | **45,65** | **128,50** | **+82,85 (×2,8)** |

**Eje B — Frecuencia ×10** (de diaria a ~10 corridas/día):

| Concepto | Base | ×10 frecuencia | Δ |
|---|---|---|---|
| Glue job (300 corridas/mes) | 0,06 | 0,55 | +0,49 |
| Instancias RDS (ya están encendidas) | 41,72 | 41,72 | **0** |
| Resto | 3,87 | 4,20 | +0,33 |
| **TOTAL** | **45,65** | **46,47** | **+0,82 (×1,02)** |

**Ejes A + B combinados: ≈ 131 USD/mes.** El cruce no es multiplicativo porque cargan sobre partidas distintas.

#### Lectura — pilares de Costos y Sostenibilidad

**1. El cómputo del ETL no es el costo. Las bases encendidas sí.**
×10 volumen sube la factura ×2,8; ×10 frecuencia la sube **2%**. Glue es el **2,9%** del total; RDS el **95%**. La consecuencia es contraintuitiva y hay que sostenerla: **si el negocio pidiera datos más frescos, se puede pasar de diario a cada 2 horas por menos de un dólar al mes.** La restricción a diario **no es económica**, es de valor de negocio. Confundir ambas cosas llevaría a negar una mejora casi gratis.

**2. El crecimiento es sublineal en costo**, porque el gasto está dominado por capacidad reservada y no por consumo. Bueno para el margen, malo para la disciplina: se paga capacidad ociosa el 90% del tiempo. **Optimización de costos** en orden de retorno:
- Reservas de 1 año en RDS: **−30 a −40%** sobre la partida que domina la factura. La palanca de mayor impacto por lejos.
- `gp3` con IOPS base mientras la carga no exija más.
- Apagar el OLAP fuera de la ventana analítica (7:00–20:00 hábiles): **−60%** de esa instancia. Viable justamente porque es Single-AZ y reconstruible.

**3. Sostenibilidad — dónde está el desperdicio real.** Tres decisiones concretas, no una declaración de intenciones:
- **Extracción incremental por marca de agua.** Sin ella, cada corrida diaria reprocesaría la historia completa: al año, ~365× más cómputo y E/S **para producir el mismo resultado**. Es el mayor ahorro energético del pipeline y ya está implementado.
- **Agregación en el origen** (`fact_flight_occupancy`): mueve por la red el resultado, no las filas crudas.
- **Dimensionar al volumen real.** `db.t3.micro` para el OLAP no es tacañería: es no encender capacidad que nadie usará. Y `t3` es *burstable*: consume proporcionalmente a la carga real.

### 13.5 Verificación local del pipeline

El mismo `docker compose` incluye la base OLAP, lo que permite desarrollar y probar el ETL **sin consumir crédito del Learner Lab**. Tras generar 122 reservas (95 confirmadas, 20 canceladas) y ejecutar el job:

| Tabla del modelo estrella | Filas cargadas |
|---|---|
| `dim_date` / `dim_route` / `dim_fare` / `dim_flight` / `dim_channel` | 1.095 / 12 / 48 / 854 / 3 |
| `fact_ticket_sale` | 161 |
| `fact_flight_occupancy` | 1.342 |
| `fact_reservation_lifecycle` | 127 |
| **Tiempo total** | **0,8 s** |

Las tres vistas de negocio responden con datos reales. **Un resultado que valida la modelación de la política tarifaria:** en `v_revenue_by_fare`, la clase `P` ("Promo anticipada 21d") aparece con una **anticipación media de compra de 22,5 días**, mientras que la clase `B` ("Flexible", sin restricción) aparece con **5,0 días**. La regla `fare_rule.min_advance_days` no solo está en el modelo: **se comporta como debe en los datos**.

### 13.6 Modelo estrella del OLAP

![Modelo estrella de la base analítica](diagramas/estrella-olap.png)

*Figura 3 — Esquema en estrella: 5 dimensiones y 3 hechos, uno por cada pregunta de negocio del enunciado.*

### 13.7 Lista de verificación del requisito

El enunciado exige que **ambas bases queden registradas en el Glue Data Catalog**. Evidencia a adjuntar:

| # | Verificación | Comando |
|---|---|---|
| 1 | Instancias RDS disponibles | `aws rds describe-db-instances --query 'DBInstances[].[DBInstanceIdentifier,DBInstanceStatus]'` |
| 2 | Conexiones JDBC creadas | `aws glue get-connections --query 'ConnectionList[].Name'` |
| 3 | Crawlers en `SUCCEEDED` | `aws glue get-crawler --name airline-oltp-crawler --query 'Crawler.LastCrawl'` |
| 4 | **Tablas del OLTP en el catálogo** | `aws glue get-tables --database-name airline_oltp_catalog --query 'TableList[].Name'` |
| 5 | **Tablas del OLAP en el catálogo** | `aws glue get-tables --database-name airline_olap_catalog --query 'TableList[].Name'` |
| 6 | Corrida exitosa del ETL | `aws glue get-job-runs --job-name airline-etl-oltp-to-olap --query 'JobRuns[0].[JobRunState,ExecutionTime]'` |
| 7 | Datos en la analítica | `SELECT * FROM analytics.v_occupancy_by_route LIMIT 20;` |
| 8 | Captura de la consola de Glue con ambas bases | — |

---

## 14. Matriz de trazabilidad

> **Responde a:** *que las decisiones de diseño se puedan trazar desde los requerimientos funcionales y no funcionales.*
>
> Esta sección cierra la cadena completa. Se puede recorrer en cualquiera de los dos sentidos.

### 14.1 De arriba abajo: riesgo → requisito → decisión → código → prueba

| Riesgo | Requisitos | Decisiones | Código | Prueba que lo verifica |
|---|---|---|---|---|
| **RE-1** Sobreventa | RF-10, RF-11, RNF-C1, RNF-C2, RNF-C3 | **DEC-3**, **DEC-4** | `flight_inventory` + `ck_inventory_no_oversell` · `booking.py::_LOCK_INVENTORY_SQL` · `expire_holds` | `test_no_oversell_under_concurrency` (4 casos) · `test_multi_segment_hold_is_atomic` · `test_expired_hold_releases_inventory` · script de estrés (3 escenarios) |
| **RE-2** Abandono por lentitud | RF-01, RF-02, RNF-P1, RNF-P2 | DEC-8, DEC-12, DEC-13 | `search.py` con índices y vista de disponibilidad · caché TTL 60 s | `test_search_returns_direct_flights` · `test_search_returns_connections` |
| **RE-3** Caída en temporada alta | RNF-D1, RNF-D3, RNF-P3, RNF-E3 | DEC-9, DEC-6 | RDS Multi-AZ · aplicación sin estado · azul-verde | Prueba de carga ×5 antes de temporada (planificada) · simulacro de restauración semestral |
| **RE-4** Fuga de datos | RF-13, RNF-S1 a RNF-S5 | DEC-5, DEC-7 | `payment` sin PAN · PNR aleatorio · 404 ante apellido incorrecto | Prueba de acceso con apellido incorrecto en `test_reservation_lifecycle` · escaneo de PAN en CI |
| **RE-5** Disputa no reconstruible | RF-18, RNF-A1 a RNF-A3, RNF-C4 | DEC-10 | `reservation_event` + trigger de inmutabilidad · `_audit` transaccional | `test_confirm_is_idempotent` · el trigger se verificó de forma accidental (D-7) |
| **RE-6** Crecimiento que degrada | RNF-E1, RNF-P4 | DEC-8, DEC-11 | Caché por ruta · réplicas · ETL incremental desde réplica | Prueba de carga con catálogo de 55 rutas (planificada) |

### 14.2 De abajo arriba: cada decisión y el requisito que la origina

| Decisión | Qué resuelve | Requisitos que la originan | Alternativa descartada | Riesgo mitigado |
|---|---|---|---|---|
| **DEC-1** Programado ≠ instancia | Vuelos recurrentes, cambio de aeronave | RF-24, RF-25, RF-28 | Tabla `flight` única | RE-6 |
| **DEC-2** Itinerario como entidad | Escalas y multi-tramo | RF-08, RF-09, RF-16, RNF-C2 | Tabla puente plana | RE-1, RE-5 |
| **DEC-3** Inventario por cabina | Dónde vive el control de sobreventa | RF-10, RNF-C1 | Control por silla individual | **RE-1** |
| **DEC-4** Bloqueo pesimista | Concurrencia sobre la última silla | RNF-C1, RNF-C2 | Optimista con reintentos · cola serializadora | **RE-1** |
| **DEC-5** Precio congelado + pago delegado | Contrato inmutable, alcance PCI | RF-04, RF-12, RF-14, RNF-S1 | Recomponer precio con `JOIN` · procesar tarjeta en casa | RE-4, RE-5 |
| **DEC-6** Monolito modular | Transacción única para el inventario | RNF-C1, RNF-P1, RNF-E3 | Microservicios con saga | RE-1, RE-2 |
| **DEC-7** API B2B separada | Canal de agencias | RF-20 a RF-23, RNF-E2, RNF-S5 | Misma API con roles | RE-2, RE-4 |
| **DEC-8** Caché y réplicas | Rendimiento de búsqueda | RNF-P1, RNF-P3, RNF-E1 | Sin caché · precomputar itinerarios desde el día uno | RE-2, RE-6 |
| **DEC-9** Multi-AZ y degradación | 99,9% | RNF-D1, RNF-D3 | 99,99% multi-región | RE-3 |
| **DEC-10** Auditoría transaccional | Historial reconstruible | RF-18, RNF-A1, RNF-A2 | Auditoría asíncrona | RE-5 |
| **DEC-11** ETL desde réplica | Aislar analítica de transaccional | RF-30, RNF-P4 | ETL contra el primario | RE-2 |
| **DEC-12** Híbrido SSR/SPA | Primer resultado rápido | RNF-P1, RNF-P2 | SPA pura · SSR puro | RE-2 |
| **DEC-13** Reloj + revalidación + SSE | Disponibilidad en tiempo real | RNF-C1, RNF-C3 | WebSocket en toda la app | RE-1, RE-2 |
| **DEC-14** Consola B2B | Oficio distinto, riesgo distinto | RF-20, RF-23, RNF-S5 | Web pública con rol | RE-4 |
| **DEC-15** i18n y multi-moneda | Precio estable, hora local | RF-04, SUP-12 | Conversión en el frontend | RE-5 |

### 14.3 Compromisos asumidos

Toda arquitectura es un conjunto de renuncias. Estas son las del equipo, declaradas:

| Se prioriza | Sobre | Por qué | Dónde se paga |
|---|---|---|---|
| Corrección del inventario (RNF-C1) | Disponibilidad 99,99% | La sobreventa tiene costo regulatorio; 39 min extra de caída al mes, no | RNF-D1 se queda en 99,9% |
| Rechazar rápido | Confirmar y revertir | Revertir una venta confirmada es peor que negarla | Bloqueo pesimista: menor rendimiento en el punto de contención |
| Delegar el pago (SUP-14) | Control del flujo de pago | Reduce PCI a SAQ-A | Dependencia de un tercero; se mitiga con conciliación |
| Un PNR por viaje (DEC-2) | Simplicidad del modelo | El pasajero compra un viaje, no tramos | Una entidad adicional y transacciones más largas |
| Monolito modular (DEC-6) | Microservicios | La garantía de inventario cabe en una transacción | Escalado menos granular |
| Frescura diaria del ETL | Tiempo real | Las preguntas de gerencia son tácticas | Ninguno relevante: el costo de mejorarla es <1 USD/mes |

---

## Anexo A — Bitácora de prompts

> Requisito de la sección 4 del enunciado.

**Asistente:** Claude (Opus 5), vía Claude Code.
**Alcance:** redacción del documento, diseño del modelo de datos, implementación del backend y del ETL, guiones de infraestructura.

> ⚠️ **Para el equipo:** complete esta bitácora con los prompts propios de cada integrante y, sobre todo, con la columna **"corrección del equipo"** — que es la que demuestra criterio y la que el docente puede contrastar en la sustentación. Un prompt sin corrección registrada sugiere que la salida se aceptó sin revisar.

| # | Intención del prompt | Corrección que hubo que hacer |
|---|---|---|
| **P-01** | Encuadre inicial: *"Ayúdame con este parcial"* | El asistente preguntó el alcance y **si ya se tenía la restricción confidencial de la sección 3**. La segunda pregunta era la importante: sin ella habría escrito un documento que después habría que rehacer. Llevó al punto de extensión **PE-0** |
| **P-02** | Traducir el enunciado al español | Tener la rúbrica en español permitió estructurar el documento **contra la rúbrica**, no contra el orden de lectura del enunciado |
| **P-03** | Requisitos que resuelvan las preguntas guía | Los primeros RNF decían *"búsqueda rápida"* sin número → se exigió **métrica + umbral + verificación + riesgo**. Los supuestos eran una lista plana → se agregó **"qué se rompe si cambia"**. Se propuso 99,99% de disponibilidad → se corrigió a **99,9%** con el argumento del conflicto con RNF-C1 |
| **P-04** | Modelo E-R que resuelva las cinco preguntas de 5.3 | Primera propuesta con una sola entidad `flight` → se partió en dos (**DEC-1**). Relación `N—N` directa → se introdujo `itinerary` (**DEC-2**). **Inventario por silla individual → se cambió a conteo por cabina (DEC-3): la corrección de mayor impacto de todo el ejercicio.** `v_inventory_reconciliation` contaba tramos en vez de sillas |
| **P-05** | Arquitectura conectada a los RNF | Propuso bloqueo **optimista** por ser el patrón más habitual → se corrigió tras analizar que con 40 solicitudes por 1 silla produce una tormenta de reintentos. Faltaba el **orden canónico de bloqueo**: se añadió al notar el riesgo de abrazo mortal |
| **P-06** | Implementación con prueba de concurrencia real | La primera prueba usaba `asyncio.gather` (**habría pasado sin ningún bloqueo**), lanzaba en bucle (sin contención), un solo worker y solo verificaba códigos HTTP. Se corrigieron las cuatro cosas |
| **P-07** | AWS con pilares y costos | Propuso Glue **Spark** por defecto → se cambió a Python Shell. Los pilares se citaban de forma decorativa → se exigió nombrar un RF/RNF concreto. Faltaban las desviaciones del Learner Lab → se agregaron como **A-1 a A-4** |
| **P-08** | Ejecutar de verdad lo construido | Aparecieron **tres bugs de tipado de parámetros** invisibles en revisión de código (D-6) y un problema de aislamiento de pruebas (D-7) |

### Balance del uso de IA

**Dónde aportó más:** velocidad de redacción, exhaustividad de las tablas y, sobre todo, el **análisis comparativo de alternativas** — tener las tres opciones de concurrencia escritas con sus contras permitió elegir con criterio en vez de por costumbre.

**Dónde hubo que corregirla:**

1. Propone el patrón **más común**, no el más adecuado al escenario (bloqueo optimista, Glue Spark). Ambos habrían "funcionado" en la demostración y habrían sido malas decisiones bajo el escenario real.
2. Tiende a **métricas sin número** si no se le exige el umbral y el método de medición.
3. Escribe **pruebas que pasan** antes que pruebas que prueban: la primera prueba de concurrencia habría dado verde sin ningún mecanismo de bloqueo.
4. Omite las **desviaciones incómodas** (Single-AZ, `LabRole`) si no se le pide declararlas.

**Consecuencia para la sustentación:** las decisiones sustentables de este trabajo son las que **sobrevivieron a una corrección**, y están documentadas con la alternativa descartada y el motivo.

---

## Anexo B — Guía de ejecución

### Levantar el sistema

```bash
cd backend && docker compose up -d --build
```

Arranca tres servicios:

| Servicio | Puerto | Contenido |
|---|---|---|
| `oltp` | 5433 | PostgreSQL transaccional; ejecuta `01_schema.sql` y `02_seed.sql` al primer arranque |
| `olap` | 5434 | PostgreSQL analítico (`etl/sql/olap_schema.sql`) |
| `api` | 8010 | FastAPI con 4 procesos uvicorn |

Comprobar:

```bash
curl http://localhost:8010/health
```

Documentación interactiva de la API: `http://localhost:8010/docs`

### Reproducir la evidencia de no-sobreventa

```bash
cd backend && python scripts/concurrency_stress.py --workers 40 --seats 1
```

Escenario literal del enunciado:

```bash
cd backend && python scripts/concurrency_stress.py --workers 2 --seats 1
```

### Suite automatizada

```bash
cd backend && pytest tests/ -v
```

### Ejecutar el ETL en local

```bash
cd etl && python glue_job_oltp_to_olap.py --oltp_dsn "postgresql://airline:airline@localhost:5433/airline" --olap_dsn "postgresql://analytics:analytics@localhost:5434/analytics"
```

### Provisionar en AWS

```bash
cd etl/infra && DB_PASSWORD='<clave>' bash setup_aws.sh
```

### Estructura del repositorio

```
docs/
  DOCUMENTO-FINAL.md       Este documento
  diagramas/               Diagramas en SVG y PNG
  evidencia/               Reportes de las pruebas de concurrencia
backend/
  sql/01_schema.sql        Modelo de 5.3 · 26 tablas · la CHECK de no-sobreventa
  sql/02_seed.sql          Red de prueba y materializacion de instancias
  app/services/booking.py  EL BLOQUEO PESIMISTA (DEC-4)
  app/services/search.py   Busqueda directa y con escalas
  app/main.py              7 endpoints
  tests/                   Suite pytest, 4 escenarios de concurrencia
  scripts/                 Generador de la evidencia de RNF-C1
etl/
  glue_job_oltp_to_olap.py Job de Glue Python Shell, incremental e idempotente
  sql/olap_schema.sql      Esquema en estrella
  infra/setup_aws.sh       Provision reproducible del entorno AWS
```

---

## Anexo C — Desviaciones declaradas

> El enunciado es explícito: *"un ajuste no es un error: es información valiosa que debe documentarse"* y *"cualquier desviación debe declararse explícitamente, con su justificación, en lugar de dejarse sin mencionar"*.

### C.1 Diseño vs. implementación

| # | Desviación | Motivo | Documento actualizado |
|---|---|---|---|
| **D-1** | El precio se congela al **retener**, no al emitir. Se agregaron `quoted_fare_id`, `quoted_fare_amount` y `quoted_tax_amount` a `itinerary_segment` | Entre el `HELD` y el pago hay 20 minutos, y las tarifas de aerolínea cambian varias veces al día. Sin esto, el pasajero vería un precio al reservar y se le cobraría otro al pagar | DDL de §8 y diccionario. **DEC-5 no cambia de sentido: se precisa cuándo ocurre el congelamiento** |
| **D-2** | Cotizar **antes** de tomar el bloqueo | Resolver la tarifa elegible requiere tres `JOIN` por tramo; dentro del bloqueo alargaba la sección crítica sin necesidad. Es lectura pura y no participa del invariante | Protocolo de DEC-4 en §9.2 |
| **D-3** | Los **infantes no consumen inventario**: `seats_needed = COUNT(*) WHERE passenger_type <> 'INF'` | Un infante viaja en brazos y no ocupa silla. Regla implícita en el modelo, ahora explícita y consistente en los cuatro caminos que tocan contadores | Diccionario de `reservation_passenger`. Se corrigió también `v_inventory_reconciliation`, que contaba tramos en vez de sillas |
| **D-4** | Los holds se expiran con `SELECT … FOR UPDATE SKIP LOCKED` | Con varias instancias del job en paralelo se bloqueaban entre sí. `SKIP LOCKED` hace el job **horizontalmente escalable sin coordinación externa** — el mismo motivo por el que se descartó una cola en DEC-4: PostgreSQL ya da la primitiva | RF-11 en §6 |
| **D-5** | Errores de dominio con **código estable** (`SEAT_UNAVAILABLE`, `INVENTORY_BUSY`, `INVALID_ITINERARY`, `CREDIT_LIMIT_EXCEEDED`) más objeto `details` | DEC-13 exige que la interfaz no muestre un error genérico sino que recargue disponibilidad y proponga alternativas; para eso necesita distinguir este `409` de cualquier otro sin analizar cadenas de texto | DEC-13 en §10.2 |
| **D-6** | Tres fallos de **tipado de parámetros** corregidos con `CAST` explícito | Ninguno es visible leyendo el código. **(a)** SQLAlchemy no reconoce `:param::tipo`: su expresión regular descarta un `:nombre` seguido de otro `:`, así que el literal llega crudo al motor. **(b)** En `(:param IS NULL OR …)`, con el parámetro nulo PostgreSQL no puede inferir el tipo → `AmbiguousParameter`. **Ambas son fallas del camino por defecto** —un usuario que no filtra por clase tarifaria y no cancela un itinerario específico—, no de casos límite exóticos | §11.2 |
| **D-7** | Las pruebas necesitan `reset_bookings()` con **`TRUNCATE`**, no `DELETE` | La suite pasaba y fallaba de forma alterna porque una prueba deja un vuelo lleno y esa marca sobrevivía a la corrida. Al limpiar, el `DELETE` **falló por dos razones que son ambas correctas**: `payment`, `ticket` y `reservation_event` referencian la reserva **sin `ON DELETE CASCADE`** (en producción una reserva no se borra, se cancela), y `reservation_event` tiene el trigger de inmutabilidad de **RNF-A2**. **La prueba chocó contra las garantías del propio diseño y las garantías ganaron** — que borrar el historial de auditoría sea difícil incluso desde una prueba es la señal de que RNF-A2 está realmente implementado y no solo declarado | §12.2, RNF-A2 |

### C.2 Lo que **no** hubo que ajustar

Vale la pena decirlo porque es la validación real del diseño: **el mecanismo de concurrencia funcionó exactamente como estaba documentado.** El bloqueo pesimista sobre la fila de `flight_inventory` con orden canónico se implementó sin sorpresas, sin abrazos mortales y sin reintentos. La decisión de DEC-3 de controlar el inventario por conteo por cabina fue lo que hizo que la sección crítica cupiera en una sola consulta de bloqueo.

### C.3 Desviaciones del entorno AWS (Learner Lab)

| # | Desviación | Motivo | Impacto |
|---|---|---|---|
| **A-1** | OLTP **Single-AZ**, no Multi-AZ como exige RNF-D1 | El Learner Lab no habilita Multi-AZ | Solo de entorno. El costo del diseño correcto está cuantificado en §13.4 (74,23 USD/mes) |
| **A-2** | Rol IAM compartido **`LabRole`** en vez de privilegio mínimo por componente | El Learner Lab no permite crear roles ni políticas | Debilita RNF-S4 en el entorno de práctica. En producción: un rol por componente |
| **A-3** | El ETL lee del **primario**, no de una réplica como establece DEC-11 | No hay presupuesto para una tercera instancia en el lab | Viola RNF-P4 **solo en el lab**. Se mitiga corriendo en la ventana de menor tráfico. En producción la réplica es obligatoria |
| **A-4** | Contraseñas por parámetro del job en vez de **Secrets Manager** | El Learner Lab restringe Secrets Manager de forma intermitente | Debilita RNF-S3. En producción: Secrets Manager (0,40 USD/secreto/mes) con rotación |

---

## Anexo D — Pendientes para el equipo

- [ ] Integrar la **restricción confidencial de la sección 3** en los tres puntos preparados (PE-0, §2.3).
- [ ] Completar los datos del equipo en la portada.
- [ ] Ampliar la bitácora de prompts (Anexo A) con los propios de cada integrante.
- [ ] Publicar el código en GitHub y añadir el enlace (entregable de 5.6).
- [ ] **Verificar los precios de AWS en la calculadora** antes de entregar: las cifras de §13.4 deben confirmarse contra los precios vigentes.
- [x] ~~Ejecutar `etl/infra/setup_aws.sh` en el Learner Lab~~ **Hecho.** Ambas bases quedaron registradas en el Glue Data Catalog y el ETL corrió con éxito. Salidas reales en [`evidencia/aws-despliegue.md`](evidencia/aws-despliegue.md); los seis obstáculos del despliegue, en §5.7.7 de [`03-aws-etl-catalogo-costos.md`](03-aws-etl-catalogo-costos.md).
- [ ] Adjuntar la **captura de la consola de Glue** mostrando ambas bases del catálogo (punto 8 de la lista de §13.7: es lo único de esa lista que no se puede generar por CLI).
