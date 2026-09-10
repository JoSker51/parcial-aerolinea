# PARCIAL — Sistema de Reservas de una Aerolínea
**Big Data e Ingeniería de Datos**

> Traducción íntegra al español del enunciado `parcial12026-2-EN.docx`. Se conserva la numeración original.

---

## 1. Propósito

Antes de elegir servicios de AWS o tecnologías específicas, cada equipo debe definir **qué debe hacer el sistema** (requisitos funcionales), **bajo qué condiciones debe hacerlo** (requisitos no funcionales), **cómo se organizan los datos** (modelo entidad-relación) y **cómo se estructura la solución** (arquitectura de backend y frontend).

El ejercicio está diseñado para que cada equipo tome sus propias decisiones y las justifique. **No hay una única respuesta correcta**; lo que se evalúa es la coherencia entre las decisiones tomadas y los requisitos que el propio equipo define.

## 2. Escenario de negocio

Una aerolínea regional contrata al equipo para construir desde cero su sistema de reservas de vuelos. Los lineamientos de negocio son **deliberadamente incompletos**:

- Los pasajeros deben poder buscar vuelos, reservar y pagar tiquetes, y consultar o cancelar sus reservas.
- La aerolínea vende tiquetes directamente en su sitio web, pero **también a través de agencias de viaje aliadas**.
- Existen **tarifas diferentes** según la anticipación con que se compra el tiquete y el tipo de silla (económica, ejecutiva).
- Un mismo vuelo puede tener **escalas**, y un pasajero puede reservar un itinerario de **múltiples trayectos en una sola transacción**.
- La gerencia comercial quiere poder analizar después la **ocupación por ruta**, los **ingresos por tarifa** y los **patrones de cancelación**.
- Se menciona que *"el sistema no debe vender más sillas de las que tiene el avión"* y que *"el tráfico se dispara en temporada alta"*, **sin dar ningún número concreto**.

Varios detalles se dejan deliberadamente sin especificar (número de rutas, si existe un programa de viajero frecuente, si se permiten cambios de itinerario, qué pasa con el equipaje). **Parte del ejercicio es decidir con qué supuestos se trabaja y documentarlos explícitamente: un supuesto no declarado es una decisión de arquitectura oculta.**

## 3. Restricción específica por equipo

Cada equipo recibirá, de forma **independiente y confidencial** frente a los demás equipos, una **restricción adicional** sobre el escenario general descrito en la sección 2 (por ejemplo, una condición de negocio, una limitación técnica o una regla de negocio específica).

Esta restricción debe integrarse **coherentemente en todos los entregables** del ejercicio: requisitos, modelo de datos y arquitectura. La consistencia con esta restricción particular hace parte de la evaluación y de la sustentación oral.

## 4. Uso de asistentes de inteligencia artificial

Se **permite** el uso de asistentes de IA (Amazon Q Developer, Claude u otros) y es consistente con la metodología del curso, **siempre que el estudiante pueda explicar y sustentar cualquier decisión generada con su ayuda**. Se exige una condición adicional:

> **Bitácora de prompts (*prompt log*):** el documento final debe incluir, como anexo, una bitácora breve de los principales prompts usados durante el ejercicio y de cómo fueron refinados o corregidos por el equipo.

## 5. Actividades y decisiones a tomar

### 5.1 Requisitos Funcionales (RF)

Escriba los requisitos funcionales **en formato de tabla** (ID, descripción, actor involucrado, prioridad). Preguntas guía que el equipo debe resolver por su cuenta:

- ¿Quiénes son los actores del sistema (pasajero, agente de agencia aliada, personal de aeropuerto, administrador)? ¿Todos interactúan con el mismo sistema, o con interfaces distintas sobre el mismo backend?
- ¿Cómo se maneja una reserva con múltiples trayectos o conexiones? ¿Es una sola reserva con varios vuelos asociados, o varias reservas encadenadas?
- ¿Se permite reservar sin pagar de inmediato (retención temporal de la silla), o el pago es parte atómica de la reserva?
- ¿Qué pasa si el pasajero quiere cancelar o cambiar la fecha?
- ¿Las agencias de viaje aliadas tienen un flujo diferente (descuento, comisión), o usan el mismo proceso que un pasajero final?

