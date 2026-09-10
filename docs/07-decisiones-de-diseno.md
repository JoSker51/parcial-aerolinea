# Decisiones de diseño y modelo de datos — explicación razonada

> Este documento responde a una pregunta concreta: **¿por qué el sistema quedó así y no de otra forma?**
>
> Está escrito para leerse de corrido, no como tabla de consulta. Cubre los supuestos que se tomaron, las decisiones detrás de los requisitos funcionales y no funcionales, y una explicación del modelo entidad-relación entidad por entidad. Cada decisión viene con **la alternativa que se descartó y el motivo**, porque una decisión sin alternativa descartada no es una decisión: es lo primero que se le ocurrió a alguien.
>
> Referencias cruzadas: `SUP-n` supuesto · `RF-nn` requisito funcional · `RNF-Xn` requisito no funcional · `DEC-n` decisión de arquitectura · `RE-n` riesgo de negocio.

---

## 1. El punto de partida: un enunciado deliberadamente incompleto

El escenario dice qué debe hacer el sistema (buscar, reservar, pagar, cancelar; venta directa y por agencias; tarifas por anticipación y cabina; escalas; análisis comercial posterior) pero **no da un solo número**. No dice cuántas rutas hay, ni cuánto tráfico, ni qué disponibilidad se exige. Dice cosas como *"el tráfico se dispara en temporada alta"* y *"el sistema no debe vender más sillas de las que tiene el avión"*.

Eso obliga a una decisión previa a cualquier decisión técnica: **traducir frases a números**. Un requisito que dice "debe ser rápido" no se puede verificar, no se puede probar y no se puede defender. Por eso el trabajo empieza por los supuestos y no por la tecnología.

La regla que se siguió, tomada del propio enunciado: *un supuesto no declarado es una decisión de arquitectura oculta*. Todo número que aparece más adelante en el diseño tiene un supuesto detrás con nombre propio.

---

## 2. Los supuestos, y qué se rompe si cambian

Cada supuesto es **una variable, no una verdad**. Lo importante no es que el número sea correcto —nadie puede saberlo sin el negocio real— sino saber **qué parte del diseño depende de él**. Esa es la columna que de verdad importa.

### 2.1 Escala y volumen

| ID | Supuesto | Por qué ese valor | Qué se rompe si cambia |
|---|---|---|---|
| **SUP-1** | 35 rutas, 18 aeropuertos | "Regional" en Colombia (una operación tipo Satena o EasyFly) está en el orden de decenas de rutas, no de cientos | Con 500 rutas la búsqueda con escalas deja de ser un `JOIN` viable y exige un motor de búsqueda precomputado |
| **SUP-2** | ~120 vuelos/día, 12 aeronaves, 3 configuraciones (ATR-72 de 68 sillas, A320 de 180, A320neo de 186) | 12 aeronaves × ~10 tramos diarios. Las tres configuraciones son las que justifican separar `aircraft_model` de `aircraft` | Con flota homogénea el mapa de sillas colapsa a una constante y la entidad `seat_map` desaparece |
| **SUP-3** | Horizonte de venta de 360 días (~43.000 instancias de vuelo vivas) | Estándar del sector: los GDS abren entre 330 y 360 días | Es el número que dimensiona la tabla más grande del OLTP y el volumen del ETL |
| **SUP-4** | 30 búsquedas/s base, 120/s pico diario, ×5 = 600/s en temporada alta. Relación búsqueda:reserva ≈ 80:1 | Es la traducción numérica de *"el tráfico se dispara en temporada alta"*. El 80:1 es el *look-to-book* típico del sector | Es el insumo directo de los umbrales de rendimiento. Con una relación 10:1 el cuello de botella se movería de la búsqueda al inventario |
| **SUP-5** | 40 solicitudes concurrentes sobre el mismo vuelo | Es el pico realista el día de apertura de venta de temporada alta — el escenario que hace fallar el control de sobreventa | **Define el mecanismo de concurrencia elegido.** Es el número contra el que se probó el sistema |

### 2.2 Reglas de negocio

