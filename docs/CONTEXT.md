# Proyecto: servicio de vigilancia normativa de facturación electrónica (España + Francia)

> Este documento es la especificación completa del proyecto. Está escrito para
> entregárselo a un agente de código (Claude Code, Cursor, etc.) como contexto
> permanente del repositorio. Guárdalo como `CONTEXT.md` o `docs/spec.md` en la
> raíz del proyecto y refiérete a él en cada sesión de trabajo.

---

## 0. Cómo usar este documento

**Instrucción para el agente:**

Vas a construir el producto descrito aquí. Trabaja **por fases**, en el orden
del apartado 12. No intentes generar todo el proyecto de una vez. Al empezar
cada fase, relee este documento completo, propón el desglose de tareas de esa
fase, espera confirmación, y solo entonces escribe código.

Reglas de trabajo:

- **No inventes URLs de organismos oficiales ni versiones de esquemas.** El
  catálogo de fuentes se rellena con datos verificados manualmente. Si necesitas
  una URL, déjala como `TODO_VERIFICAR` y pídemela.
- **No inventes contenido normativo.** Todo texto sobre obligaciones fiscales
  que aparezca en la web pública debe salir de datos reales de la base de datos
  o quedar como placeholder marcado.
- Prioriza código legible y aburrido sobre código ingenioso.
- Cada módulo con lógica no trivial nace con sus tests.
- Si una decisión de este documento te parece equivocada, dilo antes de
  implementarla.

---

## 1. Qué es el producto

Un servicio de suscripción que **vigila los cambios en las especificaciones
técnicas de facturación electrónica** de España y Francia, y avisa a sus
suscriptores con el impacto concreto sobre su implementación.

**El cliente no es la pyme que factura.** El cliente es quien **construye
software que emite facturas**: fabricantes de ERP verticales, integradores,
departamentos de IT con desarrollo propio, plataformas homologadas.

### El problema que resuelve

Un ERP que factura en varios países depende de esquemas XSD, listas de códigos,
reglas de validación y calendarios que cambian constantemente y sin aviso
proactivo. El equipo de desarrollo se entera de un cambio cuando las facturas
empiezan a rebotar en producción. Nadie tiene una persona dedicada a mirar
portales de agencias tributarias todos los días.

### El valor que se cobra

No es el resumen de texto (eso lo genera cualquiera hoy). Es:

1. **Continuidad**: alguien lo mira todos los días, de forma sistemática.
2. **Diff estructural**: no "ha cambiado el documento", sino "el campo X pasa a
   obligatorio el día D y tu XML dejará de validar".
3. **Histórico acumulado**: el archivo versionado de todos los artefactos, que
   quien empiece dentro de un año no tiene.
4. **Fiabilidad**: cero falsos positivos, garantizada por revisión humana.

### Principio de diseño rector

> El producto es el **catálogo de fuentes curado** y el **histórico de
> artefactos**. El software es solo la maquinaria que lo explota.
> Toda decisión técnica debe proteger esos dos activos: nunca se borra un
> artefacto descargado, nunca se pierde una versión.

---

## 2. Alcance de la v1

**Países:** España y Francia, exclusivamente.

La arquitectura debe ser **multipaís desde el minuto uno** (el país es un dato,
nunca una rama de código), pero no se dan de alta más países en la v1.

### Familias normativas a cubrir

**España**
- Verifactu / RRSIF (sistemas informáticos de facturación, AEAT)
- SII (Suministro Inmediato de Información, AEAT)
- Facturae (formato de factura electrónica)
- Factura electrónica B2B obligatoria (desarrollo reglamentario Ley Crea y Crece)
- TicketBAI: Álava, Bizkaia, Gipuzkoa — tratados como **tres jurisdicciones
  distintas**, no como una
- Navarra
- FACe / FACeB2B (sector público)

**Francia**
- Réforme de la facturation électronique (spécifications externes, DGFiP)
- Formats admitidos: Factur-X, UBL, CII
- E-reporting
- Annuaire / directorio central
- Registro de plateformes agréées (PA)
- Chorus Pro (sector público)

**Transversal**
- Peppol BIS Billing y sus listas de códigos (ciclo de releases semestral;
  afecta a ambos países)

### Fuera de alcance en v1

LATAM, Italia, Alemania, Polonia, Bélgica. El modelo de datos debe admitirlos
sin migración, pero no se implementan colectores.

---

## 3. Stack tecnológico (decidido, no reabrir)

