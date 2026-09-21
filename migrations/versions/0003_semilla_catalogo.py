"""semilla del catalogo normativo, planes y configuracion de prueba

Los datos van en una migracion aparte de la 001 a proposito: asi se puede recrear el
esquema vacio para tests sin arrastrar datos.

Que entra aqui y que no:

- **Si**: jurisdicciones, familias normativas, tipos de documento, planes y la fila
  unica de `trial_config`. Son el andamiaje que el catalogo necesita para existir, y
  cambian una vez al año.
- **No**: las `source`. Esas son el producto curado (apartado 1) y las gestiona el
  operador con `regwatch source add`, no un despliegue. Una fuente cuya configuracion
  de colector solo se pueda tocar escribiendo una migracion es una fuente que nadie
  ajusta.

Los identificadores van como literales fijos, no generados al ejecutar: son UUIDv7
validos con un instante de referencia comun, de modo que todos los entornos comparten
los mismos identificadores de catalogo y la migracion es reproducible. El codigo de la
aplicacion nunca los usa directamente —busca por `code`, que es unico—, pero comparar
dos bases o rastrear un dato entre entornos deja de ser un ejercicio de adivinanza.

`official_url` se rellena **solo** con las URLs de nivel A del catalogo semilla, las
descargadas e inspeccionadas a mano. Las demas quedan nulas: el apartado 0 prohibe
inventar URLs de organismos oficiales, y una URL plausible pero sin verificar es peor
que ninguna porque nadie vuelve a comprobarla.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: Los identificadores viajan como cadenas, y Postgres no compara `uuid` con `varchar`:
#: sin este tipo explicito, el parametro se enlaza como texto y el INSERT falla.
_UUID = postgresql.UUID(as_uuid=False)


# -- identificadores ------------------------------------------------------------
# UUIDv7 con instante de referencia 2025-09-10T00:00:00Z.

J_ES = "019930ec-0c00-7fb3-ab66-ffc86f38d952"
J_ES_VI = "019930ec-0c00-7e1a-b273-ff34fce19d6b"
J_ES_BI = "019930ec-0c00-71cd-9e3a-265e16eee03f"
J_ES_SS = "019930ec-0c00-781d-8085-62bedb8b60ce"
J_ES_NA = "019930ec-0c00-7c89-b777-d4dd1fc61c6f"
J_FR = "019930ec-0c00-7cb4-927d-e37b942baad0"
J_EU = "019930ec-0c00-7fdb-8011-776e8db7cd33"

F_ES_VERIFACTU = "019930ec-0c00-7409-a99b-e42c8a8e46fb"
F_ES_SII = "019930ec-0c00-7189-8232-cdd221771294"
F_ES_FACTURAE = "019930ec-0c00-7560-9e27-de7ced00ff1c"
F_ES_B2B = "019930ec-0c00-7913-9c15-364204a80fe8"
F_ES_FACE = "019930ec-0c00-7f20-ab26-aecb47d2868c"
F_VI_TBAI = "019930ec-0c00-7d47-9431-df5d7f141cbe"
F_BI_TBAI = "019930ec-0c00-7f6e-a35f-04dc8c462986"
F_BI_BATUZ = "019930ec-0c00-749e-a891-e224136950ff"
F_SS_TBAI = "019930ec-0c00-78a7-ba65-98d732768f7c"
F_NA = "019930ec-0c00-75fb-b6d1-9c7a5b1ee83b"
F_FR_EINV = "019930ec-0c00-748d-940f-1504cd17100c"
F_FR_EREP = "019930ec-0c00-7b25-999f-c203d176a301"
F_FR_ANNUAIRE = "019930ec-0c00-7003-b1b2-1cb527d7fa3d"
F_FR_PA = "019930ec-0c00-7728-b8f7-48a1d6eaf726"
F_FR_CHORUS = "019930ec-0c00-7d2d-a612-125fb3a0daec"
F_EU_PEPPOL = "019930ec-0c00-717c-bec7-eb32f30b90cd"

D_FACTURAE_32 = "019930ec-0c00-7d7e-a30d-7e25dd8a49f1"
D_FACTURAE_321 = "019930ec-0c00-78d5-a069-e9d1e79ca924"
D_FACTURAE_322 = "019930ec-0c00-7e95-a873-cd771f2c446d"
D_FACTUR_X = "019930ec-0c00-7e71-8ab0-8cac7457e911"
D_UBL_INVOICE = "019930ec-0c00-7c19-b1cd-97404156226e"
D_CII_INVOICE = "019930ec-0c00-7786-9706-d51d39f66711"

P_FREE = "019930ec-0c00-744d-9aaa-6cc23891b40c"
P_PRO = "019930ec-0c00-792d-a0c5-8c9d8bfb6ff1"
P_TEAM = "019930ec-0c00-7c78-b3e8-a81b697b7585"
T_CONFIG = "019930ec-0c00-7a72-9c18-0c6279b0b02a"


# -- URLs de nivel A del catalogo semilla ---------------------------------------
# Descargadas e inspeccionadas el 8 de septiembre de 2026. Ver
# docs/catalogo-fuentes-semilla.md.

URL_FACTURAE = "https://www.facturae.gob.es/formato/ultima-version"
URL_VERIFACTU = (
    "https://sede.agenciatributaria.gob.es/Sede/iva/"
    "sistemas-informaticos-facturacion-verifactu/informacion-tecnica/esquemas.html"
)


# -- tablas, declaradas aqui para que la migracion no dependa de los modelos -----


def _jurisdiction_table() -> sa.Table:
    return sa.table(
        "jurisdiction",
        sa.column("id", postgresql.UUID(as_uuid=False)),
        sa.column("code", sa.String),
        sa.column("name_i18n", postgresql.JSONB),
        sa.column("country_code", sa.String),
        sa.column("parent_id", postgresql.UUID(as_uuid=False)),
        sa.column("is_active", sa.Boolean),
    )


def _family_table() -> sa.Table:
    return sa.table(
        "regulation_family",
        sa.column("id", postgresql.UUID(as_uuid=False)),
        sa.column("jurisdiction_id", postgresql.UUID(as_uuid=False)),
        sa.column("code", sa.String),
        sa.column("name_i18n", postgresql.JSONB),
        sa.column("description_i18n", postgresql.JSONB),
        sa.column("authority", sa.String),
        sa.column("official_url", sa.Text),
    )


def _document_type_table() -> sa.Table:
    return sa.table(
        "document_type",
        sa.column("id", postgresql.UUID(as_uuid=False)),
        sa.column("regulation_family_id", postgresql.UUID(as_uuid=False)),
        sa.column("code", sa.String),
        sa.column("name_i18n", postgresql.JSONB),
    )


def _plan_table() -> sa.Table:
    return sa.table(
        "plan",
        sa.column("id", postgresql.UUID(as_uuid=False)),
        sa.column("code", sa.String),
        sa.column("name_i18n", postgresql.JSONB),
        sa.column("price_monthly", sa.Numeric),
        sa.column("price_yearly", sa.Numeric),
        sa.column("currency", sa.String),
        sa.column("limits", postgresql.JSONB),
        sa.column("is_public", sa.Boolean),
    )


# -- datos -----------------------------------------------------------------------

#: Las tres haciendas forales y Navarra son jurisdicciones propias colgando de `ES`
#: (apartado 2): comparten el esquema TicketBAI pero no la obligatoriedad de sus
#: campos, y esa es la diferencia que el producto tiene que saber contar.
#:
#: `EU` existe para Peppol, que es transversal a ambos paises. La alternativa era
#: duplicar la familia en `ES` y en `FR`, y entonces un cambio en una lista de codigos
#: generaria dos avisos identicos y nadie podria suscribirse a Peppol sin suscribirse a
#: un pais. Los nombres de las forales van igual en los tres idiomas: son los toponimos
#: oficiales que usan las propias diputaciones.
JURISDICTIONS: list[dict[str, object]] = [
    {
        "id": J_ES,
        "code": "ES",
        "name_i18n": {"es": "España", "fr": "Espagne", "en": "Spain"},
        "country_code": "ES",
        "parent_id": None,
        "is_active": True,
    },
    {
        "id": J_ES_VI,
        "code": "ES-VI",
        "name_i18n": {"es": "Álava", "fr": "Álava", "en": "Álava"},
        "country_code": "ES",
        "parent_id": J_ES,
        "is_active": True,
    },
    {
        "id": J_ES_BI,
        "code": "ES-BI",
        "name_i18n": {"es": "Bizkaia", "fr": "Bizkaia", "en": "Bizkaia"},
        "country_code": "ES",
        "parent_id": J_ES,
        "is_active": True,
    },
    {
        "id": J_ES_SS,
        "code": "ES-SS",
        "name_i18n": {"es": "Gipuzkoa", "fr": "Gipuzkoa", "en": "Gipuzkoa"},
        "country_code": "ES",
        "parent_id": J_ES,
        "is_active": True,
    },
    {
        "id": J_ES_NA,
        "code": "ES-NA",
        "name_i18n": {"es": "Navarra", "fr": "Navarre", "en": "Navarre"},
        "country_code": "ES",
        "parent_id": J_ES,
        "is_active": True,
    },
    {
        "id": J_FR,
        "code": "FR",
        "name_i18n": {"es": "Francia", "fr": "France", "en": "France"},
        "country_code": "FR",
        "parent_id": None,
        "is_active": True,
    },
    {
        "id": J_EU,
        "code": "EU",
        "name_i18n": {"es": "Unión Europea", "fr": "Union européenne", "en": "European Union"},
        "country_code": "EU",
        "parent_id": None,
        "is_active": True,
    },
]

#: `authority` se rellena solo donde el organismo esta confirmado. Facturae lo mantienen
#: varios ministerios y el catalogo semilla no lo concreta, asi que queda nulo en vez de
#: adivinado.
FAMILIES: list[dict[str, object]] = [
    # -- España ----------------------------------------------------------------
    {
        "id": F_ES_VERIFACTU,
        "jurisdiction_id": J_ES,
        "code": "VERIFACTU",
        "name_i18n": {
            "es": "Verifactu / RRSIF",
            "fr": "Verifactu / RRSIF",
            "en": "Verifactu / RRSIF",
        },
        "description_i18n": {
            "es": "Reglamento de requisitos de los sistemas informáticos de facturación.",
            "fr": "Règlement sur les exigences des systèmes informatiques de facturation.",
            "en": "Regulation on requirements for invoicing software systems.",
        },
        "authority": "AEAT",
        "official_url": URL_VERIFACTU,
    },
    {
        "id": F_ES_SII,
        "jurisdiction_id": J_ES,
        "code": "SII",
        "name_i18n": {
            "es": "SII (Suministro Inmediato de Información)",
            "fr": "SII (fourniture immédiate d'informations)",
            "en": "SII (Immediate Supply of Information)",
        },
        "description_i18n": None,
        "authority": "AEAT",
        "official_url": None,
    },
    {
        "id": F_ES_FACTURAE,
        "jurisdiction_id": J_ES,
        "code": "FACTURAE",
        "name_i18n": {"es": "Facturae", "fr": "Facturae", "en": "Facturae"},
        "description_i18n": {
            "es": "Formato de factura electrónica de la Administración española.",
            "fr": "Format de facture électronique de l'administration espagnole.",
            "en": "Electronic invoice format of the Spanish administration.",
        },
        "authority": None,
        "official_url": URL_FACTURAE,
    },
    {
        "id": F_ES_B2B,
        "jurisdiction_id": J_ES,
        "code": "B2B_CREA_CRECE",
        "name_i18n": {
            "es": "Factura electrónica B2B obligatoria",
            "fr": "Facture électronique B2B obligatoire",
            "en": "Mandatory B2B electronic invoicing",
        },
        "description_i18n": {
            "es": "Desarrollo reglamentario de la Ley Crea y Crece.",
            "fr": "Règlement d'application de la loi « Crea y Crece ».",
            "en": "Implementing regulation of the Crea y Crece Act.",
        },
        "authority": None,
        "official_url": None,
    },
    {
        "id": F_ES_FACE,
        "jurisdiction_id": J_ES,
        "code": "FACE",
        "name_i18n": {"es": "FACe / FACeB2B", "fr": "FACe / FACeB2B", "en": "FACe / FACeB2B"},
        "description_i18n": {
            "es": "Punto general de entrada de facturas del sector público.",
            "fr": "Point d'entrée général des factures du secteur public.",
            "en": "General entry point for public sector invoices.",
        },
        "authority": None,
        "official_url": None,
    },
    # -- haciendas forales -----------------------------------------------------
    {
        "id": F_VI_TBAI,
        "jurisdiction_id": J_ES_VI,
        "code": "TICKETBAI",
        "name_i18n": {"es": "TicketBAI", "fr": "TicketBAI", "en": "TicketBAI"},
        "description_i18n": None,
        "authority": "Diputación Foral de Álava",
        "official_url": None,
    },
    {
        "id": F_BI_TBAI,
        "jurisdiction_id": J_ES_BI,
        "code": "TICKETBAI",
        "name_i18n": {"es": "TicketBAI", "fr": "TicketBAI", "en": "TicketBAI"},
        "description_i18n": None,
        "authority": "Diputación Foral de Bizkaia",
        "official_url": None,
    },
    {
        # Batuz es un regimen propio de Bizkaia, no una variante de TicketBAI: el LROE
        # tiene sus propios esquemas. Familia aparte para que un cambio en el LROE no
        # se mezcle con uno de TicketBAI.
        "id": F_BI_BATUZ,
        "jurisdiction_id": J_ES_BI,
        "code": "BATUZ_LROE",
        "name_i18n": {
            "es": "Batuz — LROE",
            "fr": "Batuz — LROE",
            "en": "Batuz — LROE",
        },
        "description_i18n": {
            "es": "Libro registro de operaciones económicas.",
            "fr": "Registre des opérations économiques.",
            "en": "Register of economic transactions.",
        },
        "authority": "Diputación Foral de Bizkaia",
        "official_url": None,
    },
    {
        "id": F_SS_TBAI,
        "jurisdiction_id": J_ES_SS,
        "code": "TICKETBAI",
        "name_i18n": {"es": "TicketBAI", "fr": "TicketBAI", "en": "TicketBAI"},
        "description_i18n": None,
        "authority": "Diputación Foral de Gipuzkoa",
        "official_url": None,
    },
    {
        # El apartado 2 lista Navarra como familia sin concretar el regimen tecnico.
        # Queda dada de alta para poder colgarle fuentes; el nombre definitivo se ajusta
        # cuando se verifique.
        "id": F_NA,
        "jurisdiction_id": J_ES_NA,
        "code": "NAVARRA",
        "name_i18n": {
            "es": "Facturación electrónica en Navarra",
            "fr": "Facturation électronique en Navarre",
            "en": "Electronic invoicing in Navarre",
        },
        "description_i18n": {
            "es": "Régimen técnico pendiente de concretar.",
            "fr": "Régime technique à préciser.",
            "en": "Technical regime yet to be confirmed.",
        },
        "authority": "Hacienda Foral de Navarra",
        "official_url": None,
    },
    # -- Francia ---------------------------------------------------------------
    {
        "id": F_FR_EINV,
        "jurisdiction_id": J_FR,
        "code": "FR_EINVOICING",
        "name_i18n": {
            "es": "Reforma de la facturación electrónica",
            "fr": "Réforme de la facturation électronique",
            "en": "Electronic invoicing reform",
        },
        "description_i18n": {
            "es": "Especificaciones externas B2B.",
            "fr": "Spécifications externes B2B.",
            "en": "External B2B specifications.",
        },
        "authority": "DGFiP",
        "official_url": None,
    },
    {
        "id": F_FR_EREP,
        "jurisdiction_id": J_FR,
        "code": "E_REPORTING",
        "name_i18n": {"es": "E-reporting", "fr": "E-reporting", "en": "E-reporting"},
        "description_i18n": None,
        "authority": "DGFiP",
        "official_url": None,
    },
    {
        "id": F_FR_ANNUAIRE,
        "jurisdiction_id": J_FR,
        "code": "ANNUAIRE",
        "name_i18n": {
            "es": "Directorio central",
            "fr": "Annuaire",
            "en": "Central directory",
        },
        "description_i18n": None,
        "authority": "DGFiP",
        "official_url": None,
    },
    {
        "id": F_FR_PA,
        "jurisdiction_id": J_FR,
        "code": "PA_REGISTRY",
        "name_i18n": {
            "es": "Registro de plataformas autorizadas",
            "fr": "Registre des plateformes agréées",
            "en": "Register of accredited platforms",
        },
        "description_i18n": None,
        "authority": "DGFiP",
        "official_url": None,
    },
    {
        "id": F_FR_CHORUS,
        "jurisdiction_id": J_FR,
        "code": "CHORUS_PRO",
        "name_i18n": {"es": "Chorus Pro", "fr": "Chorus Pro", "en": "Chorus Pro"},
        "description_i18n": {
            "es": "Facturación al sector público francés.",
            "fr": "Facturation au secteur public français.",
            "en": "Invoicing to the French public sector.",
        },
        "authority": "AIFE",
        "official_url": None,
    },
    # -- transversal -----------------------------------------------------------
    {
        "id": F_EU_PEPPOL,
        "jurisdiction_id": J_EU,
        "code": "PEPPOL",
        "name_i18n": {
            "es": "Peppol BIS Billing",
            "fr": "Peppol BIS Billing",
            "en": "Peppol BIS Billing",
        },
        "description_i18n": {
            "es": "Ciclo de publicaciones semestral; afecta a ambos países.",
            "fr": "Cycle de publication semestriel ; concerne les deux pays.",
            "en": "Half-yearly release cycle; affects both countries.",
        },
        "authority": "OpenPeppol",
        "official_url": None,
    },
]

#: Solo los tipos que el apartado 2 nombra explicitamente. Verifactu y TicketBAI se
#: quedan sin tipos: sus esquemas existen, pero sus nombres no estan verificados y
#: `source.document_type_id` admite nulo, asi que se pueden vigilar igual.
DOCUMENT_TYPES: list[dict[str, object]] = [
    {
        "id": D_FACTURAE_32,
        "regulation_family_id": F_ES_FACTURAE,
        "code": "FACTURAE_32",
        "name_i18n": {"es": "Facturae 3.2", "fr": "Facturae 3.2", "en": "Facturae 3.2"},
    },
    {
        "id": D_FACTURAE_321,
        "regulation_family_id": F_ES_FACTURAE,
        "code": "FACTURAE_321",
        "name_i18n": {"es": "Facturae 3.2.1", "fr": "Facturae 3.2.1", "en": "Facturae 3.2.1"},
    },
    {
        "id": D_FACTURAE_322,
        "regulation_family_id": F_ES_FACTURAE,
        "code": "FACTURAE_322",
        "name_i18n": {"es": "Facturae 3.2.2", "fr": "Facturae 3.2.2", "en": "Facturae 3.2.2"},
    },
    {
        "id": D_FACTUR_X,
        "regulation_family_id": F_FR_EINV,
        "code": "FACTUR_X",
        "name_i18n": {"es": "Factur-X", "fr": "Factur-X", "en": "Factur-X"},
    },
    {
        "id": D_UBL_INVOICE,
        "regulation_family_id": F_FR_EINV,
        "code": "UBL_INVOICE",
        "name_i18n": {"es": "Factura UBL", "fr": "Facture UBL", "en": "UBL Invoice"},
    },
    {
        "id": D_CII_INVOICE,
        "regulation_family_id": F_FR_EINV,
        "code": "CII_INVOICE",
        "name_i18n": {"es": "Factura CII", "fr": "Facture CII", "en": "CII Invoice"},
    },
]

#: Los limites viven en datos, nunca en codigo (apartado 4.5): debe ser posible pasar de
#: gratis a pago editando registros. `null` significa sin limite.
#:
#: `max_jurisdictions` cuenta jurisdicciones **raiz**, es decir paises: si contase las
#: forales, un cliente vasco del plan gratuito gastaria su unica jurisdiccion en Bizkaia
#: y no veria nada de la AEAT. Quien lo aplique en la fase 3 tiene que resolver la
#: herencia asi.
#:
#: Los precios anuales llevan dos meses de descuento. El sistema tiene que funcionar con
#: todo a cero durante la validacion, asi que estos valores se editan sin desplegar.
PLANS: list[dict[str, object]] = [
    {
        "id": P_FREE,
        "code": "FREE",
        "name_i18n": {"es": "Gratuito", "fr": "Gratuit", "en": "Free"},
        "price_monthly": 0,
        "price_yearly": 0,
        "currency": "EUR",
        "limits": {
            "max_users": 1,
            "max_jurisdictions": 1,
            "webhooks_enabled": False,
            "api_access": False,
            "history_days": 30,
            "sandbox_alerts": False,
        },
        "is_public": True,
    },
    {
        "id": P_PRO,
        "code": "PRO",
        "name_i18n": {"es": "Pro", "fr": "Pro", "en": "Pro"},
        "price_monthly": 149,
        "price_yearly": 1490,
        "currency": "EUR",
        "limits": {
            "max_users": 5,
            "max_jurisdictions": 2,
            "webhooks_enabled": True,
            "api_access": False,
            "history_days": None,
            "sandbox_alerts": False,
        },
        "is_public": True,
    },
    {
        "id": P_TEAM,
        "code": "TEAM",
        "name_i18n": {"es": "Equipo", "fr": "Équipe", "en": "Team"},
        "price_monthly": 349,
        "price_yearly": 3490,
        "currency": "EUR",
        "limits": {
            "max_users": None,
            "max_jurisdictions": None,
            "webhooks_enabled": True,
            "api_access": True,
            "history_days": None,
            "sandbox_alerts": True,
        },
        "is_public": True,
    },
]


def upgrade() -> None:
    # Los planes primero: `organization.plan_id` los necesita, y `trial_config` los
    # nombra por codigo.
    op.bulk_insert(_plan_table(), PLANS)

    # Las jurisdicciones en dos tandas: las hijas referencian a `ES`, y aunque un
    # bulk_insert respeta el orden de la lista, separarlo lo hace evidente.
    roots = [row for row in JURISDICTIONS if row["parent_id"] is None]
    children = [row for row in JURISDICTIONS if row["parent_id"] is not None]
    op.bulk_insert(_jurisdiction_table(), roots)
    op.bulk_insert(_jurisdiction_table(), children)

    op.bulk_insert(_family_table(), FAMILIES)
    op.bulk_insert(_document_type_table(), DOCUMENT_TYPES)

    # Fila unica, con los valores por defecto del apartado 9.4. Las columnas llevan
    # server_default, asi que basta con el identificador: cambiar la duracion o el plan
    # de prueba es un UPDATE desde /admin, no un despliegue.
    op.execute(
        sa.text("INSERT INTO trial_config (id) VALUES (:id)").bindparams(
            sa.bindparam("id", value=T_CONFIG, type_=_UUID)
        )
    )


def _delete(table: str, ids: Sequence[object]) -> None:
    op.execute(
        sa.text(f"DELETE FROM {table} WHERE id IN :ids").bindparams(
            sa.bindparam("ids", value=tuple(ids), expanding=True, type_=_UUID)
        )
    )


def downgrade() -> None:
    # En orden inverso. Si alguien ha dado de alta fuentes, organizaciones o
    # suscripciones sobre esta semilla, las claves ajenas abortan el borrado: es lo
    # correcto, porque revertir la semilla no puede llevarse por delante el catalogo
    # curado, que es el activo del producto.
    _delete("trial_config", [T_CONFIG])
    _delete("document_type", [row["id"] for row in DOCUMENT_TYPES])
    _delete("regulation_family", [row["id"] for row in FAMILIES])
    _delete("jurisdiction", [r["id"] for r in JURISDICTIONS if r["parent_id"] is not None])
    _delete("jurisdiction", [r["id"] for r in JURISDICTIONS if r["parent_id"] is None])
    _delete("plan", [row["id"] for row in PLANS])