| ID | Supuesto | Por qué se decidió así | Qué se rompe si cambia |
|---|---|---|---|
| **SUP-6** | Sin sobreventa deliberada: `vendidas + retenidas ≤ capacidad` | Lectura literal del enunciado. Muchas aerolíneas reales sí sobrevenden, pero el texto dado es explícito | **Nada estructural.** `oversell_factor` se modeló como *columna*, no como constante, justamente para esto: permitir 5% de overbooking es un `UPDATE`, no un rediseño |
| **SUP-7** | Se puede reservar sin pagar: estado `HELD` con retención de 20 minutos | Sin retención el pasajero pierde la silla mientras digita la tarjeta, y la conversión se desploma. 20 min ≈ checkout con 3-D Secure más margen | Si el pago fuera atómico desaparece `HELD`, desaparece el proceso de expiración, y **el bloqueo de inventario tendría que sostenerse durante la llamada al proveedor de pago** — inaceptable |
| **SUP-8** | Los cambios de fecha se modelan como cancelación + reemisión en el mismo PNR | Preserva el historial: el tramo viejo queda `CANCELLED`, el nuevo nace `CONFIRMED`. Resuelve la auditabilidad sin lógica adicional | Editar el tramo en sitio perdería la trazabilidad, y sin trazabilidad no hay forma de calcular patrones de cancelación |
| **SUP-9** | Sin programa de viajero frecuente en v1 | Reduce alcance sin cerrar la puerta: `passenger.loyalty_id` queda reservado y nulo | Un programa de millas agrega una entidad y un motor de acumulación. **Afecta el cálculo de precio, no el control de inventario** — por eso es un punto de extensión aislado |
| **SUP-10** | Equipaje de mano incluido; bodega es producto auxiliar (`ancillary`) | Separa "vender equipaje" (que sí es del sistema de reservas) de "rastrear maletas" (que es un sistema de operaciones distinto) | Sin la entidad `ancillary`, los ingresos por equipaje no aparecerían en el análisis de ingresos |
| **SUP-11** | La silla se asigna en el check-in, es opcional y con costo en económica, y **no participa del control de sobreventa** | Es la decisión más consecuente del modelo entero. Ver §5.3 | Si la silla fuera obligatoria al comprar, el punto de contención pasaría de **una fila por cabina** a **una fila por silla**: más filas, más bloqueos, sin ninguna ganancia de negocio |
| **SUP-12** | Monedas COP y USD; el precio se almacena en la moneda de venta y no se reconvierte | Un precio reconvertido al vuelo hace que un tiquete "cambie de precio" al consultarlo: inaceptable contable y legalmente | Más monedas no cambian el modelo, solo el catálogo y la fuente de tasas |
| **SUP-13** | Agencias: tarifa neta (descuento) **y** comisión liquidada mensualmente, con contrato, cupo de crédito y credenciales propias | El enunciado ofrece "descuento, comisión" como alternativas; se toman ambas porque es el modelo B2B estándar y no se excluyen | Define la necesidad de una API B2B separada y de la entidad `agency` |
| **SUP-14** | El pago se delega totalmente a un proveedor externo (*hosted checkout* + webhook). El sistema **nunca** ve el número de tarjeta | Reduce el alcance PCI-DSS de SAQ-D a **SAQ-A**: la diferencia entre una auditoría de meses y un cuestionario. **Es la decisión de seguridad de mayor impacto del proyecto** | Procesar la tarjeta en casa cambiaría por completo los requisitos de seguridad y haría el proyecto inviable para una aerolínea regional |
| **SUP-15** | El sistema es fuente de verdad **de la venta, no de la operación**. Retrasos y cambios de aeronave entran desde un sistema externo | Evita que el ejercicio se convierta en un sistema de despacho de vuelos | `flight_instance` tiene estado y aeronave asignada porque debe **recibir** esos eventos, aunque no los origine |

---

## 3. Decisiones detrás de los requisitos funcionales

### 3.1 Un solo dominio, cuatro superficies de entrada

La primera pregunta es quién usa el sistema. Hay **siete actores**: pasajero, agente de agencia, personal de aeropuerto, administrador comercial, proveedor de pagos, sistema de operaciones y analista de negocio.