| Capa | Tecnología | Motivo |
|---|---|---|
| Backend API | **Python 3.12 + FastAPI** | El pipeline es Python; una sola runtime |
| ORM | **SQLAlchemy 2.x + Alembic** | Migraciones versionadas obligatorias |
| Base de datos | **PostgreSQL 16** | JSONB para diffs, full-text search nativo |
| Almacén de artefactos | **S3 / compatible (MinIO en local)** | Inmutable, versionado |
| Cola / scheduler | **APScheduler + tabla de jobs en Postgres** | No montar Airflow para 40 jobs |
| Cache / rate limit | **Redis** | |
| Frontend | **Ionic 8 + Angular + Capacitor 8** | Código único → web, iOS, Android |
| Emails transaccionales | **Resend o Postmark** | Entregabilidad; no SMTP propio |
| Pagos | **Paddle** (principal) / Stripe (plan B) | Ver apartado 9 |
| Auth | **JWT propio** (access + refresh) | Sin dependencia de proveedor externo |
| Scraping resistente | **Playwright** | Solo para fuentes que lo requieran |
| LLM | API de Claude | Solo en la etapa de redacción (ver 7.4) |
| Despliegue | Docker Compose sobre un VPS | Un VPS de 20 €/mes sobra al principio |
| Observabilidad | Sentry + logs estructurados JSON | |

**Idiomas de la interfaz:** español, francés e inglés desde el principio. El
cliente francés no comprará un producto que solo habla español. La i18n no se
añade después: se diseña desde el primer componente.

---

## 4. Modelo de datos

Todas las tablas llevan `id` (UUID), `created_at`, `updated_at`. Borrado lógico
(`deleted_at`) en las entidades de negocio; **nunca borrado físico** en
artefactos ni cambios.

### 4.1 Catálogo normativo

**`jurisdiction`** — Ámbito con potestad normativa propia.
- `code` (ej. `ES`, `ES-BI`, `FR`), `name_i18n` (JSONB), `country_code`,
  `parent_id` (autoreferencia: Bizkaia cuelga de España), `is_active`

**`regulation_family`** — Familia normativa dentro de una jurisdicción.
- `jurisdiction_id`, `code` (ej. `VERIFACTU`, `SII`, `FR_EINVOICING`),
  `name_i18n`, `description_i18n`, `authority` (AEAT, DGFiP, Diputación Foral…),
  `official_url`

**`document_type`** — Documento o formato concreto afectado.
- `regulation_family_id`, `code` (ej. `FACTURAE_32`, `FACTUR_X`, `UBL_INVOICE`),
  `name_i18n`

**`source`** — **La entidad más importante del sistema.** Una fuente vigilada.
- `regulation_family_id`, `document_type_id` (nullable)
- `name`, `url`, `source_kind`: `SCHEMA` | `INDEX` | `NARRATIVE` | `SANDBOX`
- `collector_type`: `HTTP_FILE` | `HTTP_HTML_INDEX` | `PLAYWRIGHT` | `RSS` | `API`
- `collector_config` (JSONB: selectores CSS, XPath, patrones de nombre, cabeceras)
- `check_frequency` (cron), `priority`: `HOT` | `WARM` | `COLD`
- `last_checked_at`, `last_success_at`, `consecutive_failures`
- `is_active`, `notes` (texto libre del operador: por qué existe, qué mirar)

**`artifact`** — Cada captura concreta de una fuente. **Inmutable.**
- `source_id`, `captured_at`, `content_hash` (SHA-256)
- `storage_key` (ruta en S3), `mime_type`, `size_bytes`
- `declared_version` (versión que declara el propio documento, si se detecta)
- `http_status`, `http_headers` (JSONB)
- Índice único sobre `(source_id, content_hash)`: si el hash no cambia, no se
  crea artefacto nuevo, solo se actualiza `last_checked_at` de la fuente.

**`normalized_form`** — Representación comparable de un artefacto.
- `artifact_id`, `form_type`: `XSD_ELEMENTS` | `CODE_LIST` | `TEXT_BLOCKS` |
  `INDEX_ENTRIES`
- `payload` (JSONB), `parser_version` (para poder reprocesar el histórico
  cuando mejores el parser)

### 4.2 Cambios detectados

**`change_event`** — Un cambio detectado entre dos artefactos.
- `source_id`, `artifact_from_id`, `artifact_to_id`
- `detected_at`
- `structural_diff` (JSONB — ver formato en 7.3)
- `status`: `DETECTED` | `UNDER_REVIEW` | `PUBLISHED` | `DISCARDED`
- `discard_reason` (obligatorio si `DISCARDED`)

**`change_note`** — La ficha publicable, escrita a partir de un `change_event`.
Uno a uno con `change_event`, pero separada porque tiene ciclo editorial propio.
- `change_event_id`
- `severity`: `INFO` | `REQUIRES_CHANGE` | `BLOCKING`
- `title_i18n`, `summary_i18n`, `impact_i18n`, `action_required_i18n` (JSONB)
- `effective_date` (fecha de entrada en vigor, nullable), `effective_date_note`
- `affected_fields` (JSONB: lista de rutas XML afectadas)
- `xml_example_before`, `xml_example_after` (texto, nullable)
- `source_reference` (documento y página de origen)
- `llm_draft` (JSONB: el borrador que generó el modelo, conservado para auditar)
- `reviewed_by_user_id`, `reviewed_at`, `published_at`
- `is_public` (si aparece en la zona pública o solo para suscriptores)

