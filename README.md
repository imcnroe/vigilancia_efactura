# Vigilancia normativa de facturación electrónica

Servicio que vigila los cambios en las especificaciones técnicas de facturación
electrónica de España y Francia, y avisa a sus suscriptores con el impacto concreto
sobre su implementación.

La especificación completa está en [`docs/CONTEXT.md`](docs/CONTEXT.md). Este README
solo cuenta cómo arrancar y qué hay construido de verdad a día de hoy.

> **El producto es el catálogo de fuentes curado y el histórico de artefactos.** El
> software es la maquinaria que los explota. Nunca se borra un artefacto descargado,
> nunca se pierde una versión.

---

## ¿Has llegado aquí desde un `User-Agent` en tus registros?

Esta página es la que apunta el `User-Agent` del programa, para que sepas quién está
detrás sin tener que averiguarlo.

**Qué es.** Un proyecto personal de I+D, sin ánimo de lucro y sin servicio publicado. No
hay clientes ni empresa detrás.

**Qué hace.** Comprueba una vez al día si han cambiado unos pocos documentos técnicos
**públicos** —esquemas XSD de facturación electrónica, sus historiales de versiones y las
páginas que los listan— y guarda una copia cuando cambian. Nada más. El objetivo es
detectar a tiempo cuándo una administración publica una versión nueva de un esquema, que
es información que cualquiera puede consultar a mano y que este programa solo mira con más
constancia.

**Por qué esas peticiones y no otras.** Sólo se piden las URL concretas que un operador ha
dado de alta a mano tras comprobarlas. No hay rastreo, ni recorrido de enlaces, ni
descubrimiento automático: la lista está en
[`docs/catalogo-fuentes-semilla.md`](docs/catalogo-fuentes-semilla.md) y hoy son siete
documentos de dos organismos.

**Cuánto tráfico.** Una pasada completa son **7 peticiones al día**, repartidas entre dos
servidores. Abrir una sola de esas páginas en un navegador genera entre 30 y 60 peticiones
entre HTML, hojas de estilo, imágenes y tipografías: este programa pesa menos que una
visita.

**Qué hace para molestar lo menos posible.**

- Respeta `robots.txt`. Si una ruta está prohibida, no se pide y no se insiste: el portal
  de desarrolladores de la AEAT está excluido por esto mismo y no se toca.
- Usa peticiones condicionales (`If-None-Match`, `If-Modified-Since`). Si el documento no
  ha cambiado, la respuesta es un `304` sin cuerpo y no se descarga nada.
- Tiene un timeout duro y reintentos con retroceso exponencial: un servidor que va lento
  no recibe más presión, sino menos.
- No rellena formularios, no accede a zonas autenticadas, no envía datos de nadie y no
  trata datos personales.
- Se identifica siempre con un `User-Agent` propio y con un contacto.

**Para que pare.** Escribe a la dirección de contacto del `User-Agent` y se para. No hace
falta bloquear nada: eso es exactamente para lo que está puesta ahí.

---

## Arranque

```bash
python -m venv .venv && source .venv/bin/activate
make install                  # dependencias del proyecto y de desarrollo
cp .env.example .env          # rellenar COLLECTOR_USER_AGENT antes de salir a la red
make up                       # PostgreSQL 16 y MinIO en contenedores
make migrate                  # aplica las migraciones
make test                     # 144 unitarios + 42 de integración
make lint                     # ruff y mypy en modo estricto
```

`ruff` y `mypy --strict` pasan limpios sobre `src`, `tests` y `scripts`.

Los tests de integración se saltan solos, en segundos, si no hay Postgres en marcha; los
que tocan el servicio de ingesta crean su propia base temporal, la migran con Alembic y
la destruyen al terminar, porque confirman transacciones y los artefactos que crean no se
pueden borrar. Los unitarios no tocan la red, ni el disco, ni la base: usan un transporte
HTTP simulado.