La decisión no es cuántos actores hay, sino **si comparten backend**. La respuesta fue: **interfaces distintas sobre el mismo backend y el mismo dominio.**

*¿Por qué no un backend por canal?* Porque el requisito de no-sobreventa es el que manda. Si cada canal tuviera su propia lógica de inventario, la garantía habría que probarla cuatro veces, y **se rompería la primera vez que los canales divergieran**. Un solo módulo de reservas significa una sola implementación de la regla crítica, y una sola prueba que la valide.

### 3.2 Una reserva multi-tramo es un PNR, no varias reservas encadenadas

El enunciado pregunta explícitamente si un itinerario de múltiples trayectos es una reserva con varios vuelos o varias reservas encadenadas. Se eligió **una sola reserva (un PNR) con una entidad `itinerary` intermedia**, por tres razones:

1. **El pasajero compró un viaje, no tres vuelos.** Si se cancela el primer tramo, la aerolínea tiene una obligación sobre el itinerario completo, no sobre un tramo suelto. Con reservas encadenadas habría que inventar y mantener a mano el vínculo entre ellas.
2. **El pago es uno solo** — el enunciado dice "en una sola transacción". Con reservas separadas habría N pagos y una compensación distribuida entre ellos.
3. **Una ida y vuelta no se puede tarifar por tramo suelto.** El precio de ida-y-vuelta es una propiedad del itinerario, no la suma de sus partes.

### 3.3 Reservar y comprar son operaciones distintas

Esta es la distinción que la rúbrica evalúa de forma específica, y el diseño la hace explícita separándola en dos requisitos:

- **RF-10 — retener**: bloquea inventario de todos los tramos de forma atómica, por 20 minutos, garantizando `vendidas + retenidas ≤ capacidad`. Es **síncrono y transaccional**.
- **RF-12 — confirmar**: pasa de `HELD` a `CONFIRMED` únicamente al recibir la confirmación de pago. Es **asíncrono y externo**.

La sutileza que sostiene todo el diseño de concurrencia: **el pago no es atómico con la reserva, pero la retención sí es atómica con la verificación de inventario.** Meter el pago dentro de la transacción significaría mantener una fila de inventario bloqueada mientras responde un tercero — durante segundos, bloqueando el vuelo entero.

### 3.4 Las agencias: mismo dominio, distinto contrato

*¿Tienen las agencias un flujo diferente?* La respuesta precisa es: **flujo de dominio idéntico, contrato de API y condiciones comerciales distintos.**

Lo que cambia realmente es el **paso de pago**: la agencia no paga con tarjeta, consume cupo de crédito y se le liquida mensualmente. Lo que **no** cambia es el paso de inventario, y eso es deliberado: reutilizar el flujo de dominio garantiza que la regla de no-sobreventa sea idéntica en ambos canales.

Lo que sí se separa es la **superficie de API** (`/api/v1/**` pública, `/b2b/v1/**` para agencias), porque son contratos con ciclos de vida distintos: la web pública se puede cambiar el martes, mientras que un integrador B2B necesita meses de aviso. Mezclarlas obligaría a congelar la evolución de la web al ritmo del socio más lento.

### 3.5 Nada se borra

Toda transición de estado —crear, confirmar, cancelar, expirar— se registra de forma inmutable con actor, marca de tiempo, canal, estado anterior y estado posterior. Cancelar **no** borra la reserva: la marca como cancelada y deja el rastro.

Esto empezó como un requisito de cumplimiento, pero resultó tener una consecuencia analítica que no era obvia: **los "patrones de cancelación" que pide la gerencia comercial no se pueden calcular sin ese historial.** Una reserva cancelada que solo guarda su estado final no dice ni cuándo se canceló, ni cuánto tiempo estuvo viva, ni a cuántos días de la salida. Un requisito de auditoría terminó habilitando una capacidad de negocio.

---

## 4. Decisiones detrás de los requisitos no funcionales

La regla que se aplicó a todos: **un requisito que no se puede medir no es un requisito, es un deseo.** Cada uno lleva métrica, umbral, método de verificación y el riesgo de negocio concreto que mitiga.

