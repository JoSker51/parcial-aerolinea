# Arquitectura e implementación

> Complemento de los documentos de requisitos y de modelo de datos. Aquí está **todo lo que no es la base de datos**: cómo está construido el backend, cómo el frontend, qué se implementó de verdad y cómo se probó, y cómo quedó desplegado en AWS.
>
> Igual que en el resto del proyecto, **cada decisión viene con la alternativa que se descartó y el motivo**.

---

# 1. Arquitectura del backend

## 1.1 Monolito modular, no microservicios

**Decisión: un solo despliegue con cuatro módulos de frontera explícita** — `search`, `booking` (que contiene inventario), `payments` y `catalog` — sobre una base PostgreSQL única, más réplicas de lectura para la búsqueda.

**La alternativa descartada** eran servicios separados con bases propias. El razonamiento que decide, y que hay que sostener en la defensa:

> Retener un itinerario de varios tramos exige verificar y actualizar **N filas de inventario de forma atómica**. Con una base única, eso es **una transacción** y el problema lo resuelve el motor. Con servicios separados, la misma garantía exigiría una **saga con compensaciones**: retener el tramo 1, retener el 2, fallar en el 3, y compensar los dos primeros. **Durante la ventana de compensación el sistema está temporalmente inconsistente — es decir, temporalmente sobrevendido.** Se puede construir, pero se estaría pagando complejidad distribuida para *debilitar* la garantía que el enunciado señala como innegociable.

Complementos de la decisión:

| Aspecto | Decisión | Por qué |
|---|---|---|
| **Separación de lectura** | La búsqueda consulta **réplicas de lectura**; la reserva va siempre al primario | La búsqueda es el 98,7% del tráfico y tolera unos segundos de desfase: **mostrar disponibilidad ligeramente vieja es aceptable porque la verdad se verifica al retener**, no al buscar |
| **Sin estado en la aplicación** | La sesión vive en el cliente y la caché en Redis, nunca en memoria del proceso | Es lo que habilita el escalado horizontal |
| **Fronteras internas reales** | Cada módulo expone una interfaz; nada de consultas cruzadas a las tablas de otro módulo | Permite extraer un módulo como servicio propio **cuando haya evidencia de que hace falta**, sin reescribir el dominio |
| **Cuándo dividir** | Umbral declarado: más de 8 instancias en pico sostenido de búsqueda, o equipo de más de ~15 personas | **Tener el número escrito de antemano evita la discusión ideológica** monolito-contra-microservicios |

**Lo que se paga:** escalado menos granular (escala toda la aplicación aunque solo la búsqueda lo necesite) y un despliegue acoplado. A esta escala, ambos costos son menores que operar una saga distribuida en la ruta crítica.

---

## 1.2 El control de sobreventa: por qué bloqueo pesimista

Las tres alternativas se evaluaron contra el escenario real de contención: **40 solicitudes concurrentes por la última silla**.

| Mecanismo | Cómo se comporta bajo alta contención | Veredicto |
|---|---|---|
| **Optimista con reintentos** | Lee la versión, calcula, escribe condicionando por ella. Con 40 solicitudes por 1 silla, **39 fallan y reintentan; al reintentar vuelven a chocar**. Tormenta de reintentos justo en el momento de mayor valor comercial, con latencia impredecible | ✗ Es óptimo cuando los conflictos son **raros**. Aquí **el conflicto es el caso de uso** |
| **Cola serializadora por vuelo** | Serialización perfecta y sin bloqueos. Pero la reserva pasa a ser **asíncrona**: el usuario recibe "procesando" y hay que sondear o notificar. Añade broker, consumidores, orden y reintentos | ✗ Sobre-ingeniería a esta escala. Es la respuesta correcta a 10.000 solicitudes/s por vuelo, no a 40 |
| **Pesimista con bloqueo de fila** ✅ | Las 40 se serializan sobre **una sola fila corta**, con transacción de ~5 ms. La primera gana; las 39 siguientes leen el estado ya actualizado y reciben un rechazo limpio. **Determinista, síncrono y sin reintentos** | ✅ **Elegido** |