En Windows hay tres detalles que conviene leer antes: [`docs/windows.md`](docs/windows.md).

### Poner una fuente en marcha

```bash
regwatch source add --family ES/FACTURAE --name "XSD Facturae 3.2.2" \
    --url https://www.facturae.gob.es/.../Facturaev3_2_2.xml \
    --kind SCHEMA --collector HTTP_FILE --cron "0 6 * * *" --priority HOT

# Una página de índice: lo que se vigila es su lista de publicaciones.
regwatch source add --family ES/FACTURAE --name "Índice de versiones Facturae" \
    --url https://www.facturae.gob.es/formato/ultima-version \
    --kind INDEX --collector HTTP_HTML_INDEX --priority WARM \
    --config '{"url_pattern": "/formato/versiones/.*[.](xml|pdf)$",
               "version_pattern": "v([0-9]+_[0-9]+(?:_[0-9]+)?)"}'

regwatch source test <ref>      # qué devuelve el colector, sin escribir nada
regwatch collect run <ref>      # una pasada ahora
regwatch source show <ref>      # artefactos, cambios e incidencias abiertas
regwatch source list            # salud de todo el catálogo
```

La planificación la hace el cron del sistema llamando a `collect run-due`, que ejecuta
las fuentes vencidas —las `HOT` primero— y sale con código 1 si alguna falló. No hay
proceso residente en la fase 1.

```cron
*/15 * * * *  cd /srv/regwatch && .venv/bin/regwatch collect run-due --json >> /var/log/regwatch.log
```

`collect reprocess <ref>` vuelve a normalizar el histórico de una fuente con el parser
actual y recalcula los diffs **sin descargar nada**. Es lo que hace que mejorar el parser
no obligue a volver a molestar a la AEAT.

La `ref` es el fragmento que muestra `source list`. Es la **cola** del identificador, no
el prefijo: en un UUIDv7 los primeros 12 caracteres hexadecimales son el instante de
creación, así que dos fuentes dadas de alta el mismo día comparten prefijo y solo la cola
aleatoria discrimina.

### Probar el detector contra esquemas reales

```bash
python scripts/fetch_fixtures.py        # descarga los tres XSD de Facturae
regwatch diff tests/fixtures/xsd/facturae_3_2_1.xsd \
              tests/fixtures/xsd/facturae_3_2_2.xsd
```

`fetch_fixtures.py` congela el SHA-256 de cada fichero en un manifiesto. Si un hash
deja de coincidir es que el organismo ha resustituido el fichero sin cambiar la
versión, que es exactamente el suceso que este producto existe para detectar.

---

## Estado de la fase 1

| Bloque | Estado |
|---|---|
| Andamiaje, docker-compose, CI | hecho |
| Modelo de datos completo (20 tablas) | hecho, migración aplicada y revertida contra Postgres real |
| Almacén de artefactos (local y S3) | hecho |
| Colector `HTTP_FILE` | hecho |
| Normalizador XSD | hecho |
| Detector de diffs (11 tipos) | hecho |
| Servicio de ingesta (une colector, almacén y base) | hecho |
| CLI: `source` y `collect` | hecho |
| Migración semilla con planes y jurisdicciones | hecho |
| Colector `HTTP_HTML_INDEX` y normalizador `INDEX_ENTRIES` | hecho |
| Captura real de las fuentes del criterio de fase | hecho, 7 fuentes de dos organismos |
| `COLLECTOR_USER_AGENT` con contacto real | **pendiente**, y bloquea la vigilancia desatendida |

**El criterio de la fase 1 está demostrado.** Siete fuentes reales dadas de alta, sus
siete artefactos capturados de `facturae.gob.es` y de `sede.agenciatributaria.gob.es`, y
el diff correcto entre dos versiones de un XSD contrastado contra el listado de cambios
que publica el propio organismo. El SHA-256 de los tres XSD coincide con el manifiesto
congelado el 11 de septiembre.