### 5.2 Requisitos No Funcionales (RNF)

Defina **al menos un requisito no funcional por categoría**, con una **métrica o condición verificable**. No basta con escribir *"debe ser rápido"* o *"debe ser seguro"*.

| Categoría | Pregunta que el equipo debe resolver |
|---|---|
| **Rendimiento** | ¿Qué tiempo de respuesta es aceptable para una búsqueda de disponibilidad de vuelos? ¿Cambia si hay escalas de por medio? |
| **Consistencia / Concurrencia** | Si dos pasajeros intentan reservar la última silla disponible al mismo tiempo, ¿qué debe garantizar el sistema? |
| **Disponibilidad** | ¿Qué número de disponibilidad (99%, 99.9%, 99.99%) traduce *"no se puede caer en temporada alta"*, y qué implica eso en costo y complejidad? |
| **Seguridad** | ¿Cómo se protegen los datos de pago y los datos personales de los pasajeros? ¿Aplica alguna regulación relevante? |
| **Escalabilidad** | Si la aerolínea agrega 20 rutas nuevas en un año, ¿qué parte de la arquitectura sería el primer cuello de botella? |
| **Auditabilidad** | ¿Qué tan importante es poder reconstruir el historial completo de cambios de una reserva? |

### 5.3 Modelo entidad-relación

Diseñe el modelo entidad-relación considerando, **como mínimo**, entidades como Vuelo, Aeronave, Ruta, Aeropuerto, Pasajero, Reserva, Tarifa, Silla y Pago, **sin asumir que esta lista está completa ni que son exactamente las entidades correctas**. Decisiones que el equipo debe tomar y justificar:

- ¿Cómo se modela un itinerario de múltiples trayectos? ¿Una reserva tiene muchos vuelos asociados, o el itinerario es una entidad propia entre Reserva y Vuelo?
- ¿La asignación de silla es parte de la reserva desde el momento de la compra, o es un proceso separado (check-in) que ocurre después?
- ¿Cómo se representa que el mismo vuelo se repite todos los días? ¿Existe una entidad *"vuelo programado"* distinta de una *"instancia de vuelo en una fecha específica"*?
- ¿Dónde vive el control del inventario de sillas disponibles para evitar la sobreventa?
- ¿Qué nivel de normalización se justifica, dado que este modelo alimentará tanto el sistema transaccional como el pipeline de Big Data del curso?

