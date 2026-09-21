# Migración 001 — Esquema completo

Propuesta de bloque 1 de la Fase 1. Pendiente de confirmación antes de escribir código.

---

## 0. Convenciones aplicadas a todas las tablas

- **PK**: `id UUID` generado en Python como **UUIDv7** (ordenable por tiempo). No se
  usa `gen_random_uuid()` en la base: el valor se conoce antes del `INSERT`, lo que
  simplifica los servicios que insertan padre e hijos en la misma transacción.
- **Tiempos**: `created_at` y `updated_at`, ambos `TIMESTAMPTZ NOT NULL DEFAULT now()`.
  `updated_at` lo mantiene un trigger genérico `touch_updated_at()`, no la aplicación.
- **Borrado lógico**: `deleted_at TIMESTAMPTZ NULL` solo en entidades de negocio
  (organización, usuario, fuente, suscripción de alerta). **Nunca** en `artifact`,
  `normalized_form`, `change_event`, `notification_log` ni `audit_log`.
- **Enumerados**: `VARCHAR` + `CHECK`, no tipos `ENUM` de Postgres. Añadir un valor a
  un `ENUM` en producción es una operación incómoda y estos van a crecer.
- **i18n**: `JSONB` con forma `{"es": "...", "fr": "...", "en": "..."}`. Un helper en
  `core/i18n.py` resuelve con fallback y marca el idioma real devuelto.
- **Extensiones requeridas**: `citext` (emails), `pg_trgm` (búsqueda). No hace falta
  `pgcrypto` ni `uuid-ossp`.

---

## 1. Catálogo normativo

### `jurisdiction`
| Columna | Tipo | Notas |
|---|---|---|
| `code` | `VARCHAR(12)` | Único. `ES`, `ES-BI`, `ES-SS`, `ES-VI`, `ES-NA`, `FR` |
| `name_i18n` | `JSONB` | |
| `country_code` | `CHAR(2)` | ISO 3166-1 |
| `parent_id` | `UUID` FK autoref. | Bizkaia cuelga de `ES` |
| `is_active` | `BOOL` | |

Índice sobre `parent_id`. La herencia del apartado 8 se resuelve con un `WITH RECURSIVE`
sobre esta columna; a esta escala no compensa un `ltree`.

### `regulation_family`
`jurisdiction_id` FK · `code` · `name_i18n` · `description_i18n` · `authority` ·
`official_url`
Único sobre `(jurisdiction_id, code)`.

### `document_type`
`regulation_family_id` FK · `code` · `name_i18n`
Único sobre `(regulation_family_id, code)`.

### `source`
| Columna | Tipo | Notas |
|---|---|---|
| `regulation_family_id` | FK | |
| `document_type_id` | FK nullable | |
| `name`, `url` | `TEXT` | |
| `source_kind` | `VARCHAR` | `SCHEMA` \| `INDEX` \| `NARRATIVE` \| `SANDBOX` |
| `collector_type` | `VARCHAR` | `HTTP_FILE` \| `HTTP_HTML_INDEX` \| `PLAYWRIGHT` \| `RSS` \| `API` |
| `collector_config` | `JSONB NOT NULL DEFAULT '{}'` | Selectores, patrones, cabeceras |
| `check_frequency` | `TEXT` | Expresión cron |
| **`next_check_at`** | `TIMESTAMPTZ` | **Añadido al doc.** Ver nota |
| `priority` | `VARCHAR` | `HOT` \| `WARM` \| `COLD` |
| `last_checked_at`, `last_success_at` | `TIMESTAMPTZ` | |
| `consecutive_failures` | `INT NOT NULL DEFAULT 0` | |
| `is_active` | `BOOL` | |
| `notes` | `TEXT` | |

**Sobre `next_check_at`**: el cron no se puede evaluar en SQL, así que `collect run-due`
necesita una columna materializada. Se recalcula al terminar cada ejecución. Índice
parcial: `(next_check_at) WHERE is_active AND deleted_at IS NULL`.

También guardo en `collector_config` el `etag` y el `last_modified` de la última
respuesta, para las peticiones condicionales del `HTTPFileCollector`.

---

## 2. Artefactos

### `artifact` — inmutable
| Columna | Tipo | Notas |
|---|---|---|
| `source_id` | FK | |
| `captured_at` | `TIMESTAMPTZ` | |
| `content_hash` | `CHAR(64)` | SHA-256 en hex |
| `storage_key` | `TEXT` | `sha256/ab/cd/<hash>` |
| `mime_type`, `size_bytes` | | |
| `original_filename` | `TEXT` | |
| **`fetched_url`** | `TEXT` | **Añadido.** En `HTTP_HTML_INDEX` la URL descargada no es la de `source.url` |
| `declared_version` | `TEXT NULL` | Nulo si no se detecta; nunca inferido |
| `http_status` | `INT` | |
| `http_headers` | `JSONB` | |