**Las tres condiciones que lo hacen correcto aquí** —y que en otro contexto darían otra respuesta—: la contención está concentrada en **una sola fila corta**, la transacción es **breve y no incluye ninguna llamada externa**, y el requisito prefiere **rechazar rápido a confirmar y revertir**.

### El protocolo de la transacción crítica

```
BEGIN;
  1. Bloquear TODAS las filas de inventario, en ORDEN CANÓNICO
  2. Verificar disponibilidad de TODOS los tramos antes de escribir nada
  3. Incrementar las sillas retenidas
  4. Crear la reserva en estado HELD con su vencimiento
  5. Crear itinerarios, tramos y pasajeros
  6. Registrar el evento de auditoría
COMMIT;
```

Tres detalles que marcan la diferencia:

1. **Orden canónico de bloqueo.** Las filas se bloquean siempre ordenadas por identificador de vuelo. Sin ese orden, dos itinerarios que comparten tramos en sentido inverso (A→B→C y C→B→A) se bloquearían mutuamente. **Con orden fijo el abrazo mortal es imposible por construcción**, no improbable.
2. **Ninguna llamada externa dentro de la transacción.** Si el proveedor de pagos tardara 3 segundos con la fila bloqueada, se bloquearía el vuelo entero durante 3 segundos. Por eso el pago vive fuera.
3. **Tiempo máximo de espera explícito (3 s).** Antes que dejar crecer una espera sin límite, se falla rápido con un mensaje claro. **Un fallo limpio es mejor que una degradación silenciosa.**

---

## 1.3 El flujo de pago: delegado, con máquina de estados propia

**Decisión: se delega el *manejo del instrumento de pago*, pero el sistema conserva la *máquina de estados de la venta*.** No es lo mismo, y confundirlo es el error común.

| Qué se delega | Qué se conserva |
|---|---|
| Captura y almacenamiento del número de tarjeta, autenticación 3-D Secure, antifraude del instrumento, tokenización, cumplimiento PCI del dato | Estado de la reserva, retención de inventario, reglas de reembolso, **idempotencia**, conciliación diaria y **la decisión final de confirmar** |

**Por qué así:** delegar el instrumento reduce el alcance de cumplimiento PCI al nivel más bajo — la decisión de seguridad de mayor impacto del proyecto. Pero **no se puede delegar la máquina de estados**: el proveedor no sabe nada de inventario ni de retenciones.

**Casos límite resueltos explícitamente:**

| Caso | Comportamiento |
|---|---|
| Webhook duplicado | La unicidad del identificador de evento lo descarta. Reenviar el mismo evento diez veces produce exactamente una confirmación |
| **Webhook tras expirar la retención** | Se reintenta retener. Si hay cupo, confirma. **Si el vuelo ya se llenó, reembolso automático y notificación** |
| Pago exitoso pero el webhook nunca llega | Proceso de conciliación cada 15 min contra la API del proveedor, sobre reservas vencidas con intento de pago |
| El usuario paga dos veces | El segundo intento se rechaza por la clave de idempotencia de la reserva |
| Venta por agencia | No hay proveedor externo: se debita el cupo de crédito **dentro de la misma transacción** que la retención, y la reserva nace confirmada. **Es la única diferencia real de flujo entre canales** |

---

## 1.4 Dos superficies de API, un solo dominio

|  | API pública | API B2B |
|---|---|---|
| Autenticación | Sesión de usuario o invitado | Credenciales de cliente por agencia + usuario nominal |
| Tarifas | Públicas | **Netas**, con descuento contractual |
| Pago | Proveedor externo con tarjeta | **Cupo de crédito** |
| Límite de tasa | Por IP | **Cuota por agencia** |
| Versionado | Cambia con el sitio web | **Contrato estable** |
| Datos visibles | Solo la reserva del propio usuario | Solo las reservas de **esa** agencia |