> **Entregable:** diagrama entidad-relación en notación estándar (Chen o Pata de Gallo / *Crow's Foot*, a elección del equipo, pero **consistente en todo el diagrama**) y un breve **diccionario de datos por entidad**.

### 5.4 Arquitectura del backend

- ¿Monolito o servicios separados (búsqueda de vuelos, gestión de reservas, pagos)? ¿Qué RNF respalda esa elección, en particular el de concurrencia sobre el inventario de sillas?
- ¿Cómo se garantiza que un vuelo no se sobrevenda cuando hay múltiples solicitudes simultáneas? ¿Bloqueo pesimista, bloqueo optimista con reintentos, o una cola que serializa las solicitudes por vuelo?
- ¿Cómo se integra el flujo de pago? ¿Se delega completamente a un proveedor externo, o el sistema maneja parte de la lógica?
- ¿Cómo se exponen los servicios a las agencias de viaje aliadas? ¿La misma API del sitio web del pasajero final, o una API B2B separada?
- ¿Cómo se relaciona esta arquitectura transaccional con el pipeline de Big Data del curso?

### 5.5 Arquitectura del frontend

- ¿Aplicación de una sola página (SPA) o renderizado tradicional del lado del servidor? ¿Qué RNF de rendimiento pesa en esta decisión?
- ¿Cómo se notifica al usuario, en tiempo real, si la silla que estaba seleccionando ya no está disponible?
- ¿Se necesita una interfaz separada para los agentes de agencias de viaje?
- ¿Qué implicación tiene manejar múltiples monedas o idiomas sobre la estructura del frontend?

### 5.6 Primera implementación del backend

Esta sección exige una **primera implementación funcional, aunque parcial**, del backend diseñado en 5.4: una API que exponga al menos las operaciones críticas del sistema, respaldada por una base de datos que implemente el modelo entidad-relación de 5.3. El propósito **no es construir el sistema completo**, sino validar que las decisiones de diseño tomadas hasta ahora son realmente implementables y consistentes entre sí.

**Alcance mínimo exigido:**

- Una base de datos funcional (el motor y el nivel de normalización son los definidos y justificados por el equipo en 5.3).
- Una **API en FastAPI** que exponga, como mínimo, las operaciones de: **buscar vuelos disponibles**, **crear una reserva** y **consultar una reserva existente**.
- La operación de creación de reserva debe **reflejar la decisión de concurrencia tomada en 5.4** (bloqueo pesimista, bloqueo optimista, cola u otro mecanismo): **no basta con que la API funcione en el caso de un solo usuario a la vez**.

**Decisiones que el equipo debe tomar y justificar en esta etapa:**

- ¿El código de la API refleja exactamente las decisiones documentadas en 5.4, o aparecieron ajustes durante la implementación? Si es así, ¿qué los motivó y qué documento se actualiza como resultado?
- ¿Cómo se probó específicamente el escenario de concurrencia descrito en el RNF de 5.2 (dos solicitudes simultáneas por la última silla)? ¿Qué evidencia (prueba automatizada, script, capturas de ejecución) respalda que el mecanismo elegido efectivamente previene la sobreventa?
- ¿Qué parte del modelo entidad-relación de 5.3 hubo que ajustar, si es que hubo alguna, al enfrentar la implementación real? **Un ajuste no es un error: es información valiosa que debe documentarse.**

> **Entregable de esta subsección:** código fuente de la API en GitHub y el esquema de la base de datos (o acceso al repositorio), una breve guía de ejecución, y evidencia concreta de la prueba de concurrencia realizada. Cualquier desviación del diseño de 5.3/5.4 debe declararse explícitamente, con su justificación, en lugar de dejarse sin mencionar.

### 5.7 Implementación en AWS: ETL, catálogo de datos y costos

Esta sección exige una **primera implementación real** que conecte el sistema transaccional con una base de datos analítica, documentando las decisiones de infraestructura con el mismo nivel de justificación exigido en las secciones anteriores.

**Requisitos de implementación:**

- La base de datos **transaccional** que soporta el modelo entidad-relación diseñado en 5.3.
- Una **segunda base de datos PostgreSQL**, con propósito analítico (**OLAP**), que reciba una copia de al menos una tabla o vista derivada de la base transaccional mediante un proceso **ETL**.
- **Ambas bases de datos deben quedar registradas en el AWS Glue Data Catalog**, de modo que sus tablas sean visibles y consultables como metadatos desde Glue.

**Decisiones que el equipo debe tomar y justificar:**

- ¿Qué tabla o combinación de tablas tiene sentido copiar a la base analítica para responder las preguntas de negocio del escenario (ocupación por ruta, ingresos por tarifa, patrones de cancelación)? ¿Se copia tal cual, o se transforma/agrega en el camino?
- ¿El esquema de la base analítica debe ser idéntico al transaccional, o es apropiado un modelo diferente dado su propósito analítico? ¿Cómo se relaciona esto con la discusión de normalización de 5.3?
- ¿Qué servicio de AWS se usa para implementar el proceso ETL? ¿Qué opciones existen dentro del Learner Lab, y por qué elegir una sobre otra?
- ¿Cómo se conectan ambas bases PostgreSQL al Glue Data Catalog (conexión JDBC, *crawler*, rol IAM, acceso de red)?
- ¿El ETL debe ejecutarse una sola vez, periódicamente, o dispararse por eventos? ¿Qué implica cada opción en frescura de los datos frente a costo?

**Justificación de los servicios AWS elegidos:** por cada servicio incluido en esta implementación (instancias de base de datos, servicio de ETL, catálogo de datos, roles IAM, componentes de red), el equipo debe justificar la elección conectándola con **(1)** un requisito funcional o no funcional específico de las secciones 5.1/5.2, y **(2)** uno o más de los **seis pilares del AWS Well-Architected Framework**.

| Servicio AWS | Alternativa considerada | Requisito que respalda la elección | Pilar(es) Well-Architected |
|---|---|---|---|
| *(a definir por el equipo)* | *(a definir por el equipo)* | *(referencia a un RF/RNF)* | *(uno o más de los 6 pilares, con breve explicación)* |

**Proyección de costos:** elabore una proyección de costo mensual usando la calculadora de precios de AWS o los precios públicos vigentes, incluyendo:

- Costo estimado de cada instancia de base de datos (transaccional y analítica), **indicando el tamaño/clase de instancia asumido**.
- Costo estimado del proceso ETL, según el servicio elegido y la frecuencia de ejecución decidida.
- Costo estimado de los *crawlers* y del catálogo de Glue.
- Una **proyección adicional** de cómo cambiaría ese costo si el volumen de datos o la frecuencia de actualización se multiplicaran por **10**, conectada explícitamente con los pilares de **optimización de costos** y **sostenibilidad**.

> **Entregable de esta subsección:** diagrama de arquitectura actualizado que muestre ambas bases de datos, el proceso ETL y su relación con el Glue Data Catalog; tabla completa de justificación de servicios; tabla de proyección de costos con supuestos explícitamente declarados.

## 6. Entregables

| Entregable | Contenido |
|---|---|
| **Entrega parcial** | Requisitos funcionales y no funcionales (5.1 y 5.2) |
| **Entrega final** | Documento completo: contexto y supuestos, RF, RNF, modelo entidad-relación, arquitectura de backend y frontend, primera implementación del backend (5.6), implementación en AWS (5.7), y anexo con la bitácora de prompts |

## 7. Sustentación oral

**Todos los integrantes del equipo** deben poder sustentar cualquier parte del documento entregado, incluida la restricción específica asignada en la sección 3. Durante la sustentación, el docente puede plantear **variaciones del escenario no cubiertas en el documento** (por ejemplo, un cambio en una política de negocio) y pedir al equipo que explique, en el momento, qué parte de su diseño se vería afectada.

> ⚠️ **La sustentación oral es eliminatoria para la sección correspondiente:** si el equipo no puede sustentar coherentemente una decisión documentada, la nota de esa sección específica se anula, sin importar la calidad del documento escrito.

## 8. Rúbrica de evaluación (20 puntos)

| Criterio | Insuficiente (0-40%) | Aceptable (41-70%) | Sobresaliente (71-100%) | Peso |
|---|---|---|---|---|
| **Requisitos funcionales** | Genéricos, no distinguen reserva de compra ni manejan itinerarios de múltiples trayectos | Cubren el escenario pero con ambigüedades sin resolver | Resuelven explícitamente las preguntas guía, declaran supuestos e integran la restricción específica del equipo | **10%** |
| **Requisitos no funcionales** | Sin métricas verificables | Métricas presentes pero no conectadas con un riesgo real de negocio | Métricas concretas trazables a un riesgo de negocio explícito, en particular la concurrencia | **10%** |
| **Modelo entidad-relación** | No resuelve escalas ni el control de sobreventa | Modelo funcional pero con decisiones injustificadas | Modelo coherente que resuelve explícitamente escalas, vuelos recurrentes e inventario de sillas, con justificación | **15%** |
| **Arquitectura backend y frontend (diseño)** | Elecciones tecnológicas sin justificación | Justificación superficial, mal conectada con los RNF | Cada decisión se conecta explícitamente con un RNF o RF específico, especialmente el manejo de concurrencia | **15%** |
| **Primera implementación del backend** | La API o la base de datos no funciona, o no refleja el diseño 5.3/5.4 | Implementación funcional pero sin evidencia de la prueba de concurrencia, o sin declarar desviaciones del diseño | Implementación funcional, fiel al diseño (o con desviaciones justificadas), con evidencia concreta de la prueba de concurrencia | **10%** |
| **Implementación ETL, catálogo Glue y costos** | El ETL no es funcional, o las bases no aparecen registradas en el Glue Data Catalog | Implementación funcional pero sin justificación clara de servicios ni de costos | Implementación funcional, servicios justificados con RF/RNF y un pilar Well-Architected, costos con supuestos declarados y escenario de crecimiento | **20%** |
| **Claridad y trazabilidad del documento** | Secciones desconectadas | Documento completo pero sin referencias cruzadas | Una decisión de arquitectura puede rastrearse hasta el requisito específico que la origina | **10%** |