**Regla de negocio innegociable:** una `change_note` **nunca** pasa a
`PUBLISHED` sin `reviewed_by_user_id`. No existe publicación automática. El
sistema debe hacer imposible saltarse este paso, incluso por API.

### 4.3 Usuarios y organizaciones

**`organization`** — La unidad que contrata. El SaaS es B2B: se factura a la
empresa, no a la persona.
- `name`, `vat_number`, `country_code`, `billing_email`
- `plan_id`, `subscription_status`
- `payment_provider`, `payment_customer_id`, `payment_subscription_id`
- Control de prueba: `trial_plan_id` (qué plan se prueba), `trial_started_at`,
  `trial_ends_at`, `trial_extension_days`, `trial_extended_by_user_id`,
  `has_used_trial` (bool, no se resetea nunca)
- Antiabuso: `signup_email_domain`, `signup_ip`

**`user`**
- `organization_id`, `email` (único), `password_hash` (Argon2id)
- `full_name`, `locale`, `timezone`
- `role`: ver apartado 5
- `email_verified_at`, `last_login_at`
- `mfa_secret` (nullable), `mfa_enabled`

**`invitation`** — Alta de compañeros dentro de una organización.
- `organization_id`, `email`, `role`, `token`, `expires_at`, `accepted_at`

### 4.4 Suscripciones a alertas

**`alert_subscription`** — Qué quiere vigilar cada usuario.
- `user_id`
- `scope_type`: `JURISDICTION` | `REGULATION_FAMILY` | `DOCUMENT_TYPE` | `SOURCE`
- `scope_id`
- `min_severity`: `INFO` | `REQUIRES_CHANGE` | `BLOCKING`
- `channels` (JSONB: `{"email": true, "push": true, "webhook": false}`)
- `is_active`

**`notification_preference`** — Uno por usuario.
- `digest_frequency`: `IMMEDIATE` | `DAILY` | `WEEKLY`
- `digest_hour`, `digest_weekday`
- `blocking_bypasses_digest` (bool, por defecto `true`: un cambio bloqueante
  sale inmediatamente aunque el usuario tenga digest semanal)

**`webhook_endpoint`** — Solo planes de pago.
- `organization_id`, `url`, `secret`, `is_active`, `last_delivery_at`,
  `consecutive_failures`

**`notification_log`** — Auditoría de todo lo enviado.
- `user_id`, `change_note_id`, `channel`, `sent_at`, `status`, `error`
- Sirve para dos cosas: soporte ("no me llegó") y para no duplicar envíos.

### 4.5 Planes

**`plan`**
- `code`, `name_i18n`, `price_monthly`, `price_yearly`, `currency`
- `limits` (JSONB): `max_users`, `max_jurisdictions`, `webhooks_enabled`,
  `api_access`, `history_days`, `sandbox_alerts`
- `is_public` (para poder crear planes a medida no listados)

Los límites viven en datos, **nunca en código**. Debe ser posible cambiar el
modelo de negocio (de gratis a pago, de por usuario a por jurisdicción)
editando registros, sin desplegar.

---

## 5. Tipos de usuario y permisos

### Roles dentro de una organización cliente

| Rol | Puede |
|---|---|
| `OWNER` | Todo lo del admin + gestionar suscripción, facturación y cancelar |
| `ADMIN` | Invitar y eliminar usuarios, configurar webhooks, ver todo el contenido |
| `MEMBER` | Ver contenido según plan, configurar **sus propias** alertas |

### Roles internos (equipo del servicio)

| Rol | Puede |
|---|---|
| `EDITOR` | Revisar, editar y publicar `change_note`. Marcar falsos positivos |
| `OPERATOR` | Todo lo del editor + gestionar el catálogo de fuentes, relanzar colectores, ver estado del pipeline |
| `SUPERADMIN` | Todo + gestión de organizaciones, planes y usuarios internos |

Los roles internos viven en el mismo modelo `user` con una organización interna
marcada como `is_internal`. **La zona de administración es una sección
separada** (`/admin`), no una vista con botones extra dentro del panel de
cliente.

### Usuario anónimo

Ve la zona pública. Puede registrarse. No accede a ninguna `change_note` que no
tenga `is_public = true`.

---

## 6. Superficie de la aplicación

### 6.1 Zona pública (SEO — es el canal de captación)

Renderizado del lado del servidor o prerenderizado. Esta zona **tiene que
indexar bien**: es de donde vendrán la mayoría de los clientes.

- **Portada.** Propuesta de valor, países cubiertos, últimos cambios públicos.
- **Calendario normativo.** Vista cronológica de fechas de entrada en vigor por
  jurisdicción. Página con alto valor de tráfico orgánico; mantenerla siempre
  actualizada.