Los seis riesgos que dan sentido a las métricas: sobreventa (**RE-1**), abandono por lentitud (**RE-2**), caída en temporada alta (**RE-3**), fuga de datos (**RE-4**), disputa no reconstruible (**RE-5**) y crecimiento que degrada (**RE-6**).

### 4.1 Rendimiento: por qué el umbral cambia con escalas

**p95 ≤ 400 ms para vuelos directos, p95 ≤ 1.200 ms con escalas.** No es arbitrario que sean distintos, y la razón es estructural:

- Buscar un vuelo **directo** es una consulta indexada sobre una tabla: origen + destino + fecha.
- Buscar **con escalas** es una búsqueda de caminos de longitud 2-3 en el grafo de la red, validando el tiempo mínimo de conexión en cada nodo intermedio.

Son dos problemas computacionales distintos. Fijarles el mismo umbral obligaría a **precomputar todos los itinerarios posibles**, un costo que a 35 rutas no se justifica. Es preferible aceptar que la búsqueda compleja tarde más y decirlo explícitamente.

### 4.2 Concurrencia: la decisión que sostiene el sistema

*Si dos pasajeros piden la última silla al mismo tiempo, ¿qué debe garantizar el sistema?*

La respuesta elegida: **uno gana de forma determinista y el otro recibe un rechazo inmediato y claro.** No un error genérico, no una confirmación optimista que luego se revierte, y en ningún caso dos confirmaciones.

La preferencia declarada es **rechazar rápido antes que confirmar y revertir**, y tiene una justificación de negocio, no de gusto técnico: revertir una venta ya confirmada tiene costo regulatorio (denegación de embarque, compensación, reubicación). Un rechazo no tiene ese costo. Esa asimetría es la que justifica el mecanismo elegido.

Se evaluaron tres mecanismos contra el escenario real (**40 solicitudes por 1 silla**, SUP-5):

| Mecanismo | Cómo se comporta bajo alta contención | Veredicto |
|---|---|---|
| **Optimista con reintentos** | Las 40 leen, calculan y escriben condicionando por versión. **39 fallan y reintentan; al reintentar vuelven a chocar.** Tormenta de reintentos justo en el momento de mayor valor comercial, con latencia impredecible | ✗ Es óptimo cuando los conflictos son **raros**. Aquí el conflicto **es** el caso de uso |
| **Cola serializadora por vuelo** | Serialización perfecta, sin bloqueos. Pero la reserva pasa a ser **asíncrona**: el usuario recibe "procesando" y hay que sondear o notificar. Añade broker, consumidores y manejo de orden | ✗ Sobre-ingeniería a esta escala. Es la respuesta correcta a 10.000 solicitudes/s por vuelo (venta de entradas de concierto), no a 40 |
| **Pesimista con `SELECT … FOR UPDATE`** ✅ | Las 40 se serializan sobre **una sola fila corta**, con transacción de ~5 ms. La primera gana; las 39 siguientes leen el estado ya actualizado y reciben un rechazo limpio. Determinista, síncrono, sin reintentos | ✅ **Elegido** |

Tres condiciones hacen que el pesimista sea el correcto **aquí**, y conviene enunciarlas porque en otro contexto la respuesta sería distinta: (1) la contención está concentrada en una sola fila corta; (2) la transacción es breve y **no incluye ninguna llamada externa**; (3) el requisito prefiere rechazar a revertir.

Se añadieron dos refuerzos que no son cosméticos:

- **Orden canónico de bloqueo.** Las filas se bloquean siempre ordenadas por identificador de vuelo. Sin ese orden, dos itinerarios que comparten tramos en sentido inverso (A→B→C y C→B→A) se bloquearían mutuamente. Con orden fijo el abrazo mortal es **imposible por construcción**, no improbable.
- **Una restricción `CHECK` en la base de datos**, deliberadamente redundante con la lógica de la aplicación. Si un error de programación, una migración o una consulta manual intentaran sobrevender, la transacción falla en el motor. **La regla de negocio más importante del sistema no debe vivir únicamente en el código de aplicación.**