El pipeline está probado de extremo a extremo contra Postgres y MinIO reales, y **con los
bytes de verdad del organismo**: una fuente que servía Facturae 3.2.1 pasa a servir 3.2.2,
y el `change_event` que queda en la base trae los cinco cambios del listado oficial con su
clasificación intacta ([`test_ingest_service.py`](tests/integration/test_ingest_service.py)).

Lo que queda pendiente es el `COLLECTOR_USER_AGENT`. Las capturas hechas hasta ahora han
ido por `COLLECTOR_ALLOW_ANONYMOUS=true`, la vía explícita de desarrollo supervisado: deja
un aviso en el log en cada uso y no sirve para saltarse el `TODO_VERIFICAR`. **Antes de
poner el cron a correr solo hace falta un buzón de contacto real**, que es cuando el
apartado 7.1 empieza a importar de verdad.

### El catálogo semilla

`0003_semilla_catalogo.py` deja el andamiaje que el catálogo necesita para existir: las
siete jurisdicciones (España con las tres haciendas forales y Navarra colgando de ella,
Francia, y una `EU` transversal para Peppol), sus dieciséis familias normativas, seis
tipos de documento, los tres planes con sus límites en JSONB y la fila única de
`trial_config`.

Las `source` **no** entran en la migración: son el producto curado y las gestiona el
operador con `source add`. Una fuente cuya configuración de colector solo se pueda tocar
escribiendo una migración es una fuente que nadie ajusta.

`official_url` se rellena solo en Facturae y Verifactu, las dos URLs de nivel A —
descargadas e inspeccionadas a mano. El apartado 0 prohíbe inventar URLs de organismos, y
una URL plausible pero sin verificar es peor que ninguna porque nadie vuelve a
comprobarla.

### Lo que el servicio de ingesta hace cumplir

Cada punto tiene su test de integración contra Postgres real:

- Hash igual → no hay artefacto nuevo, solo se toca `last_checked_at`. Con `ETag` la
  petición ni descarga: sale un 304.
- El artefacto se guarda **entero y primero**. Si el normalizador no sabe qué hacer con
  él, el artefacto ya está a salvo y el aviso queda en el informe. Es el activo del
  producto; lo demás se puede recalcular.
- **Ningún cambio de contenido pasa en silencio.** Si no se puede comparar la estructura
  —un PDF donde antes había un XSD, un ZIP todavía sin normalizador— se emite un
  `change_event` de solo-hash (`comparison: CONTENT_HASH_ONLY`) para que lo mire una
  persona.
- Heartbeat: dos fallos seguidos abren una `pipeline_incident` y un log de nivel `ERROR`;
  el primer éxito la resuelve. Reintentar no abre una segunda: actualiza la que hay.
- Cada fuente corre en su propia transacción, con `SELECT ... FOR UPDATE SKIP LOCKED`.
  Una fuente que falla, o que tiene un bug, no bloquea la cola ni se procesa dos veces si
  se solapan dos pasadas.
- Un fallo de infraestructura a mitad de pasada (S3 caído) deshace la transacción y se
  anota como fallo de la fuente. No deja artefactos huérfanos.

---

## Cómo está organizado

```
src/regwatch/
  core/         ids (UUIDv7), enums, base declarativa, settings, sesión, logs JSON
  catalog/      jurisdicción, familia normativa, tipo de documento, fuente + repositorio
  ingest/
    collectors/ interfaz Collector, HTTP_FILE y HTTP_HTML_INDEX
    storage/    ArtifactStore direccionado por contenido + fábrica desde configuración
    normalizers/ XSD -> XSD_ELEMENTS, HTML -> INDEX_ENTRIES, y el despachador
    diff/       forma común del diff y un detector determinista por tipo de forma
    schedule.py próxima ejecución a partir del cron
    service.py  IngestService: la única pieza que toca colector, almacén y base
    bootstrap.py composición desde la configuración
  changes/      change_event y change_note
  accounts/     planes, organizaciones, usuarios, prueba, auditoría
  alerts/       suscripciones, preferencias, webhooks, log de envíos
  cli/
migrations/     Alembic
tests/          unit (sin red ni base), integration (requiere Postgres)
scripts/
```