- **Ficha por jurisdicción** (`/es/espana`, `/fr/france`, `/es/bizkaia`…).
  Qué se vigila, cuántas fuentes, últimos cambios públicos.
- **Ficha por familia normativa** (`/es/verifactu`, `/fr/facturation-electronique`).
- **Detalle de cambio público.** Versión recortada de la ficha completa, con
  llamada a registro para ver el diff estructural y el ejemplo XML.
- **Precios.** Debe funcionar aunque todos los planes sean gratuitos.
- **Blog / notas.** Markdown desde base de datos.
- Legales: aviso legal, privacidad, cookies, condiciones. Obligatorio en la UE.

**Estrategia de contenido:** publicar como público un ~20% de los cambios, los
de severidad `INFO`, con retardo de 30 días. Es el escaparate. Los `BLOCKING` y
los recientes son siempre de suscriptor.

### 6.2 Zona privada (panel de cliente)

- **Dashboard.** Cambios recientes según las suscripciones del usuario,
  destacando los bloqueantes y los que tienen fecha de vigor próxima.
- **Explorador de cambios.** Filtros por jurisdicción, familia, tipo de
  documento, severidad, rango de fechas, estado. Búsqueda de texto completo.
- **Detalle de cambio.** Ficha completa + diff estructural navegable + ejemplo
  XML antes/después + enlace al artefacto original archivado + histórico de
  versiones de esa fuente.
- **Mis alertas.** Creación y edición de `alert_subscription`. Interfaz de
  árbol: País → Familia → Documento, con casillas y selector de severidad
  mínima por rama.
- **Preferencias de notificación.** Frecuencia de digest, hora, canales.
- **Equipo.** Invitar, cambiar rol, revocar (solo `ADMIN`/`OWNER`).
- **Facturación.** Plan actual, uso frente a límites, facturas, cambiar plan,
  cancelar (solo `OWNER`).
- **Webhooks y API.** Endpoints, secreto, log de entregas, claves de API.
- **Perfil.** Datos, contraseña, MFA, idioma.

### 6.3 Zona de administración interna (`/admin`)

- **Bandeja de revisión.** Cola de `change_event` en estado `DETECTED`. Para
  cada uno: el diff estructural calculado, el borrador del LLM, y un editor
  para redactar la ficha final. Botones: publicar, guardar borrador, descartar
  (con motivo obligatorio).
- **Catálogo de fuentes.** CRUD completo. Estado de salud de cada colector,
  último éxito, fallos consecutivos, botón de ejecución manual, previsualización
  de lo que devuelve el colector con la configuración actual.
- **Salud del pipeline.** Panel con fuentes en fallo, latencia de colectores,
  cola de revisión, envíos fallidos.
- **Organizaciones y usuarios.** Alta manual, cambio de plan, extensión de
  prueba, suplantación de identidad para soporte (con registro de auditoría).

---

## 7. Motor de ingesta

Cinco etapas desacopladas. Cada una debe poder ejecutarse de forma
independiente sobre datos ya almacenados (reprocesabilidad).

### 7.1 Colector

Un job por fuente, según su `check_frequency`. Descarga, calcula SHA-256,
compara con el último artefacto de esa fuente:

- Hash igual → actualiza `last_checked_at` y termina.
- Hash distinto → sube el fichero íntegro a S3 y crea `artifact`.

Requisitos:
- `User-Agent` identificable y honesto. Respetar `robots.txt`. Cadencia
  razonable: no somos un scraper agresivo, y que nos bloqueen mata el producto.
- Reintentos con backoff exponencial.
- **Heartbeat:** si `consecutive_failures >= 2`, alerta al operador. El fallo
  silencioso de un colector es el único riesgo existencial del producto.
- Timeout duro. Un portal caído no puede bloquear el resto de la cola.

Tipos de colector a implementar:
- `HTTP_FILE`: descarga directa de XSD, ZIP, PDF.
- `HTTP_HTML_INDEX`: parsea un listado de publicaciones; detecta entradas
  nuevas o versiones incrementadas y encola la descarga de cada una.
- `PLAYWRIGHT`: para portales con JS o protección.
- `RSS`: si el organismo lo ofrece.
- `SANDBOX`: fase 5, no en v1.

### 7.2 Normalizador

Convierte el artefacto a una forma comparable.

- **XSD** → lista plana de elementos: `{path, name, type, min_occurs,
  max_occurs, restrictions, enumerations, annotation}`. Resolver `include` e
  `import`; si una dependencia falta, marcar la forma como parcial en vez de
  fallar.
- **Listas de códigos** (CSV, XLSX, XML) → `{code, description, valid_from,
  valid_to}`.
- **PDF y HTML narrativo** → bloques de texto con número de página y jerarquía
  de encabezados. Se guarda la posición para poder citar "página 14".
- **Índices** → `{title, url, version, published_at}`.

Guarda siempre `parser_version`. Cuando mejores un parser, debes poder
reprocesar todo el histórico y recalcular diffs.

