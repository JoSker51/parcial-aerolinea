# Anexo A — Bitácora de prompts

> Requisito de la sección 4 del enunciado: *"el documento final debe incluir, como anexo, una bitácora breve de los principales prompts usados durante el ejercicio y de cómo fueron refinados o corregidos por el equipo."*

**Asistente utilizado:** Claude (Opus 5), vía Claude Code.
**Alcance de la asistencia:** redacción del documento, diseño del modelo de datos, implementación del backend y del ETL, guiones de infraestructura.

> ⚠️ **Para el equipo:** esta bitácora registra la sesión de trabajo asistida por IA que produjo el material base. Complétela con los prompts propios de cada integrante y, sobre todo, con la columna **"Corrección del equipo"** — que es la que demuestra criterio y la que el docente puede contrastar en la sustentación. Un prompt sin corrección registrada sugiere que la salida se aceptó sin revisar, y la sustentación es eliminatoria.

---

## P-01 · Encuadre inicial

**Prompt:** *"Ayúdame con este parcial"* (adjuntando `parcial12026-2-EN.docx`)

**Qué produjo:** El asistente leyó el enunciado completo y, en vez de empezar a escribir, planteó dos preguntas que cambiaban materialmente el trabajo: (a) qué alcance se necesitaba —entrega parcial (5.1–5.2), diseño (5.1–5.5) o todo (5.1–5.7)— y (b) si ya se había recibido la restricción confidencial de la sección 3.

**Refinamiento / decisión del equipo:** Se pidió el alcance completo (5.1–5.7) y se indicó que la restricción del punto 3 **aún no había sido asignada**.

**Aprendizaje:** La segunda pregunta era la importante. Sin ella, el asistente habría escrito un documento que después habría que rehacer para acomodar la restricción. La respuesta llevó a que el documento incluyera el **punto de extensión PE-0**, que deja preparados los tres lugares donde la restricción debe entrar (supuestos, requisitos, decisiones) y muestra con ejemplos cómo aterrizaría según su tipo.

---

## P-02 · Traducción del enunciado

**Prompt:** *"Traduce todos los puntos"*

**Qué produjo:** [`00-enunciado-traducido.md`](00-enunciado-traducido.md) — traducción íntegra al español conservando la numeración original, incluidas las tablas de preguntas guía y la rúbrica completa.

**Por qué importó:** Tener la rúbrica en español y a la vista permitió verificar, sección por sección, que cada criterio de evaluación estuviera cubierto explícitamente. El documento final se estructuró **contra la rúbrica**, no contra el orden de lectura del enunciado.

---

## P-03 · Supuestos y requisitos (5.1 y 5.2)

**Intención del prompt:** Producir requisitos que resolvieran las preguntas guía del enunciado en vez de enumerar funcionalidades genéricas.

**Qué produjo:** 15 supuestos declarados, 30 RF y 19 RNF, seis riesgos de negocio nombrados.

**Refinamientos aplicados en el camino:**

| Problema detectado | Corrección |
|---|---|
| Los primeros RNF decían *"búsqueda rápida"* sin número. | Se exigió **métrica + umbral + método de verificación + riesgo de negocio** en cada uno. La rúbrica es explícita: sin métrica verificable, el criterio es "Insuficiente". |
| Los supuestos eran una lista plana. | Se agregó la columna **"Qué se rompe si cambia"**. Es lo que convierte un supuesto en algo sustentable: en la defensa oral el docente puede variar el escenario, y esa columna es literalmente la respuesta preparada. |
| La disponibilidad se propuso en 99,99% sin más. | Se corrigió a **99,9%**, con la tabla de costo y complejidad y el argumento de que 99,99% exige multi-región activa, lo cual **entra en conflicto directo con RNF-C1** (no-sobreventa). Priorizar corrección sobre disponibilidad es una decisión defendible; elegir el número más alto porque suena mejor, no. |

---

## P-04 · Modelo entidad-relación (5.3)

**Intención del prompt:** Resolver las cinco preguntas guía del enunciado, sin asumir que la lista de entidades sugerida fuera correcta ni completa.

**Correcciones que hubo que hacer sobre la primera propuesta:**

| Problema detectado | Corrección |
|---|---|
| El primer modelo tenía una sola entidad `flight`. | Se partió en `scheduled_flight` (plantilla recurrente) e `flight_instance` (fecha concreta) — **DEC-1**. Sin esa separación no hay dónde representar que el 14 de junio el A320 se cambió por un ATR-72, que es justamente lo que cambia la capacidad. |
| Relación directa `reservation N—N flight_instance`. | Se introdujo `itinerary` como entidad propia — **DEC-2**. La tabla puente plana no puede responder si dos tramos son una conexión o dos vuelos independientes. |
| El inventario se controlaba marcando sillas individuales. | Se cambió a **conteo por cabina** en `flight_inventory` — **DEC-3**. Con sillas individuales, dos compradores del mismo vuelo tocan filas distintas: nadie se bloquea, pero **nadie controla el total**. Fue la corrección de mayor impacto de todo el ejercicio. |
| `v_inventory_reconciliation` contaba tramos. | Debe contar **sillas** (tramos × pasajeros que ocupan silla). Error detectado al revisar la vista contra el caso de una reserva de 3 pasajeros. |

---

## P-05 · Arquitectura y concurrencia (5.4 y 5.5)