**Por qué separadas:** son contratos con ciclos de vida distintos. La web pública se puede cambiar el martes; un integrador B2B necesita meses de aviso. Mezclarlas obligaría a **congelar la evolución de la web al ritmo del socio más lento**.

**Por qué el dominio es compartido:** si el canal B2B tuviera su propia lógica de inventario, la garantía de no-sobreventa habría que probarla dos veces y se rompería la primera vez que los canales divergieran. **Flujo de dominio idéntico, contrato y condiciones comerciales distintos.**

---

## 1.5 Rendimiento, disponibilidad y auditoría

| Capa | Mecanismo | Por qué |
|---|---|---|
| Catálogo (aeropuertos, rutas) | Caché con vigencia de 24 h | Datos casi estáticos |
| Resultados de búsqueda | Caché por origen-destino-fecha-cabina, **60 s** | El desfase solo puede producir un **falso positivo** de disponibilidad, que se corrige al retener |
| Reserva | **Solo primario, sin caché jamás** | Cachear la decisión de retener sí violaría la garantía de no-sobreventa: está prohibido explícitamente |
| Base de datos | Multi-AZ con conmutación automática | Es la implementación literal del objetivo de disponibilidad |
| Aplicación | ≥ 2 instancias en zonas distintas tras balanceador | |
| Respaldos | Automáticos + recuperación a un punto en el tiempo | Cubre el objetivo de pérdida máxima de 5 minutos |
| Degradación | **Si el proveedor de pagos cae, la búsqueda y la retención siguen operando**; solo se bloquea la confirmación | Degradar una función, no el sistema |
| Auditoría | Cada transición escribe en el log **dentro de la misma transacción** | Si fuera asíncrona, un fallo entre la operación y su registro dejaría una transición sin rastro |

> **Por qué la caché de búsqueda dura 60 segundos y no más:** el desfase solo puede mostrar un vuelo que acaba de llenarse — molesto pero recuperable, porque el usuario recibe un error claro y alternativas. Lo inverso, cachear la decisión de retener, produciría sobreventa real. **Por eso una está permitida y la otra prohibida.**

---

# 2. Arquitectura del frontend

> Se diseñó pero **no se implementó**: el enunciado exige la API en 5.6, no la interfaz. Queda declarado como fuera de alcance.

## 2.1 Híbrido: servidor para descubrir, cliente para comprar

| Superficie | Modo | Por qué |
|---|---|---|
| Inicio, páginas de ruta, contenido | **Renderizado en servidor** | SEO: estas páginas capturan tráfico de búsqueda orgánica, que es **adquisición gratuita**. Una aplicación cliente pura las hace invisibles |
| Resultados de búsqueda | **Servidor en la primera carga + hidratación** | El primer resultado debe verse rápido; filtros y orden ya son cliente, sin ida y vuelta |
| Selección → pasajeros → sillas → pago | **Cliente** | Flujo con mucho estado y **un reloj de retención corriendo**. Recargar en cada paso rompería la experiencia justo antes de pagar |
| Gestión de reserva y check-in | **Cliente** | Aplicación autenticada, sin valor SEO |

**El argumento que decide:** el tiempo que importa comercialmente no es el de la API, es **el tiempo hasta que el usuario ve el primer resultado**. Con una aplicación cliente pura hay tres saltos en serie. Elegir un solo modo optimiza la mitad del recorrido.

## 2.2 Avisar que la silla ya no está: tres capas, no websockets para todo

| Capa | Mecanismo | Qué cubre |
|---|---|---|
| **1. Reloj de retención** | Cuenta regresiva alimentada por el vencimiento **que dicta el servidor**, no el reloj del navegador | El caso mayoritario: **el usuario ya tiene el inventario retenido, así que no puede perderlo**. La mejor forma de no dar una mala noticia es que no ocurra |
| **2. Revalidación antes de retener** | Al pulsar "continuar" se revalida contra el servidor | Cubre el desfase de la caché. **Falla temprano**, cuando el usuario aún no invirtió esfuerzo |
| **3. Eventos del servidor en el mapa de sillas** | Solo mientras el mapa está abierto; marca en gris las sillas que se ocupan en vivo | La única pantalla donde hay competencia visible por un recurso concreto |