### 7.3 Detector de cambios (determinista, sin IA)

**Esta es la pieza que diferencia el producto de un `diff` con un cron.** No
compara texto: compara estructura.

Sobre formas `XSD_ELEMENTS`, detecta y clasifica:

| Tipo | Severidad sugerida |
|---|---|
| `FIELD_ADDED_OPTIONAL` | INFO |
| `FIELD_ADDED_MANDATORY` | BLOCKING |
| `FIELD_REMOVED` | BLOCKING |
| `CARDINALITY_TIGHTENED` (0..1 → 1..1) | BLOCKING |
| `CARDINALITY_RELAXED` | INFO |
| `TYPE_CHANGED` | REQUIRES_CHANGE |
| `LENGTH_RESTRICTED` | REQUIRES_CHANGE |
| `PATTERN_CHANGED` | REQUIRES_CHANGE |
| `ENUM_VALUE_ADDED` | INFO |
| `ENUM_VALUE_REMOVED` | BLOCKING |
| `ELEMENT_RENAMED` (heurística: mismo tipo y posición) | BLOCKING |

Sobre listas de códigos: altas, bajas y cambios de vigencia.
Sobre texto narrativo: bloques añadidos, eliminados o modificados, con
detección de patrones de fecha para candidatos a `effective_date`.

El resultado es el JSONB `structural_diff`, y es **la fuente de verdad**. La
severidad calculada aquí es una sugerencia; el editor humano puede cambiarla.

### 7.4 Analista (única etapa con LLM)

Entrada: el `structural_diff` **ya calculado** + el documento narrativo asociado
si existe.

Salida: JSON estructurado con `title`, `summary`, `impact`, `action_required`,
`effective_date`, `effective_date_evidence`, `severity_suggestion`, en los tres
idiomas.

**Restricciones estrictas del prompt:**
- El modelo **redacta**, no descubre. Solo puede hablar de campos que aparecen
  en el diff que se le pasa.
- Si no encuentra fecha de entrada en vigor explícita en el documento, debe
  devolver `null`. Prohibido inferirla.
- Toda afirmación debe poder rastrearse al diff o a un bloque de texto citado
  con su página.
- Se guarda íntegro en `change_note.llm_draft` para poder auditar después qué
  dijo el modelo frente a lo que publicó el humano.

Genera además el ejemplo XML antes/después de forma **programática** a partir
del diff, no con el LLM. Es determinista y no puede alucinar.

### 7.5 Publicación

Revisión humana obligatoria (apartado 4.2) → `PUBLISHED` → dispara el motor de
notificaciones.

---

## 8. Sistema de alertas

### Resolución de destinatarios

Al publicarse una `change_note`, resolver qué usuarios la reciben:

1. Buscar `alert_subscription` activas cuyo `scope` contenga la fuente del
   cambio (herencia: suscribirse a `ES` incluye Verifactu, SII, Facturae y las
   forales; suscribirse a `VERIFACTU` incluye solo esa familia).
2. Filtrar por `min_severity`.
3. Filtrar por límites del plan de la organización (un plan gratuito puede
   estar limitado a una jurisdicción).
4. Deduplicar: un usuario con varias suscripciones solapadas recibe **un solo**
   aviso.
5. Aplicar `notification_preference`: inmediato, o encolar para digest.
6. Excepción: si `severity = BLOCKING` y `blocking_bypasses_digest`, sale ya.

### Canales

- **Email.** Plantilla HTML sobria, en el idioma del usuario. Asunto con
  formato `[ES · Verifactu · BLOQUEANTE] Nuevo campo obligatorio a partir del …`
  El asunto es el producto: se tiene que entender sin abrir el correo.
- **Push móvil.** Vía Capacitor + FCM/APNs. Solo para `BLOCKING` por defecto.
- **Webhook.** POST JSON firmado con HMAC-SHA256 en cabecera. Reintentos con
  backoff. Desactivación automática tras N fallos consecutivos, con aviso al
  administrador de la organización.
- **Digest.** Un email por periodo agrupando todo lo pendiente, ordenado por
  severidad y proximidad de fecha de vigor.

Todo envío se registra en `notification_log`. Sin excepciones.

---

## 9. Pagos y planes

### Decisión: Paddle como proveedor principal

**Motivo:** Paddle actúa como *merchant of record*. Es él quien vende
legalmente al cliente final, y por tanto asume el cálculo, la recaudación y la
declaración del IVA en cada país, incluido el tratamiento de reverse charge
para B2B intracomunitario. Para un operador pequeño que vende a empresas en
España y Francia, eso elimina una carga administrativa que de otro modo requiere
un asesor fiscal. Soporta tarjeta y **PayPal** de forma nativa, con
suscripciones recurrentes.

**Contrapartida:** comisión más alta que Stripe y menos control sobre el
aspecto del checkout.

**Redsys queda descartado**: está pensado para pagos puntuales en el mercado
español, la recurrencia internacional es frágil, y no encaja con clientes
franceses.