- Único sobre `(source_id, content_hash)`.
- Índice sobre `(source_id, captured_at DESC)`: es la consulta caliente («último
  artefacto de esta fuente»).
- **Trigger `BEFORE UPDATE OR DELETE` que lanza excepción.** El apartado 1 dice que
  nunca se borra un artefacto; conviene que la base lo garantice, no la disciplina.

Nota: la clave está direccionada por contenido, así que dos fuentes que descarguen el
mismo fichero comparten objeto en S3 y tienen dos filas en `artifact`. Es lo correcto,
pero implica que **borrar un artefacto no puede borrar nunca el objeto**.

### `normalized_form`
`artifact_id` FK · `form_type` (`XSD_ELEMENTS` \| `CODE_LIST` \| `TEXT_BLOCKS` \|
`INDEX_ENTRIES`) · `payload JSONB` · `parser_version` · `is_partial BOOL` ·
`parse_warnings JSONB`

Único sobre **`(artifact_id, form_type, parser_version)`**, no sobre los dos primeros.
Sin el `parser_version` en la clave, reprocesar el histórico destruye la forma anterior
y pierdes la comparación entre parsers, que es justo lo que el apartado 7.2 quiere
permitir.

---

## 3. Cambios

### `change_event`
`source_id` · `artifact_from_id` · `artifact_to_id` · `detected_at` ·
`structural_diff JSONB` · **`diff_schema_version`** · `severity_suggested` ·
`status` (`DETECTED` \| `UNDER_REVIEW` \| `PUBLISHED` \| `DISCARDED`) · `discard_reason`

- Único sobre `(artifact_from_id, artifact_to_id)`: hace idempotente el reproceso.
- `CHECK (status <> 'DISCARDED' OR discard_reason IS NOT NULL)`.
- Índice parcial sobre `(detected_at) WHERE status = 'DETECTED'` para la bandeja.
- `diff_schema_version` permite evolucionar el formato del JSONB sin romper lo guardado.

### `change_note`
`change_event_id` FK **único** · `severity` · `title_i18n` · `summary_i18n` ·
`impact_i18n` · `action_required_i18n` · `effective_date` · `effective_date_note` ·
`affected_fields JSONB` · `xml_example_before` · `xml_example_after` ·
`source_reference` · `llm_draft JSONB` · `reviewed_by_user_id` FK · `reviewed_at` ·
`published_at` · `is_public`

**La restricción del apartado 4.2, en la base:**

```
CHECK (status <> 'PUBLISHED' OR (reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL))
```

Con la migración completa esto se puede crear ya, porque `user` existe en la misma
migración. Es la única forma de que sea imposible saltárselo «incluso por API».

Columna generada `search_vector TSVECTOR` con índice GIN, sobre la concatenación de los
tres idiomas con configuración `simple`. No uso `spanish`/`french` todavía: el stemming
por idioma exige un vector por idioma y a este volumen no compensa.

---

## 4. Organizaciones, usuarios y planes

### `plan`
`code` único · `name_i18n` · `price_monthly` y `price_yearly` `NUMERIC(10,2)` ·
`currency` · `limits JSONB` · `is_public`

`limits` con claves `max_users`, `max_jurisdictions`, `webhooks_enabled`, `api_access`,
`history_days`, `sandbox_alerts`. Se valida contra un esquema Pydantic al escribir, no
con un `CHECK`.

### `organization`
`name` · `vat_number` · `country_code` · `billing_email` · `plan_id` FK ·
`subscription_status` (`TRIALING` \| `ACTIVE` \| `PAST_DUE` \| `CANCELED` \| `EXPIRED`) ·
`payment_provider` · `payment_customer_id` · `payment_subscription_id` ·
`trial_plan_id` FK · `trial_started_at` · `trial_ends_at` · `trial_extension_days` ·
`trial_extended_by_user_id` FK · `has_used_trial BOOL NOT NULL DEFAULT false` ·
`signup_email_domain` · `signup_ip INET` · `is_internal BOOL NOT NULL DEFAULT false`

### `user`
`organization_id` FK · `email CITEXT` único · `password_hash` · `full_name` · `locale` ·
`timezone` · `role` · `email_verified_at` · `last_login_at` · `mfa_secret` ·
`mfa_enabled`

`CHECK` sobre `role` con los seis valores del apartado 5. Los tres internos solo son
válidos si la organización es interna, pero eso es una regla entre tablas: va en el
servicio con un test, no en un `CHECK`.