**Y cuando el rechazo ocurre igual:** la interfaz **no muestra un error genérico**. Recarga la disponibilidad de ese tramo y propone alternativas (siguiente vuelo, otra cabina, otra fecha) conservando el resto del itinerario. **Un rechazo bien manejado recupera la venta; un error en rojo la pierde.**

## 2.3 Consola B2B separada, e internacionalización

**Aplicación separada para agencias.** No se reutiliza la web pública con un rol distinto porque **el agente no es un pasajero con permisos extra, es otro oficio**: vende 40 tiquetes al día para clientes que no son él, necesita comparación lado a lado, cupo de crédito visible, liquidación de comisiones y atajos de teclado. Meter eso en la web pública tras condicionales de rol produce una interfaz peor para ambos y **una superficie de riesgo mayor**.

| Dimensión | Decisión | Implicación real |
|---|---|---|
| **Idioma** | Rutas con prefijo, no detección por navegador | Cada idioma es una URL indexable. **La detección automática rompe el SEO** y confunde a quien comparte un enlace |
| **Moneda** | **Se fija al inicio de la reserva y no cambia** | Impide el "precio que se mueve": el frontend **nunca** convierte |
| **Formato** | Formateadores nativos con la configuración regional | El separador decimal cambia entre monedas; **un formateo manual produciría errores de tres órdenes de magnitud** |
| **Zonas horarias** | Los horarios se muestran **siempre en hora local del aeropuerto** | Es convención del sector, no capricho: **mostrar la hora local del usuario hace perder vuelos** |
| **Contenido de negocio** | Los nombres de aeropuertos se traducen **en la base de datos** | Un catálogo no puede vivir en archivos de traducción del cliente: cambiaría con cada despliegue en vez de con cada edición |

---

# 3. La implementación real del backend

## 3.1 Qué se construyó

**Stack:** PostgreSQL 16 · FastAPI · SQLAlchemy · 4 procesos de servidor · Docker Compose.

| Operación | Endpoint | Estado |
|---|---|---|
| Buscar vuelos | `GET /api/v1/flights/search` | ✅ Directos y con escala, con tiempo mínimo de conexión por aeropuerto |
| **Crear reserva** | `POST /api/v1/reservations` | ✅ Multi-tramo, multi-pasajero, **con bloqueo pesimista** |
| Consultar reserva | `GET /api/v1/reservations/{pnr}` | ✅ Con control de acceso por PNR + apellido |
| Confirmar pago | `POST /api/v1/reservations/{pnr}/confirm` | ✅ Idempotente |
| Cancelar | `POST /api/v1/reservations/{pnr}/cancel` | ✅ Libera inventario de inmediato |
| Expirar retenciones | `POST /internal/jobs/expire-holds` | ✅ Escalable horizontalmente |
| Verificar invariante | `GET /internal/oversell-check` | ✅ Aserción en vivo |

**Los tres primeros son exactamente el alcance mínimo exigido.** Los otros cuatro se agregaron porque **una API que solo retiene pero nunca confirma ni cancela no permite verificar que los contadores de inventario cierran** — sin ellos la evidencia sería incompleta.

**Fuera de alcance, declarado:** el frontend, la API B2B como superficie separada (el descuento, la comisión y el cupo **sí** están implementados en el dominio), check-in y asignación de sillas, compra de auxiliares, notificaciones y la materialización programada.

## 3.2 Cómo se probó la concurrencia — y por qué la prueba es honesta

Se probó de **dos formas independientes**, porque una sola no bastaría: una prueba que solo mira códigos HTTP **podría pasar con una base sobrevendida**.