**Plan B documentado, no implementado:** Stripe Billing. Si en algún momento el
volumen justifica gestionar el IVA por cuenta propia, la capa de abstracción
debe permitir el cambio sin tocar el resto del sistema.

### Abstracción obligatoria

Toda la lógica de pago vive detrás de una interfaz `PaymentProvider` con las
operaciones: `create_checkout_session`, `get_subscription`, `cancel_subscription`,
`change_plan`, `list_invoices`, `handle_webhook`.

El resto del código **nunca** importa el SDK de Paddle. Debe existir un
`FakePaymentProvider` para desarrollo y tests que simule todo el ciclo de vida
sin red.

### Ciclo de vida de la suscripción

Estados en `organization.subscription_status`: `TRIALING`, `ACTIVE`,
`PAST_DUE`, `CANCELED`, `EXPIRED`.

- Registro → periodo de prueba (ver 9.4).
- Fin de prueba sin pago → degrada al plan `FREE`, **no bloquea la cuenta**.
- Fallo de cobro → `PAST_DUE`, tres avisos por email en 10 días, luego degrada.
- Cancelación → mantiene acceso hasta fin del periodo pagado.

### 9.4 Modo prueba (trial)

**Prueba y plan gratuito son cosas distintas y ambas existen.** La prueba es
acceso temporal a un plan de pago; el plan `FREE` es un estado permanente y
limitado. Al acabar la prueba se cae al segundo, nunca a la nada.

Esto se llama *reverse trial* y es el patrón adecuado aquí: el usuario prueba lo
bueno, y si no paga se queda con una versión reducida pero útil que lo mantiene
en la órbita del producto. En este negocio concreto es especialmente potente,
porque el cliente que se quedó en `FREE` seguirá recibiendo el digest semanal
de una jurisdicción, y el día que un cambio bloqueante le afecte de verdad ya
sabe quién se lo va a contar.

#### Parámetros — todos configurables en datos, nunca en código

Tabla `trial_config` (fila única, editable desde `/admin`):
- `enabled` (bool)
- `duration_days` (por defecto **14**)
- `trial_plan_code` (qué plan se concede durante la prueba; por defecto `PRO`)
- `requires_card` (bool, por defecto **false**)
- `fallback_plan_code` (a dónde se cae al terminar; por defecto `FREE`)

Debe ser posible cambiar la duración, el plan probado o desactivar la prueba
entera sin desplegar. Durante la fase de validación vas a querer moverlo.

#### Sin tarjeta por adelantado

Por defecto `requires_card = false`. Pedir tarjeta antes de la prueba multiplica
la calidad del lead pero hunde el volumen, y en la fase inicial necesitas
conversaciones, no ingresos. La bandera existe para poder invertir esa decisión
más adelante si te llega demasiado ruido.

#### Concesión y unicidad

- La prueba se concede a la **organización**, no al usuario. Un compañero
  invitado a una organización que ya agotó su prueba no genera una nueva.
- `has_used_trial` se pone a `true` al iniciarla y **no se revierte jamás** de
  forma automática. Solo un `SUPERADMIN` puede conceder una prueba adicional, y
  queda registrado en auditoría con motivo obligatorio.
- Antiabuso mínimo, sin paranoia: verificación de email obligatoria antes de
  activar la prueba, bloqueo de dominios de email desechables, y una alerta al
  operador (no un bloqueo) si varias organizaciones se registran desde la misma
  IP en poco tiempo. No montes detección de fraude: no eres un banco y el coste
  marginal de un abusador aquí es un email de más.

#### Durante la prueba

- Acceso completo al plan probado, sin marcas de agua ni funciones capadas.
  Si le enseñas una versión mutilada, evalúa una versión mutilada.
- Banner persistente pero discreto en el panel: días restantes y enlace a
  planes. En los últimos 3 días, cambia a un tono más visible.
- Las alertas funcionan igual que en el plan de pago. **Este es el punto
  crítico del producto**: la prueba solo convierte si durante esos 14 días le
  llega al menos un aviso que le resulte útil. Con dos países vigilados eso es
  probable, pero no está garantizado.

  Por eso: en el momento de activar la prueba, ejecutar un **backfill** que
  muestre en el dashboard los cambios publicados de los últimos 90 días que
  encajen con sus alertas configuradas, marcados claramente como históricos.
  Así el valor se ve el primer día y no dependes de que la AEAT publique algo
  esa quincena.

#### Secuencia de emails de la prueba

Independiente del motor de alertas normativas, con su propia plantilla:

| Momento | Contenido |
|---|---|
| Día 0 | Bienvenida + invitación a configurar alertas (la acción de activación) |
| Día 1, si no configuró alertas | Recordatorio con un solo enlace |
| Día 7 | Resumen de lo que ha recibido hasta ahora |
| Día 11 | Aviso de fin de prueba + planes |
| Día 14 | Prueba terminada, qué conserva en `FREE` |
| Día 21 | Un último contacto, personal y sin plantilla comercial |