### 4.3 Disponibilidad: por qué 99,9% y no 99,99%

*"No se puede caer en temporada alta"* es una frase, no un número. Traducirla exige decidir qué se paga por ella:

| Nivel | Caída al mes | Qué exige | Costo relativo |
|---|---|---|---|
| 99% | 7,2 h | Una instancia, respaldo diario | ×1 |
| **99,9%** | **43,2 min** | **Multi-AZ, despliegue sin caída, autoescalado** | **≈ ×2** |
| 99,99% | 4,3 min | Multi-región activo-activo, réplica de escritura entre regiones | ≈ ×5-8 |

Se eligió **99,9%**, y la razón no es principalmente el dinero: **99,99% obliga a multi-región activa, y una base de datos escribible en dos regiones no puede garantizar de forma barata la serialización del inventario.** Habría que elegir entre disponibilidad extrema y no-sobreventa, y el enunciado es explícito en que la sobreventa es inaceptable.

Es decir: **se priorizó la corrección del inventario sobre 39 minutos adicionales de disponibilidad al mes.** El refuerzo a 99,95% en temporada alta se consigue con capacidad preaprovisionada y congelamiento de despliegues, no con más arquitectura.

El costo de esa decisión está cuantificado: pasar de Single-AZ a Multi-AZ son **+28,58 USD/mes (+63%)**. Poder decir la cifra es lo que separa "elegimos 99,9%" de "elegimos 99,9% y sabemos lo que cuesta".

### 4.4 Seguridad: la regulación como decisión de arquitectura

Aplican tres marcos a la vez:

1. **PCI-DSS**, por procesar pagos con tarjeta. Aquí está la decisión de mayor impacto: delegar todo el manejo del instrumento de pago a un proveedor externo reduce el alcance de **SAQ-D a SAQ-A**, el nivel más bajo. **Es una decisión de arquitectura tomada por una razón regulatoria**, y así hay que sustentarla.
2. **Ley 1581 de 2012 (Habeas Data) y Decreto 1377 de 2013**: exigen autorización previa e informada, finalidad declarada y derecho de supresión. De ahí la retención acotada a 5 años y la anonimización posterior.
3. **RGPD**, aplicable si se vende a residentes de la UE. Se satisface con el mismo mecanismo de anonimización, por eso no se lista aparte.

Un detalle de diseño que vale la pena notar: consultar una reserva con el apellido equivocado devuelve **404, no 403**. Un 403 confirmaría que el PNR existe, y eso facilitaría enumerar reservas ajenas.

### 4.5 Escalabilidad: cuál es realmente el primer cuello de botella

*Si la aerolínea agrega 20 rutas en un año, ¿qué se satura primero?*

La respuesta intuitiva sería "el almacenamiento" o "la base de datos". Es incorrecta. **El primer cuello de botella es la explosión combinatoria de la búsqueda con escalas:**

- Los tramos **directos** crecen de forma lineal: de 35 a 55 rutas es +57% de filas, algo que un índice absorbe sin problema.
- Los itinerarios **con conexión** crecen con el número de **pares** de rutas que comparten un aeropuerto de conexión: aproximadamente cuadrático. De ~10² caminos a evaluar se pasa a ~3×10².

Lo primero en saturarse es la **CPU de la base de datos durante la búsqueda de itinerarios**, no el disco ni el motor de reservas. La mitigación es por etapas y en orden: (1) caché de resultados por origen-destino-fecha con TTL corto, porque el 90% del tráfico se concentra en pocas rutas; (2) réplicas de lectura para separar búsqueda de reserva; (3) **solo si se superan ~150 rutas**, precomputar itinerarios. No se hace (3) desde el día uno: sería optimizar para un problema que no se tiene.

### 4.6 Auditabilidad: tres razones, no una

Reconstruir el historial completo de una reserva es **crítico**, y por tres motivos que conviene separar porque apuntan a interlocutores distintos:

1. **Comercial** — las disputas por cancelaciones y cambios son frecuentes, y sin historial se pierden por defecto.
2. **Financiera** — la liquidación de comisiones a agencias es dinero real y debe ser auditable.
3. **Analítica** — los patrones de cancelación que pide la gerencia **no son calculables sin el historial de transiciones**.