Monolito modular. Un colector **no toca la base de datos ni S3**: recibe una URL,
devuelve bytes y metadatos, y ahí acaba. Persistir es cosa del servicio de ingesta. Eso
es lo que permite probar los colectores sin levantar infraestructura.

Las cinco etapas del apartado 7 son invocables por separado sobre datos ya almacenados,
que es el requisito de reprocesabilidad y no una comodidad: `normalize` y `diff` trabajan
sobre ficheros en disco sin base de datos, y `collect reprocess` sobre el histórico sin
red. `bootstrap.py` es el único sitio donde se juntan las piezas concretas (Postgres, S3,
httpx); ningún test unitario lo importa.

---

## Decisiones que se apartan del `CONTEXT.md`

Cada una está argumentada en el código, junto a la línea que la implementa.

**El colector de índices captura la página, no lo que la página enlaza.** El apartado 7.1
dice que `HTTP_HTML_INDEX` «encola la descarga de cada una», y no se hace. Dos motivos.
El primero es de corrección: la línea base del diff es **por fuente**, así que una fuente
que guardase el XSD 3.2, el 3.2.1, el 3.2.2 y tres PDF compararía cada artefacto nuevo
con el anterior de esa misma fuente, que es otro documento distinto, y saldría un diff
enorme y falso. El segundo es de producto: dar de alta una fuente a partir de un enlace
raspado es exactamente la URL plausible y sin verificar que el apartado 0 prohíbe.
Descubrir no es capturar. El `change_event` nombra la entrada nueva con su URL y el
operador decide si merece una `source` propia.

**Lo que se compara de un índice es su lista de entradas, no su HTML.** Una página de
portal cambia todos los días por el aviso de cookies, un banner o la fecha del pie.
Vigilar su SHA-256 da una alarma por semana, ninguna significa nada, y a la tercera nadie
las mira: es el falso positivo del apartado 1 con otro disfraz. El normalizador
`INDEX_ENTRIES` reduce la página a sus publicaciones —URL, título, fichero y versión— y
tira el resto, de modo que un cambio cosmético llega como diff vacío y severidad `INFO`.

**La identidad de una entrada de índice es su URL, no su texto.** El texto lo retoca
cualquiera («Versión 3.2.2» pasa a «Versión 3.2.2 (vigente)») y usarlo como clave
convertiría una corrección de estilo en un alta más una baja. Con la URL como clave, ese
caso sale como `INDEX_ENTRY_UPDATED` con severidad `INFO`.

**Un índice vacío no se lee como un catálogo retirado.** Si el selector deja de casar
—porque el organismo remaquetó— la forma sale vacía, y compararla con la anterior
anunciaría que han retirado todas las publicaciones. Es el falso positivo más caro
posible. La forma vacía se marca parcial y el detector no emite altas ni bajas contra
ella: lo dice en las notas y ya.

**Tres tipos de cambio nuevos, sin migración.** `INDEX_ENTRY_ADDED`, `_REMOVED` y
`_UPDATED` viven dentro del JSONB de `structural_diff`; `change_type` nunca fue una
columna. Las altas y bajas son `REQUIRES_CHANGE`, no porque rompan nada todavía, sino
porque alguien tiene que mirarlas; retitular un enlace es `INFO`.

**`source.next_check_at`, columna nueva.** El cron no se puede evaluar en SQL, así que
`collect run-due` necesita la próxima ejecución materializada. Se recalcula al terminar
cada pasada.

**`parser_version` dentro de la clave única de `normalized_form`.** Sin él, reprocesar
el histórico con un parser mejorado destruye la forma anterior y se pierde la
comparación entre parsers, que es justo lo que el apartado 7.2 quiere permitir.

