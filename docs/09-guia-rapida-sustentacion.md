# Guía rápida de sustentación

> Material de repaso para la defensa oral. Dos partes:
>
> **Parte 1** — las **30 preguntas guía** del enunciado, respondidas en una línea cada una. La versión larga está en el §3 del documento final; esta es la que se puede recordar de pie frente al profesor.
>
> **Parte 2** — **qué hace cada componente de AWS** en esta implementación concreta. No definiciones de manual: para qué se usó aquí.

---

# Parte 1 — Las 30 preguntas guía, en una línea

El enunciado plantea 30 preguntas guía: 5 en 5.1, 6 en 5.2, 5 en 5.3, 5 en 5.4, 4 en 5.5 y 5 en 5.7.

## 5.1 — Requisitos funcionales

| # | Pregunta | Respuesta |
|---|---|---|
| 1 | ¿Quiénes son los actores? ¿Mismo sistema o interfaces distintas? | Siete actores, cuatro superficies de entrada, **un solo backend y un solo dominio** — si cada canal tuviera su lógica de inventario, la garantía de no-sobreventa habría que probarla cuatro veces |
| 2 | ¿Reserva multi-tramo: una reserva o varias encadenadas? | **Un PNR** con `itinerary` como entidad intermedia: el pasajero compró **un viaje**, no tres vuelos |
| 3 | ¿Se permite reservar sin pagar? | Sí, 20 minutos en `HELD`. El pago no es atómico con la reserva, **pero la retención sí lo es con la verificación de inventario** |
| 4 | ¿Qué pasa si cancela o cambia la fecha? | Ambas operan sobre el itinerario. Cancelar libera inventario de inmediato; cambiar es cancelar + reemitir en el mismo PNR. **Nada se borra** |
| 5 | ¿Las agencias tienen flujo diferente? | Mismo flujo de dominio; distinto contrato, tarifa y medio de pago. **Lo único que cambia de verdad es el paso de pago**: cupo de crédito en vez de tarjeta |

## 5.2 — Requisitos no funcionales

| # | Pregunta | Respuesta |
|---|---|---|
| 6 | ¿Qué tiempo de respuesta? ¿Cambia con escalas? | p95 ≤ 400 ms directos, ≤ 1.200 ms con escalas. Cambia porque directo es una consulta indexada y con escalas es **búsqueda de caminos en un grafo** con validación de conexión en cada nodo |
| 7 | ¿Dos pasajeros por la última silla? | Uno gana de forma determinista y el otro recibe rechazo inmediato y claro. **Nunca dos confirmaciones**, y se prefiere rechazar rápido antes que confirmar y revertir: revertir una venta tiene costo regulatorio, un rechazo no |
| 8 | ¿Qué número traduce "no se puede caer"? | **99,9%** (43,2 min/mes), 99,95% en temporada alta. No 99,99% porque exige multi-región activa **y una base escribible en dos regiones no puede serializar el inventario barato**. Multi-AZ cuesta +28,58 USD/mes |
| 9 | ¿Cómo se protegen los datos? ¿Qué regulación? | Tres marcos: **PCI-DSS** (reducido a SAQ-A al delegar el pago), **Ley 1581** de Habeas Data y **RGPD**. El sistema nunca ve el número de tarjeta |
| 10 | Con 20 rutas nuevas, ¿primer cuello de botella? | No el almacenamiento: **la explosión combinatoria de la búsqueda con escalas**, que crece con los pares de rutas que comparten aeropuerto — aproximadamente cuadrático |
| 11 | ¿Qué tan importante es el historial? | Crítico por tres razones distintas: comercial (disputas), financiera (comisiones) y **analítica** — los patrones de cancelación no son calculables sin él |

## 5.3 — Modelo entidad-relación

| # | Pregunta | Respuesta |
|---|---|---|
| 12 | ¿Itinerario: entidad propia o relación directa? | **Entidad propia.** El itinerario es la unidad de **cancelación y tarifación**; el tramo es la unidad de **inventario**. Granos distintos, entidades distintas |
| 13 | ¿La silla es parte de la reserva o del check-in? | Del check-in. Y no es un detalle de conveniencia: **desacoplar la silla de la venta es lo que hace barato el control de capacidad** |
| 14 | ¿Cómo se representa un vuelo que se repite? | Dos entidades: `scheduled_flight` (plantilla) y `flight_instance` (fecha concreta). **La capacidad no es del programado, es de la instancia** |
| 15 | ¿Dónde vive el control de inventario? | En `flight_inventory`, una fila por vuelo × cabina. Ahí y solo ahí. **Es la fila que se bloquea** |
| 16 | ¿Qué normalización se justifica? | **3FN estricta con tres excepciones nombradas**; el analítico es otro modelo (estrella) y se construye en el ETL |