| Decisión de la prueba | Qué error evita |
|---|---|
| **Hilos reales del sistema operativo**, no corrutinas | El paralelismo cooperativo serializaría las solicitudes por sí solo: **la prueba pasaría sin ningún mecanismo de bloqueo** |
| **Barrera de sincronización** antes de cada solicitud | Sin ella, la primera terminaría antes de que la segunda empezara: **no habría contención que medir** |
| **HTTP contra la API real, con 4 procesos** | Un solo proceso podría serializar en la aplicación y ocultar un fallo de la capa de datos. Con 4, **la contención está obligada a resolverse en la base** |
| **Aserción contra el estado real de la base**, no contra los códigos de respuesta | Es la diferencia entre *"la API respondió bien"* y *"la base quedó consistente"* |
| **Documento distinto por cada solicitud** | Documentos repetidos introducirían contención ajena a la que se quiere medir |

### Los resultados

| Escenario | Solicitudes | Sillas | Creadas | Rechazos | **Sobreventa** |
|---|---|---|---|---|---|
| El literal del enunciado | 2 | 1 | **1** | **1** | **0** |
| El pico asumido | 40 | 1 | **1** | **39** | **0** |
| Varias sillas libres | 60 | 5 | **5** | **55** | **0** |

En los tres casos la cabina queda **exactamente llena**, la vista de verificación devuelve **cero filas**, y la conciliación entre el contador materializado y los tramos reales cuadra. **Suite completa: 11 de 11 pruebas pasan.**

**Latencias:** con 40 solicitudes, p95 de 139 ms; con 60, p95 de 294 ms. El bloqueo serializa, así que la última en ser atendida espera a todas las anteriores: **la latencia crece linealmente con la cola**. Extrapolando (~5 ms por solicitud encolada), harían falta del orden de **600 solicitudes simultáneas sobre el mismo vuelo y cabina** para agotar el tiempo máximo de espera — quince veces el pico asumido.

> **Eso es una propiedad del diseño, no un defecto.** Es exactamente el compromiso que se aceptó al preferir rechazar rápido antes que confirmar y revertir. Si la aerolínea creciera a niveles de venta de entradas de concierto, la respuesta sería migrar a la cola serializadora ya evaluada — **y el modelo de datos no cambiaría**, porque el punto de contención seguiría siendo la misma fila.

## 3.3 Los siete ajustes que aparecieron al implementar

> El enunciado es explícito: *"un ajuste no es un error: es información valiosa que debe documentarse"*.

| # | Ajuste | Por qué apareció |
|---|---|---|
| **D-1** | **El precio se congela al retener, no al emitir** | Entre la retención y el pago hay 20 minutos, y las tarifas cambian varias veces al día. Sin esto, **el pasajero vería un precio al reservar y se le cobraría otro al pagar** |
| **D-2** | Cotizar **antes** de tomar el bloqueo | Resolver la tarifa exige tres uniones por tramo; dentro del bloqueo alargaba la sección crítica sin necesidad. Es lectura pura y no participa del invariante |
| **D-3** | **Los infantes no consumen inventario** | Al escribir el cálculo hubo que decidir qué cuenta como silla. Un infante viaja en brazos. Se corrigió en los **cuatro** caminos que tocan contadores |
| **D-4** | Las retenciones se expiran **omitiendo las filas bloqueadas** | Con varias instancias del proceso en paralelo se bloqueaban entre sí. Así el proceso es **escalable sin coordinación externa** |
| **D-5** | Errores de dominio con **código estable** y detalle estructurado | La interfaz necesita distinguir "sin disponibilidad" de cualquier otro conflicto **sin analizar cadenas de texto** |
| **D-6** | Tres fallos de tipado de parámetros | **Ninguno es visible leyendo el código**, y los tres estaban en el camino *por defecto* — el de un usuario que no filtra por clase tarifaria |
| **D-7** | Las pruebas necesitaron aislamiento explícito | Y al limpiar, **el borrado falló**: los tiquetes y pagos referencian la reserva sin cascada, y el log de auditoría tiene el bloqueo de inmutabilidad |