Por eso el registro de auditoría se escribe **dentro de la misma transacción** que la operación que lo produce. Si fuera asíncrono, un fallo entre la operación y su registro dejaría una transición sin rastro, y el requisito exige el 100%. Y los permisos de la base revocan `UPDATE` y `DELETE` sobre esa tabla incluso para el rol de la aplicación.

---

## 5. El modelo entidad-relación, explicado

El enunciado sugiere una lista de entidades (Vuelo, Aeronave, Ruta, Aeropuerto, Pasajero, Reserva, Tarifa, Silla, Pago) y advierte que no hay que asumir que sea correcta ni completa. **No lo es**: le faltan entidades imprescindibles, y una de las que nombra —"Vuelo"— es ambigua y hay que partirla en dos.

El modelo final tiene **26 tablas** en tercera forma normal, con tres excepciones declaradas. Notación **Pata de Gallo (Crow's Foot)**, consistente en todo el diagrama.

### 5.1 "Vuelo" son dos entidades distintas

> *¿Cómo se representa que el mismo vuelo se repite todos los días?*

Se separó en:

- **`scheduled_flight`** — la plantilla recurrente: *el AV-8320 sale a las 07:15 de Bogotá a Medellín, de lunes a viernes, entre el 1 de abril y el 30 de octubre*.
- **`flight_instance`** — el vuelo concreto: *el AV-8320 del 14 de junio de 2026, con la aeronave HK-4812 asignada, en estado `SCHEDULED`*.

**La alternativa descartada** era una sola tabla `flight` con una fila por fecha y las reglas de recurrencia repetidas en cada fila. Se descartó por tres razones:

1. Sin la separación, **cambiar el horario de un vuelo recurrente obliga a actualizar ~250 filas**, con riesgo de tocar fechas que ya tienen pasajeros.
2. **La capacidad no es del vuelo programado, es de la instancia.** Si el 14 de junio se cambia el A320 de 180 sillas por un ATR-72 de 68, esa instancia y solo esa cambia de capacidad. Con una sola tabla no hay dónde poner esa realidad.
3. El pipeline analítico necesita el programado como **dimensión** y la instancia como **hecho**. Si están mezclados, el modelo estrella se complica innecesariamente.

El costo aceptado es un proceso de **materialización** que genera las instancias dentro del horizonte de venta. Es un trabajo programado diario, no una complicación estructural.

### 5.2 El itinerario es una entidad propia

> *¿Una reserva tiene muchos vuelos asociados, o el itinerario es una entidad entre Reserva y Vuelo?*

La cadena quedó en tres niveles:

```
reservation (1) ── (N) itinerary ── (N) itinerary_segment ── (1) flight_instance
```

Una **reserva** agrupa itinerarios (ida, regreso). Cada **itinerario** agrupa tramos ordenados: Bogotá→Medellín→Cartagena son **dos tramos de un solo itinerario**, no dos viajes.

**La alternativa descartada** era una relación directa `reservation N—N flight_instance` con una tabla puente plana. Se descartó porque **pierde la agrupación de viaje**, y hay tres preguntas que el modelo debe poder responder y que la tabla plana no responde:

- ¿Estos dos tramos son una conexión, o dos vuelos independientes que el pasajero compró el mismo día?
- Si se cancela el regreso, ¿sobrevive la ida?
- ¿Cuál es el origen y destino **del viaje**, para reportar ocupación por ruta comercial?

La razón de fondo: **el itinerario es la unidad de cancelación y de tarifación; el tramo es la unidad de inventario.** Son granos distintos y por eso necesitan entidades distintas. Colapsarlos obligaría a reconstruir esa distinción en el código de aplicación, que es exactamente donde no debe vivir.

### 5.3 Dónde vive el control de sobreventa

> *¿Dónde vive el inventario? ¿La silla es parte de la reserva o del check-in?*

Existe **`flight_inventory`**, con **una fila por (instancia de vuelo × cabina)**, que guarda `capacity`, `seats_sold`, `seats_held` y `oversell_factor`. **Ahí, y solo ahí, vive el control de sobreventa.**

La silla física (`seat_assignment`) es una entidad **separada y opcional** que **no participa del control de capacidad**.

**La alternativa descartada** era controlar el inventario marcando sillas individuales como ocupadas. Se descartó por tres razones, en orden de peso:

1. **Es el modelo real del negocio.** Se vende "un asiento en económica", no "el 14C". El pasajero que no elige silla igual consume inventario.
2. **Concurrencia** — y esta es la razón técnica decisiva. Con conteo por cabina, el punto de contención es **una sola fila** que se puede bloquear de forma corta y determinista. Con sillas individuales, dos compradores del mismo vuelo tocan filas distintas: **nadie bloquea a nadie, pero nadie controla el total**. Habría que contar sillas libres en cada compra, y ese conteo **es exactamente la condición de carrera que se quiere evitar**.
3. **Cambio de aeronave.** Con conteos, comparar `seats_sold` contra la nueva capacidad es inmediato. Con sillas asignadas hay que reasignar 180 filas y resolver colisiones.

De aquí se sigue la consecuencia coherente: **desacoplar la silla de la venta es lo que permite que el control de capacidad sea barato**, y por eso la silla se asigna en el check-in. La unicidad de la silla se protege aparte con una restricción `UNIQUE (flight_instance_id, seat_number)` — porque es un problema de **integridad**, no de **capacidad**. Son dos problemas distintos y merecen mecanismos distintos.

### 5.4 El precio se congela en el tiquete

`ticket` almacena los montos (tarifa, impuestos, cargos, descuento, comisión, moneda) y la referencia a la regla tarifaria **vigente en el momento de la emisión**, en vez de recalcular el precio al consultar.

**La alternativa descartada** era guardar solo el identificador de tarifa y recomponer el precio con un `JOIN`. Se descartó porque **un tiquete es un contrato**: si mañana cambia la tarifa o la regla de penalidad, el tiquete vendido ayer no puede cambiar de precio ni de condiciones al consultarlo.

Es una **denormalización deliberada de naturaleza temporal**, no un descuido: el dato no es "el precio de la tarifa", es "el precio que este pasajero pagó".

> **Ajuste que apareció al implementar:** el precio hay que congelarlo **al retener**, no al emitir. Entre el `HELD` y el pago hay 20 minutos, y las tarifas de aerolínea cambian varias veces al día — el pasajero vería un precio al reservar y se le cobraría otro al pagar. Por eso `itinerary_segment` guarda la cotización, y `ticket` la copia al emitir. La decisión no cambió de sentido: se precisó **cuándo** ocurre el congelamiento.

### 5.5 Nivel de normalización

**3FN estricta en el OLTP, con tres excepciones nombradas.** El error que había que evitar es diseñar una sola base "que sirva para todo", porque queda mal normalizada para transaccionar y mal desnormalizada para analizar.

| Excepción | Qué se duplica | Por qué se acepta |
|---|---|---|
| `flight_inventory.seats_sold` / `seats_held` | Son agregados derivables de los tramos y tiquetes | Calcularlos con un `COUNT` en cada reserva sería **el peor punto de contención posible**: un agregado sobre miles de filas dentro de la transacción crítica. Materializarlos convierte el control en la lectura y escritura de **una fila**. La consistencia se garantiza porque **solo** el servicio de inventario los modifica, siempre en la misma transacción |
| Montos en `ticket` | Precio y condiciones del catálogo | Denormalización temporal: es un valor histórico, no una copia (§5.4) |
| `flight_instance.departure_utc` / `arrival_utc` | Derivables del programado + fecha + zona horaria | La aritmética de zonas horarias con horario de verano dentro de una consulta de búsqueda es cara y propensa a error. Se calcula una vez al materializar y se indexa — **habilita el índice que sostiene el umbral de rendimiento** |

**Y el modelo analítico es otro.** Las consultas de gerencia (ocupación por ruta, ingresos por tarifa, patrones de cancelación) son escaneos agregados sobre millones de filas; ejecutarlas contra el transaccional competiría por CPU con la ruta crítica de reserva. Por eso existe una segunda base en **esquema en estrella**, y **el ETL es precisamente la traducción entre ambos mundos** — un componente de diseño, no una tubería trivial.

### 5.6 Las 26 tablas, por bloques

| Bloque | Entidades | Qué resuelve |
|---|---|---|
| **Red y flota** | `airport`, `route`, `aircraft_model`, `aircraft`, `seat_map`, `seat_map_seat`, `cabin` | El catálogo casi estático. `airport.min_connection_min` guarda el tiempo mínimo de conexión **por aeropuerto**, no como constante global: conectar en un aeropuerto grande toma más que en uno pequeño. `cabin` es una entidad y no un `ENUM` para que agregar `PREMIUM_ECO` sea un `INSERT`, no una migración de tipo |
| **Programación** | `scheduled_flight`, `flight_instance`, `flight_inventory` | La separación de §5.1 y el control de sobreventa de §5.3. `flight_inventory` es **la tabla más importante del sistema**: es la fila que se bloquea |
| **Tarifas** | `fare_class`, `fare_rule`, `fare` | `fare_class` conecta "tipo de silla" con "precio". `fare_rule.min_advance_days` implementa la **tarifa por anticipación** del enunciado, y alimenta el cálculo de reembolsos y penalidades |
| **Reserva** | `reservation`, `itinerary`, `itinerary_segment`, `reservation_passenger`, `passenger`, `agency` | La cadena de §5.2. `reservation_passenger.passenger_ref` es un identificador **local** al PNR (1, 2, 3…), lo que permite emitir tiquetes sin exponer el identificador global del pasajero. Los infantes se marcan aparte porque **no consumen inventario**: viajan en brazos |
| **Emisión y pago** | `ticket`, `seat_assignment`, `ancillary`, `payment`, `payment_event`, `refund` | `ticket` tiene grano **pasajero × tramo**, que es el grano más fino con valor monetario y por tanto el grano del hecho analítico. `payment` guarda solo la referencia opaca del proveedor, **nunca datos de tarjeta**. La unicidad del identificador de evento del proveedor es lo que hace **idempotente** la confirmación: un webhook reentregado choca contra el índice y se descarta |
| **Auditoría** | `reservation_event` | Solo inserción, sin `UPDATE` ni `DELETE`. Es a la vez artefacto de cumplimiento y **fuente del pipeline analítico** (§4.6) |

---

## 6. Resumen: qué se priorizó sobre qué

Toda arquitectura es un conjunto de renuncias. Estas son las que se tomaron, declaradas:

| Se priorizó | Sobre | Por qué | Dónde se paga |
|---|---|---|---|
| Corrección del inventario | Disponibilidad de 99,99% | La sobreventa tiene costo regulatorio; 39 minutos extra de caída al mes, no | La disponibilidad se queda en 99,9% |
| Rechazar rápido | Confirmar y luego revertir | Revertir una venta confirmada es peor que negarla | Bloqueo pesimista: menor rendimiento en el punto de contención |
| Delegar el pago | Controlar el flujo de pago | Reduce el alcance PCI a SAQ-A | Dependencia de un tercero, mitigada con conciliación |
| Un PNR por viaje | Simplicidad del modelo | El pasajero compra un viaje, no tramos sueltos | Una entidad adicional y transacciones más largas |
| Monolito modular | Microservicios | La garantía de inventario **cabe en una transacción de base de datos** | Escalado menos granular, aceptable a esta escala |
| Frescura diaria del ETL | Datos en tiempo real | Las preguntas de gerencia son tácticas, no operativas | Ninguno relevante: mejorar la frescura cuesta menos de 1 USD/mes |

La última fila merece una nota, porque es contraintuitiva y es de las más defendibles: **la decisión de correr el ETL una vez al día no es económica.** Multiplicar la frecuencia por 10 sube la factura apenas un 2%, porque el costo está dominado por las bases encendidas, no por el cómputo del ETL. La restricción a diario es de **valor de negocio** — nadie cambia una ruta por lo que pasó hace diez minutos. Confundir ambas cosas llevaría a negar una mejora que es prácticamente gratis.