Si `requires_card = true`, el aviso del día 11 pasa a ser obligatorio y con al
menos 72 horas de antelación al primer cobro. En la UE esto no es cortesía.

#### Al terminar

1. `subscription_status` → `EXPIRED`, `plan_id` → `fallback_plan_code`.
2. **No se borra nada.** Sus `alert_subscription` se conservan intactas; las que
   excedan los límites del plan gratuito se marcan `is_active = false` con un
   motivo visible en la interfaz ("requiere plan Pro"), de modo que al contratar
   se reactiven solas.
3. El histórico consultable se recorta a `history_days` del plan gratuito, pero
   el dato no se elimina de la base.
4. Los webhooks se desactivan y se notifica al administrador de la organización.

#### Extensión manual

Desde `/admin`, un `OPERATOR` o superior puede extender una prueba indicando
días y motivo. Se registra en auditoría. Es una herramienta de ventas legítima
—alguien que está evaluando de verdad y necesita dos semanas más para
convencer a su jefe— y conviene tenerla desde el principio.

Los webhooks del proveedor son la **única** fuente de verdad del estado de
suscripción. Nunca activar un plan desde el retorno del navegador. Verificar
firma, procesar de forma idempotente, tolerar duplicados y desorden.

### Planes iniciales (valores en datos, cambiables sin desplegar)

| Plan | Precio | Límites |
|---|---|---|
| `FREE` | 0 € | 1 jurisdicción, 1 usuario, digest semanal, sin webhooks, histórico 30 días |
| `PRO` | 149 €/mes | Ambos países, 5 usuarios, alertas inmediatas, webhooks, histórico completo |
| `TEAM` | 349 €/mes | Usuarios ilimitados, API, soporte prioritario, SLA de aviso |

Los precios anuales con dos meses de descuento. El sistema debe funcionar
íntegramente con todos los planes a 0 € durante la fase de validación, y
permitir activar el cobro después sin cambios de código.

---

## 10. Apps nativas

**Código único: Ionic + Angular, empaquetado con Capacitor** para web, iOS y
Android.

### Restricción crítica sobre las tiendas

Apple y Google exigen su sistema de compra integrada, con su comisión, para la
venta de contenido digital dentro de la app. Para evitarlo, la app nativa
**no vende nada, nunca**:

- No hay pantalla de precios, ni botón de suscripción, ni enlace de compra en
  las builds nativas.
- El usuario se registra y contrata **en la web**. La app solo autentica cuentas
  ya existentes.
- Si un usuario sin plan abre la app, ve un mensaje neutro indicando que
  gestione su cuenta desde el sitio web, **sin enlace directo al checkout** (la
  política sobre enlaces externos varía por jurisdicción y ha cambiado
  recientemente; asumir la interpretación restrictiva).

Implementar esto con una bandera de compilación (`IS_NATIVE_BUILD`) que elimine
del bundle todos los componentes de facturación. No basta con ocultarlos.

### Alcance funcional de la app nativa

Deliberadamente reducido — es un lector con notificaciones:
- Login y biometría
- Feed de cambios según las alertas del usuario
- Detalle de cambio
- Notificaciones push
- Ajustes de notificación

Gestión de equipo, webhooks, API y facturación: solo web.

### Nota realista

La app móvil aporta poco valor de negocio inicial: un desarrollador de ERP
recibe estos avisos en el correo, en su escritorio. **Constrúyela en la fase 6,
no antes.** La arquitectura la contempla desde el principio para que sea
posible; la prioridad es otra.

---

## 11. Requisitos transversales

### Seguridad
- Contraseñas con Argon2id. MFA TOTP opcional, obligatorio para roles internos.
- Rate limiting en login, registro y recuperación de contraseña.
- Tokens: access de vida corta, refresh rotatorio con detección de reutilización.
- Aislamiento estricto por organización: **toda** consulta filtra por
  `organization_id`. Escribir un test que verifique que ningún endpoint
  devuelve datos de otra organización.
- Cabeceras de seguridad, CSP estricta, CORS cerrado.
- Secretos por variables de entorno; nunca en el repositorio.

### Cumplimiento (UE, no opcional)
- RGPD: exportación y borrado de datos personales bajo petición.
- Registro de consentimiento de cookies antes de cargar analítica.
- Condiciones de servicio con un descargo claro: **el servicio es informativo
  y no constituye asesoramiento fiscal ni legal**. Esto protege el negocio.
- Analítica sin cookies (Plausible o similar) para simplificar el banner.

### Internacionalización
- Todo texto de interfaz en ficheros de traducción desde el primer día.
- Contenido normativo en JSONB por idioma. Si falta una traducción, mostrar el
  original marcado, nunca cadena vacía.
- Formatos de fecha y número según locale.