`CITEXT` en vez de índice sobre `lower(email)`: la comparación insensible se aplica en
todas las consultas sin que nadie tenga que acordarse.

### `invitation`
`organization_id` · `email` · `role` · `token` único · `expires_at` · `accepted_at`

### `trial_config` — fila única
`enabled` · `duration_days` (14) · `trial_plan_code` (`PRO`) · `requires_card` (false) ·
`fallback_plan_code` (`FREE`)

Singleton forzado con `CREATE UNIQUE INDEX ON trial_config ((true))`.

**FKs circulares**: `organization.trial_extended_by_user_id` → `user` y
`user.organization_id` → `organization`. Se crean como `DEFERRABLE INITIALLY DEFERRED`
al final de la migración, después de ambas tablas.

---

## 5. Alertas

### `alert_subscription`
`user_id` FK · `min_severity` · `channels JSONB` · `is_active` ·
**`inactive_reason`**

**Discrepancia con el documento.** El apartado 4.4 define el ámbito como
`scope_type` + `scope_id` polimórfico. Eso impide poner una FK real y garantiza que
antes o después haya suscripciones apuntando a fuentes borradas. Propongo en su lugar
cuatro columnas nullables con FK auténtica:

```
jurisdiction_id, regulation_family_id, document_type_id, source_id
CHECK (num_nonnulls(jurisdiction_id, regulation_family_id, document_type_id, source_id) = 1)
```

Se conserva `scope_type` como columna derivada para simplificar consultas. Cuesta tres
columnas y evita la clase de bug más silenciosa del apartado 8.

`inactive_reason` es lo que exige el punto 2 de «Al terminar» de la 9.4: la suscripción
que excede el plan gratuito se desactiva con motivo visible y se reactiva sola al
contratar.

### `notification_preference`
`user_id` único · `digest_frequency` · `digest_hour` · `digest_weekday` ·
`blocking_bypasses_digest BOOL NOT NULL DEFAULT true`

### `webhook_endpoint`
`organization_id` · `url` · `secret` · `is_active` · `last_delivery_at` ·
`consecutive_failures`

### `notification_log`
`user_id` · `change_note_id` · `channel` · `sent_at` · `status` · `error`

Único sobre `(user_id, change_note_id, channel)`. Es lo que hace que el motor de
notificaciones sea idempotente y no duplique envíos si se reintenta.

---

## 6. Operación y contenido

### `pipeline_incident`
`source_id` · `kind` · `opened_at` · `resolved_at` · `detail JSONB`
Abierta por el heartbeat cuando `consecutive_failures >= 2`. En fase 1 solo se escribe;
la fase 2 la pinta.

### `audit_log`
`actor_user_id` · `action` · `entity_type` · `entity_id` · `reason` · `payload JSONB` ·
`ip INET` · `created_at`
Requerida por 9.4 (extensión manual de prueba, con motivo obligatorio) y por 6.3
(suplantación de identidad). Sin `updated_at` ni borrado.

### `blog_post`
`slug` único · `title_i18n` · `body_md_i18n` · `published_at` · `is_public`
Del apartado 6.1. No la toca nadie hasta la fase 5, pero entra en la migración completa.

---

## 7. Orden de creación en la migración

1. Extensiones (`citext`, `pg_trgm`) y función `touch_updated_at()`
2. `plan`, `trial_config`
3. `jurisdiction` → `regulation_family` → `document_type` → `source`
4. `organization` y `user` (FKs cruzadas diferidas al final)
5. `invitation`
6. `artifact` → `normalized_form`
7. `change_event` → `change_note`
8. `alert_subscription`, `notification_preference`, `webhook_endpoint`,
   `notification_log`
9. `pipeline_incident`, `audit_log`, `blog_post`
10. FKs diferidas, triggers de `updated_at`, trigger de inmutabilidad de `artifact`,
    índices GIN de búsqueda

Datos semilla en una migración aparte (`002_seed.py`), no en la 001: los tres planes y
las jurisdicciones de España y Francia. Así se puede recrear el esquema vacío para tests
sin arrastrar datos.

---

## 8. Decisiones abiertas

1. **`alert_subscription`**: ¿cuatro FK nullables (mi propuesta) o el polimórfico del
   documento?
2. **Trigger de inmutabilidad en `artifact`**: incómodo si alguna vez hay que corregir
   una fila a mano. ¿Lo pongo, o basta con no exponer el `UPDATE` en el código?
3. **`search_vector` ahora o en la fase 3**: no cuesta nada dejarlo, pero es una
   columna generada que nadie consulta hasta el explorador de cambios.