**Ámbito de `alert_subscription` con cuatro FK nulables** y `CHECK` de exactamente una,
en vez del `scope_id` polimórfico. El polimorfismo sin clave ajena garantiza que antes
o después haya suscripciones apuntando a fuentes borradas.

**TicketBAI: tres fuentes, una por hacienda.** El mismo campo del mismo esquema es
obligatorio en Gipuzkoa y opcional en Álava y Bizkaia. Con una fuente por jurisdicción
la severidad sigue siendo una por cambio y no hay que complicar el detector.

**Restricciones que se ensanchan.** La tabla del apartado 7.3 no contempla el caso, y
es real: el código postal de TicketBAI pasó a 20 caracteres en la versión 1.2. Se emite
como `LENGTH_RESTRICTED` con severidad `INFO` y `direction: "relaxed"` en el detalle,
para no perder el cambio ni disparar una alarma falsa.

**La heurística de renombrado no usa la posición en el documento.** La forma
normalizada está ordenada por ruta y no conserva el orden original; añadirlo haría que
insertar un campo desplazara a todos los siguientes. El criterio es mismo padre, mismo
tipo declarado y una única pareja posible. Con dos candidatos no se decide: se emiten
alta y baja, que es el resultado seguro.

**El diff se calcula contra el último artefacto normalizable, no contra el
inmediatamente anterior.** Un portal que sirve una página de mantenimiento con un 200
genera un artefacto ilegible; si el XSD que llega después se comparase con esa página, el
cambio estructural se perdería y aparecería como un cambio de solo-hash. Se busca hacia
atrás (hasta 20 artefactos) el último que sí tiene forma, y los que quedan en medio se
anotan en el diff como `skipped_artifact_ids`, con una nota que lo explica.

**Cada artefacto tiene como mucho un `change_event`, buscado por `artifact_to_id`.** El
apartado 4.2 sugiere la clave `(artifact_from, artifact_to)`, y esa sigue siendo la
restricción única de la tabla. Pero al reprocesar con un parser mejorado, un artefacto que
antes era ilegible puede pasar a normalizarse, y entonces la línea base del siguiente
cambia: buscar por el par completo crearía un evento nuevo en vez de corregir el que ya
existe. Los eventos ya revisados por una persona no se tocan nunca.

**El estado de la fuente se actualiza también cuando falla.** `last_checked_at` y
`next_check_at` se mueven en cualquier caso; `last_success_at` solo con éxito. Si no se
recalculase la próxima ejecución tras un fallo, una fuente con `next_check_at` nulo se
intentaría en cada pasada del cron y castigaría a un organismo que ya está caído.

**Referencias de fuente por la cola del UUID, no por el prefijo.** Los identificadores son
UUIDv7 y sus primeros 12 caracteres hexadecimales son el instante de creación: las fuentes
del catálogo semilla, dadas de alta en la misma sesión, comparten prefijo. `source list`
muestra los últimos 8 caracteres y la búsqueda acepta el fragmento en cualquier posición,
para que lo que sale por pantalla sirva para volver a teclearlo.

**El `User-Agent` con `TODO_VERIFICAR` impide arrancar los colectores.** Cualquier orden
que vaya a salir a la red falla con un mensaje que dice qué hay que rellenar. El apartado
7.1 exige un `User-Agent` identificable y honesto; dejarlo como aviso en un fichero de
ejemplo no basta, porque la primera descarga contra un organismo público ocurre una sola
vez y no se puede deshacer.

---

## Garantías que hace cumplir la base de datos

No están solo escritas en el modelo: hay un test de integración que comprueba que
Postgres las rechaza de verdad.

- Una `change_note` **no puede** pasar a `PUBLISHED` sin revisor y fecha de revisión.
  No existe publicación automática, ni siquiera por API.
- Un `artifact` **no puede** actualizarse ni borrarse. Lo impide un trigger.
- El mismo hash **no puede** repetirse en una fuente: si el contenido no cambia no hay
  artefacto nuevo, solo se toca `last_checked_at`.