## 5.4 — Arquitectura del backend

| # | Pregunta | Respuesta |
|---|---|---|
| 17 | ¿Monolito o servicios separados? | **Monolito modular, y el RNF que decide es el de concurrencia**: con una base única, retener N tramos es una transacción ACID; con servicios sería una saga con compensaciones, y durante la compensación el sistema está temporalmente sobrevendido |
| 18 | ¿Cómo se evita la sobreventa concurrente? | **Bloqueo pesimista** `SELECT … FOR UPDATE` en **orden canónico** (hace imposible el abrazo mortal), `lock_timeout` de 3 s, y una restricción `CHECK` en la base como última línea de defensa |
| 19 | ¿Cómo se integra el pago? | **Se delega el instrumento, se conserva la máquina de estados de la venta.** El proveedor no sabe nada de inventario ni de retenciones |
| 20 | ¿Misma API o API B2B separada? | **Dos superficies, un dominio.** Separadas porque tienen ciclos de vida distintos; dominio compartido para no duplicar la regla de no-sobreventa |
| 21 | ¿Relación con el pipeline de Big Data? | **Regla dura: el pipeline nunca consulta el primario.** Lee de la réplica, en ventana nocturna, con extracción incremental por marca de agua |

## 5.5 — Arquitectura del frontend

| # | Pregunta | Respuesta |
|---|---|---|
| 22 | ¿SPA o renderizado en servidor? | **Híbrido**: servidor para descubrimiento (el SEO es adquisición gratuita), cliente para el flujo de compra. Lo que importa comercialmente es **el tiempo hasta el primer resultado visible**, no el de la API |
| 23 | ¿Cómo se notifica la pérdida de disponibilidad? | **Tres capas** de menor a mayor costo, y la clave es que **el que ya retuvo no puede perder la silla**: la mejor forma de no dar una mala noticia es que no ocurra |
| 24 | ¿Interfaz separada para agentes? | **Sí.** El agente no es un pasajero con permisos extra, **es otro oficio**: vende 40 tiquetes al día para terceros |
| 25 | ¿Qué implica multi-moneda y multi-idioma? | Moneda **fija durante la reserva** (el frontend nunca convierte), rutas con prefijo por idioma (indexables), y horarios **siempre en hora local del aeropuerto** — mostrar la del usuario hace perder vuelos |

## 5.7 — AWS, ETL y costos

| # | Pregunta | Respuesta |
|---|---|---|
| 26 | ¿Qué copiar a la analítica? ¿Tal cual o transformado? | **Transformado.** Tres hechos, uno por cada pregunta de negocio del enunciado. Copiar tal cual reproduciría el problema del OLTP: seis `JOIN` por consulta |
| 27 | ¿El esquema analítico debe ser idéntico? | **No.** 3FN para escribir con integridad, estrella para leer agregando. **El ETL es la traducción entre ambos mundos** |
| 28 | ¿Qué servicio implementa el ETL? | **Glue Python Shell a 0,0625 DPU.** Descartados: Spark (mínimo 2 DPU, ~16× más caro), Lambda (límite duro de 15 min) y DMS (replica pero no transforma) |
| 29 | ¿Cómo se conectan las bases al catálogo? | Conexión JDBC + rol IAM + red + crawler. **Lo que más falla es el security group**: Glue exige auto-referencia en **todos** los puertos, no solo en el 5432 |
| 30 | ¿Una vez, periódico o por eventos? | **Diario a las 03:00, incremental.** Y el matiz que vale: **la restricción no es económica** — subir a cada 2 horas cuesta menos de 1 USD/mes; es de valor de negocio |

> **Las cinco que conviene practicar en voz alta: 7, 8, 15, 17 y 18.** Son el núcleo de la concurrencia, que es donde la rúbrica pone el peso y donde el docente va a insistir. Si esas cinco están sólidas, el resto se sostiene solo.

---

# Parte 2 — Qué hace cada componente de AWS

La clave para entender esta arquitectura —y probablemente la pregunta que más distinga a quien entendió de quien memorizó— es que **hay dos circuitos independientes**.

## Circuito A — el que mueve *datos*