### Calidad
- Tests unitarios obligatorios en: parser XSD, detector de diffs, resolución de
  destinatarios de alertas, y máquina de estados de suscripción **incluyendo el
  ciclo completo de prueba** (concesión, unicidad por organización, expiración,
  degradación, extensión manual, reactivación al contratar). Son las cuatro
  piezas donde un fallo silencioso hace daño real.
- Tests de integración de la API con base de datos real en contenedor.
- Fixtures con XSD reales de ambos países, versión antigua y nueva, para
  probar el detector contra casos verdaderos.
- CI en GitHub Actions: lint, tipos, tests, build.

---

## 12. Fases de entrega

Cada fase termina con algo desplegable y verificable.

**Fase 1 — Núcleo de datos e ingesta (sin interfaz).**
Modelo de datos completo, migraciones, colectores `HTTP_FILE` y
`HTTP_HTML_INDEX`, almacenamiento en S3, normalizador XSD, detector de diffs.
CLI para dar de alta fuentes y lanzar colectores a mano.
*Verificable:* dar de alta 5 fuentes reales, capturar artefactos, y detectar un
diff correcto entre dos versiones de un XSD de prueba.

**Fase 2 — Analista y panel de administración.**
Integración con el LLM, generación de fichas, bandeja de revisión, CRUD de
fuentes, panel de salud del pipeline.
*Verificable:* revisar y publicar una ficha real de principio a fin.

**Fase 3 — API, autenticación y panel de cliente.**
Organizaciones, usuarios, roles, invitaciones, JWT, explorador de cambios,
detalle, configuración de alertas.
*Verificable:* un usuario se registra, configura alertas y ve contenido real.

**Fase 4 — Notificaciones.**
Motor de resolución de destinatarios, emails, digest, webhooks, log de envíos.
*Verificable:* publicar un cambio y comprobar que llega a quien debe y solo a
quien debe.

**Fase 5 — Zona pública y pagos.**
Web pública con SEO, calendario normativo, integración con Paddle, planes,
ciclo de vida de suscripción.
*Verificable:* un cliente real puede contratar y pagar.

**Fase 6 — Apps nativas.**
Builds de Capacitor, push, biometría, bandera de compilación sin facturación.

**Fase 7 — Colector `SANDBOX`.**
Envío periódico de facturas sintéticas a entornos de homologación para detectar
cambios no documentados. Es la ventaja competitiva a largo plazo, pero no
bloquea el lanzamiento.

---

## 13. Criterios de aceptación del conjunto

El producto está listo cuando:

1. Un cambio real en un XSD de la AEAT o de la DGFiP se detecta en menos de 24
   horas desde su publicación.
2. El diff estructural identifica correctamente un campo que pasa de opcional a
   obligatorio, y lo clasifica como bloqueante.
3. La ficha publicada incluye ejemplo XML antes/después generado
   programáticamente.
4. Ningún cambio se publica sin revisión humana registrada.
5. Un usuario suscrito a "España · Verifactu · severidad mínima
   REQUIRES_CHANGE" recibe exactamente los avisos que le corresponden, ni uno
   más.
6. Un usuario de la organización A no puede acceder a ningún dato de la B por
   ningún endpoint.
7. La app nativa compila para iOS y Android sin ningún componente de pago en el
   bundle.
8. Todo el sistema funciona con todos los planes a precio 0.
9. Una organización que agota su prueba cae al plan gratuito conservando sus
   alertas configuradas, y al contratar después se reactivan sin intervención.
10. Cambiar la duración de la prueba de 14 a 30 días, o desactivarla por
    completo, no requiere tocar código ni desplegar.

---

## 14. Lo que NO hay que construir

Escrito explícitamente porque la tentación de añadirlo será constante:

- **No** un producto de facturación. No se emite ni valida ninguna factura real
  de ningún cliente.
- **No** un chatbot sobre normativa. Todo el mundo lo va a sugerir. El valor
  está en la vigilancia continua y la fiabilidad, no en conversar.
- **No** publicación automática sin revisión humana, por muy bueno que parezca
  el borrador del modelo.
- **No** multi-tenancy con bases de datos separadas. Una base, filtrado por
  organización.
- **No** microservicios. Un monolito modular hasta que haya clientes que
  justifiquen otra cosa.
- **No** app móvil antes de la fase 6.
- **No** más países hasta que España y Francia funcionen y haya alguien pagando.

---

## 15. Primera instrucción concreta

Empieza por la **Fase 1**. Propón:

1. El esquema completo de base de datos como migración inicial de Alembic.
2. La estructura de directorios del proyecto.
3. La interfaz abstracta `Collector` y la implementación `HTTPFileCollector`.
4. El parser de XSD a `XSD_ELEMENTS`.
5. El detector de diffs con la tabla de clasificación del apartado 7.3, con
   tests sobre pares de XSD sintéticos que cubran los once tipos de cambio.

No escribas nada hasta haber propuesto el desglose y recibido confirmación.