- Descartar un cambio **exige** motivo.
- Una suscripción tiene **exactamente un** ámbito.
- Los emails se comparan sin distinguir mayúsculas (`citext`).

---

## Lo que falta resolver fuera del código

**Acceso a los esquemas de la AEAT.** El portal de desarrolladores
(`www.agenciatributaria.es`) devuelve `ROBOTS_DISALLOWED`, y el apartado 7.1 obliga a
respetar `robots.txt`. **La salida técnica está confirmada**: el 19 de septiembre de 2026
el índice técnico de `sede.agenciatributaria.gob.es` y el host `static_files` de la misma
sede respondieron `200` a un colector que respeta `robots.txt`, y el índice ya está en el
catálogo con sus nueve entradas. Aun así conviene escribir a la AEAT identificando el
`User-Agent` y pedir acceso al portal de desarrolladores: es la familia normativa con más
valor comercial y depender de una sola vía es frágil.

**`COLLECTOR_USER_AGENT`.** Sigue sin datos de contacto. Las capturas hechas hasta hoy han
ido por `COLLECTOR_ALLOW_ANONYMOUS=true`, que es la vía de desarrollo supervisado y deja
aviso en el log en cada uso. **No sirve para la vigilancia diaria desatendida**, que es
para lo que existe la exigencia del apartado 7.1: un cron que baja ficheros todas las
mañanas sin que nadie mire es lo que alguien puede querer parar, y para eso necesita saber
a quién escribir. Hace falta un buzón —o una URL— antes de poner el cron.

**Fuentes de nivel B y C.** El catálogo semilla
([`docs/catalogo-fuentes-semilla.md`](docs/catalogo-fuentes-semilla.md)) marca qué URLs
están verificadas y cuáles no. Las no verificadas se dan de alta con
`source add --inactive` y se activan con `source activate` cuando `source test` confirme
la respuesta.

**Severidad por jurisdicción en TicketBAI.** El catálogo semilla documenta el hallazgo: el
campo Dirección en Destinatarios es obligatorio en Gipuzkoa y opcional en Álava y Bizkaia,
sobre el mismo esquema. La salida elegida —una `source` por hacienda— funciona, pero
duplica descargas del mismo fichero. El almacén las deduplica (la clave va direccionada
por contenido), así que el coste es una fila más en `artifact`, no un objeto más en S3.
Conviene confirmar con las tres diputaciones si los ficheros son de verdad el mismo antes
de dar por buena la simplificación.

---

## Lo siguiente

La fase 1 está cerrada. Lo que viene, en orden:

1. **Un buzón de contacto para `COLLECTOR_USER_AGENT`** y el cron en marcha. Es lo único
   que separa la vigilancia manual supervisada de la vigilancia de verdad, y es una
   decisión de negocio, no de código: vale un alias de equipo.
2. **Normalizador de texto narrativo (PDF y HTML).** Cuatro de las siete fuentes del
   catálogo son documentos narrativos —historiales de versiones, FAQ de desarrolladores—
   y hoy se capturan enteras pero no se comparan: cualquier cambio en ellas llega como
   solo-hash. Es lo que el analista de la fase 2 necesita para citar fechas de entrada en
   vigor.
3. **Normalizador de contenedores ZIP.** Lo necesitan el paquete francés de la DGFiP y los
   esquemas de TicketBAI. El artefacto sigue siendo el ZIP íntegro —es lo que publica el
   organismo— y cada XSD interno genera su propia `normalized_form` con el `path` dentro
   del ZIP como parte de la clave; la columna `inner_path` ya existe para eso.
4. **Afinar la extracción de versión del índice de Facturae.** El `version_pattern` actual
   lee `v3_2_1` del nombre del fichero, pero los historiales se llaman `_321.pdf` y se
   quedan sin versión. Funciona, y es configuración de la fuente: se arregla sin tocar
   código.