| Componente | Qué es | Para qué se usó aquí |
|---|---|---|
| **`airline-oltp`** | RDS PostgreSQL, `db.t3.small` | La base **transaccional**: esquema `airline` con las 26 tablas. Es la que atiende a la API, y donde ocurre el `SELECT … FOR UPDATE` sobre `flight_inventory`. Es `small` y no `micro` porque soporta la carga de escritura concurrente |
| **`airline-olap`** | RDS PostgreSQL, `db.t3.micro` | La base **analítica**: esquema `analytics` con el modelo estrella (5 dimensiones, 3 hechos, 3 vistas de negocio). Es `micro` y Single-AZ a propósito: **si se cae no se detiene ninguna venta**, y el dato se reconstruye reejecutando el ETL |
| **`airline-etl-oltp-to-olap`** | Glue Job, Python Shell, 0,0625 DPU | **El ETL propiamente dicho.** Lee de la transaccional, transforma a estrella y escribe en la analítica. Es lo único que mueve filas. Corrió en 112 s y cargó 157 tiquetes, 1.342 filas de ocupación y 122 de ciclo de vida |
| **`airline-etl-nightly`** | Glue Trigger, `cron(0 8 * * ? *)` | El **cron**: 03:00 hora de Colombia. Hace que el pipeline no dependa de que alguien se acuerde de lanzarlo |

## Circuito B — el que mueve *metadatos*

| Componente | Qué es | Para qué se usó aquí |
|---|---|---|
| **`airline-oltp-crawler`** · **`airline-olap-crawler`** | Glue Crawlers | Se conectan a cada base, **leen la estructura** (tablas, columnas, tipos) y la registran en el catálogo. **No mueven ni una sola fila de datos** |
| **`airline_oltp_catalog`** · **`airline_olap_catalog`** | Glue Data Catalog | El **inventario de metadatos**: 30 objetos del OLTP y 13 del OLAP. **Es el requisito explícito del enunciado**: que ambas bases queden registradas y sus tablas sean visibles como metadatos |

> **El punto que hay que tener clarísimo:** el ETL **no lee del catálogo**. Los dos circuitos son independientes. Se podría borrar el catálogo entero y el ETL seguiría funcionando; se podría no correr nunca el job y el catálogo seguiría poblado. **Son dos requisitos distintos del enunciado que casualmente usan el mismo servicio.** Confundirlos es el error típico.

## Las piezas de apoyo, que sirven a ambos circuitos

| Componente | Para qué se usó aquí |
|---|---|
| **Glue Connections** (`airline-oltp-conn`, `airline-olap-conn`) | Guardan endpoint, usuario y contraseña de cada base. **Las usan los dos circuitos pero para cosas distintas**: el crawler para leer metadatos, el job para leer y escribir datos. Y tienen un tercer efecto no obvio: **adjuntar una conexión a un job lo obliga a correr dentro de la VPC** — por eso el job no tenía salida a internet |
| **S3** (`airline-glue-scripts-…`) | Glue exige que el script del job viva en S3; no se puede pegar el código en el job. Guarda `scripts/` con el ETL y `wheels/` con el driver de PostgreSQL precompilado, que fue la solución al problema de la VPC sin salida |
| **Security Group** (`airline-db-sg`) | Hace dos trabajos a la vez: **aísla** las bases (solo aceptan conexiones desde dentro del grupo, nunca desde internet) y **permite que Glue se hable consigo mismo**, porque sus tarjetas de red viven en ese mismo grupo |
| **Rol IAM `LabRole`** | La identidad que Glue asume para leer S3, escribir en el catálogo y crear tarjetas de red. En producción sería un rol por componente con privilegio mínimo; el Learner Lab no permite crear roles, y queda declarado como desviación |
| **CloudWatch Logs** | `/aws-glue/python-jobs/output` y `/error` para el job, `/aws-glue/crawlers` para los crawlers. **Sin esto habría sido imposible diagnosticar los seis fallos del despliegue**: el mensaje de que `pip` no alcanzaba PyPI salió de ahí |

## Costo de cada pieza

Casi todo el gasto está en las bases encendidas, no en el cómputo del ETL:

| Componente | USD/mes (escenario base) | % del total |
|---|---|---|
| RDS OLTP (instancia + almacenamiento) | 28,58 | 63% |
| RDS OLAP (instancia + almacenamiento) | 15,44 | 34% |
| Glue: job + crawlers + catálogo | 1,32 | **2,9%** |
| S3 + CloudWatch | 0,31 | 0,7% |
| **Total** | **≈ 45,65** | |

> **La conclusión contraintuitiva, y la más defendible:** multiplicar la **frecuencia** del ETL por 10 sube la factura apenas un **2%**, porque el costo está dominado por capacidad encendida, no por consumo. Si el negocio pidiera datos más frescos, se puede pasar de diario a cada dos horas por menos de un dólar al mes. **La decisión de correr el ETL una vez al día no es económica: es de valor de negocio.** Confundir ambas cosas llevaría a negar una mejora que es casi gratis.