**Intención del prompt:** Que cada elección tecnológica quedara conectada a un RNF específico, en particular la de concurrencia sobre el inventario.

**Refinamiento clave:** la primera respuesta proponía bloqueo **optimista** con reintentos, argumentando que es el patrón más habitual. Se corrigió tras analizar el escenario real: con 40 solicitudes por 1 silla, el optimista produce **39 fallos que reintentan y vuelven a chocar** — una tormenta de reintentos exactamente en el momento de mayor valor comercial. El optimista es óptimo cuando los conflictos son raros; aquí **el conflicto es el caso de uso**. Se documentaron las tres alternativas (optimista, cola, pesimista) con su análisis para poder sustentar por qué se descartaron dos, no solo por qué se eligió una.

**Segundo refinamiento:** se añadió el requisito de **orden canónico de bloqueo** (`ORDER BY flight_instance_id`) tras notar que dos itinerarios con tramos en sentido inverso producirían un abrazo mortal. No estaba en la primera versión del protocolo.

---

## P-06 · Implementación del backend (5.6)

**Intención del prompt:** Una API FastAPI que reflejara el diseño y una prueba de concurrencia que constituyera evidencia real, no decorativa.

**Correcciones sobre la primera versión de la prueba:**

| Problema | Por qué invalidaba la prueba | Corrección |
|---|---|---|
| Usaba `asyncio.gather`. | Corrutinas cooperativas: el paralelismo lo serializa el propio bucle de eventos. La prueba habría pasado incluso sin ningún bloqueo. | Hilos reales del sistema operativo. |
| Lanzaba las solicitudes en un bucle. | La primera termina antes de que salga la segunda: **no hay contención que medir**. | `threading.Barrier` para que todas salgan en la misma ventana de microsegundos. |
| Un solo worker de uvicorn. | La aplicación podría serializar por sí sola y ocultar un fallo de la capa de datos. | 4 procesos uvicorn. |
| Solo verificaba códigos HTTP. | "La API respondió bien" ≠ "la base quedó consistente". | Aserción directa contra `v_oversell_check` en la base. |

**Ajustes de diseño que la implementación reveló** — documentados como desviaciones D-1 a D-5 en [`04-implementacion-backend.md`](04-implementacion-backend.md). El más relevante: **el precio hay que congelarlo al retener, no al emitir**, porque entre ambos momentos hay 20 minutos en los que una tarifa puede cambiar.

---

## P-07 · AWS, ETL y costos (5.7)

**Intención del prompt:** Elegir servicios justificándolos contra requisitos y pilares Well-Architected, con costos de supuestos declarados.

**Correcciones aplicadas:**

| Problema detectado | Corrección |
|---|---|
| Se propuso Glue **Spark** por defecto. | Se cambió a **Python Shell (0,0625 DPU)**. Spark cobra un mínimo de 2 DPU: ~16× más caro y más lento por el arranque del cluster, para mover decenas de miles de filas que caben en memoria. |
| La justificación de servicios citaba pilares de forma decorativa. | Se exigió que cada fila nombrara **un RF/RNF concreto** y explicara *por qué* ese pilar, no solo que lo mencionara. |
| Las desviaciones del Learner Lab (Single-AZ, `LabRole`, sin réplica de lectura) no aparecían. | Se agregaron como **A-1 a A-4**, declaradas. El enunciado pide explícitamente declarar desviaciones en lugar de omitirlas; presentar `LabRole` como diseño intencional habría sido deshonesto. |
| La proyección ×10 mezclaba volumen y frecuencia. | Se separaron en dos ejes. El hallazgo relevante solo aparece al separarlos: **×10 volumen ⇒ ×2,8 costo, pero ×10 frecuencia ⇒ +2%**. El cómputo del ETL no es el costo; las bases encendidas sí. |

---

## P-08 · Verificación

**Intención del prompt:** Ejecutar de verdad lo construido, en vez de asumir que funciona.

**Qué ocurrió:** se levantó el stack con Docker Compose, se ejecutó el *seed*, se corrió la suite de pruebas y el script de estrés de concurrencia. La evidencia generada está en [`evidencia/`](evidencia/).

---

## Balance del uso de IA

**Dónde aportó más:** velocidad de redacción, exhaustividad de las tablas de requisitos, y sobre todo **el análisis comparativo de alternativas** — tener las tres opciones de concurrencia escritas con sus contras hizo posible elegir con criterio en vez de por costumbre.

**Dónde hubo que corregirla:**
1. Propone el patrón **más común**, no el más adecuado al escenario (bloqueo optimista, Glue Spark). Ambos habrían "funcionado" en la demostración y habrían sido malas decisiones bajo el escenario real.
2. Tiende a **métricas sin número** si no se le exige explícitamente el umbral y el método de medición.
3. Escribe **pruebas que pasan** antes que pruebas que prueban: la primera prueba de concurrencia habría dado verde sin ningún mecanismo de bloqueo.
4. Omite las **desviaciones incómodas** (Single-AZ, `LabRole`) si no se le pide expresamente declararlas.

**Consecuencia para la sustentación:** las decisiones sustentables de este trabajo son las que sobrevivieron a una corrección, y están documentadas con la alternativa que se descartó y el motivo. Los cuatro puntos anteriores son los que conviene tener presentes: son exactamente donde el docente puede preguntar *"¿por qué no la otra opción?"*.