> **Sobre D-7, que es el más elocuente:** la prueba chocó contra las garantías del propio diseño **y las garantías ganaron**. Que borrar el historial de auditoría sea difícil incluso desde una prueba propia es la señal de que la inmutabilidad está realmente implementada y no solo declarada.

**Y lo que NO hubo que ajustar**, que es la validación real del diseño: **el mecanismo de concurrencia funcionó exactamente como estaba documentado**, sin sorpresas, sin abrazos mortales y sin reintentos.

---

# 4. La implementación en AWS

## 4.1 Qué quedó desplegado

| Componente | Recurso | Función |
|---|---|---|
| Base transaccional | RDS PostgreSQL `db.t3.small` | Esquema de 26 tablas; atiende a la API |
| Base analítica | RDS PostgreSQL `db.t3.micro`, Single-AZ | Modelo estrella; **si se cae no se detiene ninguna venta** |
| ETL | Glue Job **Python Shell, 0,0625 DPU** | Lee, transforma y carga. Corrió con éxito en **112 segundos** |
| Catálogo | 2 bases del Glue Data Catalog | **30 y 13 objetos registrados** — el requisito explícito del enunciado |
| Crawlers | 2, uno por base | Registran la estructura; **no mueven datos** |
| Programación | Trigger diario a las 03:00 | El pipeline no depende de que alguien lo lance |

**Datos cargados:** 1.095 fechas, 854 vuelos, 12 rutas, 48 tarifas, **157 tiquetes**, **1.342 filas de ocupación** y **122 de ciclo de vida de reservas**.

## 4.2 Las decisiones y sus alternativas

| Decisión | Alternativas descartadas | Por qué |
|---|---|---|
| **Se transforma, no se copia tal cual** | Réplica directa de las tablas | Copiar tal cual reproduciría en la analítica el problema del transaccional: **seis uniones en cada consulta de negocio** |
| **Tres hechos, uno por pregunta de negocio** | Un solo hecho genérico | Ocupación, ingresos y cancelaciones tienen **granos distintos**; forzarlos a uno solo haría imposible responder alguna |
| **El grano es el tiquete** | La reserva | Es el grano **más fino con valor monetario**. Desde ahí se agrega a ruta, tarifa, vuelo o fecha; al revés no se puede |
| **Glue Python Shell** | Spark (mín. 2 DPU, ~16× más caro), Lambda (límite de 15 min), DMS (replica pero no transforma, e instancia siempre encendida) | El volumen cabe holgadamente en un proceso |
| **Diario, incremental** | Una sola vez; cada hora; por eventos | Las preguntas de gerencia son **tácticas, no operativas**: nadie cambia una ruta por lo que pasó hace diez minutos |
| **Extracción por marca de agua** | Escaneo completo cada noche | Sin ella, cada corrida reprocesaría toda la historia: **al año, ~365× más cómputo para el mismo resultado** |

> **El hallazgo más defendible de toda la sección:** el log de auditoría —creado para cumplir un requisito de **cumplimiento**— resultó ser **la única fuente posible de los patrones de cancelación**. Una reserva cancelada que solo guarda su estado final no dice ni cuándo se canceló, ni cuánto vivió. **Un requisito regulatorio terminó habilitando una capacidad analítica.**

## 4.3 Costos, y la conclusión contraintuitiva

| Concepto | USD/mes | % |
|---|---|---|
| RDS transaccional (instancia + almacenamiento) | 28,58 | 63% |
| RDS analítica (instancia + almacenamiento) | 15,44 | 34% |
| Glue: job + crawlers + catálogo | 1,32 | **2,9%** |
| S3 + registros | 0,31 | 0,7% |
| **Total** | **≈ 45,65** | |

**Proyección ×10, separada en dos ejes** porque se comportan de forma muy distinta:

| Eje | Efecto en el costo |
|---|---|
| **Volumen ×10** | ≈ $128/mes — **×2,8**, crecimiento sublineal |
| **Frecuencia ×10** | ≈ $46/mes — **+2%**, prácticamente nada |

> **Las dos conclusiones que hay que llevar a la sustentación:**
>
> **1. El cómputo del ETL no es el costo; las bases encendidas sí.** Glue es el 2,9% del total. La consecuencia práctica es contraintuitiva: **si el negocio pidiera datos más frescos, se puede pasar de diario a cada dos horas por menos de un dólar al mes.** La restricción a diario **no es económica, es de valor de negocio** — confundir ambas cosas llevaría a negar una mejora casi gratis.
>
> **2. El crecimiento es sublineal porque el gasto está dominado por capacidad reservada, no por consumo.** Eso es bueno para el margen y malo para la disciplina: se paga capacidad ociosa el 90% del tiempo. Las palancas, por retorno: instancias reservadas (−30 a −40%), y apagar la analítica fuera de la ventana de uso (−60% de esa instancia, viable justamente porque es reconstruible).

## 4.4 Las desviaciones del entorno, declaradas

| # | Desviación | Motivo | Impacto |
|---|---|---|---|
| **A-1** | Transaccional Single-AZ, no Multi-AZ | El laboratorio no lo habilita | Solo de entorno; el costo del diseño correcto está cuantificado |
| **A-2** | Rol IAM compartido | El laboratorio no permite crear roles | Debilita el principio de mínimo privilegio en el entorno de práctica |
| **A-3** | El ETL lee del primario, no de una réplica | No hay presupuesto para una tercera instancia | Se mitiga corriendo en la ventana de menor tráfico. **En producción la réplica es obligatoria** |
| **A-4** | Contraseñas por parámetro, no en gestor de secretos | El laboratorio lo restringe de forma intermitente | En producción: gestor de secretos con rotación |
| **A-5** | Acceso público **temporal** durante la carga inicial, restringido a una IP y **revertido al terminar** | No hay bastión ni pasarela de salida | Ventana acotada; **el estado final cumple el requisito de aislamiento** |
| **A-6** | El driver se entrega como paquetes en S3, no instalado desde el repositorio público | El job corre dentro de la red privada y **sin pasarela de salida no hay ruta al repositorio** | Ninguno funcional; el job queda **más rápido y determinista** |

## 4.5 Lo que solo apareció al desplegar de verdad

Seis obstáculos que **no son visibles leyendo el diseño** — y que por eso mismo son la mejor evidencia de que el despliegue ocurrió:

| # | Síntoma | Causa raíz |
|---|---|---|
| 1 | La creación de la base falla | **La versión del motor quedó deprecada**: fijar una versión exacta en un guion de infraestructura tiene fecha de caducidad |
| 2 | La conexión queda inconsistente | Se asumía la zona de disponibilidad en vez de derivarla de la subred, **y el orden en que la API devuelve las subredes no está garantizado** |
| 3 | "El archivo no existe" aunque existe | Un binario nativo de Windows **no entiende rutas de estilo Unix** |
| 4 | El crawler falla | **La auto-referencia del grupo de seguridad limitada a un puerto no le basta a Glue**: exige apertura en todos los puertos desde el propio grupo |
| 5 | Crawler exitoso pero **catálogo vacío** | Se ejecutó **antes de cargar los esquemas**. Un crawler sobre una base sin tablas termina bien y no registra nada |
| 6 | El job se cuelga y luego falla | Corre **dentro de la red privada sin salida a internet**, así que no puede descargar el driver |

> **La moraleja de método, que es la que vale:** el número 5 enseña que **un proceso en estado "exitoso" no demuestra que el requisito se cumplió**. El requisito es que las tablas *aparezcan en el catálogo*, y eso solo lo demuestra consultar el catálogo y obtener un conteo mayor que cero. **Verificar el estado del proceso en vez de su efecto es exactamente el tipo de falso positivo que este ejercicio busca enseñar a detectar.**
