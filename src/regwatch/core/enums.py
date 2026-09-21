"""Enumerados del dominio.

Se guardan en Postgres como VARCHAR con CHECK, no como tipos ENUM: anadir un valor a
un ENUM en produccion obliga a un ALTER TYPE fuera de transaccion, y estos van a
crecer. Cada enumerado expone `values()` para que el modelo construya el CHECK sin
duplicar la lista.
"""

from __future__ import annotations

from enum import StrEnum


class DomainEnum(StrEnum):
    """Base comun: convierte el enumerado en la tupla que espera el CHECK."""

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)


class SourceKind(DomainEnum):
    SCHEMA = "SCHEMA"
    INDEX = "INDEX"
    NARRATIVE = "NARRATIVE"
    SANDBOX = "SANDBOX"


class CollectorType(DomainEnum):
    HTTP_FILE = "HTTP_FILE"
    HTTP_HTML_INDEX = "HTTP_HTML_INDEX"
    PLAYWRIGHT = "PLAYWRIGHT"
    RSS = "RSS"
    API = "API"


class Priority(DomainEnum):
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"


class FormType(DomainEnum):
    XSD_ELEMENTS = "XSD_ELEMENTS"
    CODE_LIST = "CODE_LIST"
    TEXT_BLOCKS = "TEXT_BLOCKS"
    INDEX_ENTRIES = "INDEX_ENTRIES"


class Severity(DomainEnum):
    INFO = "INFO"
    REQUIRES_CHANGE = "REQUIRES_CHANGE"
    BLOCKING = "BLOCKING"

    def rank(self) -> int:
        """Orden para poder calcular el maximo de una lista de severidades."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.REQUIRES_CHANGE: 1,
    Severity.BLOCKING: 2,
}


class ChangeStatus(DomainEnum):
    DETECTED = "DETECTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    PUBLISHED = "PUBLISHED"
    DISCARDED = "DISCARDED"


class ChangeType(DomainEnum):
    """Los once tipos de esquema del apartado 7.3, mas los de indices de publicaciones."""

    FIELD_ADDED_OPTIONAL = "FIELD_ADDED_OPTIONAL"
    FIELD_ADDED_MANDATORY = "FIELD_ADDED_MANDATORY"
    FIELD_REMOVED = "FIELD_REMOVED"
    CARDINALITY_TIGHTENED = "CARDINALITY_TIGHTENED"
    CARDINALITY_RELAXED = "CARDINALITY_RELAXED"
    TYPE_CHANGED = "TYPE_CHANGED"
    LENGTH_RESTRICTED = "LENGTH_RESTRICTED"
    PATTERN_CHANGED = "PATTERN_CHANGED"
    ENUM_VALUE_ADDED = "ENUM_VALUE_ADDED"
    ENUM_VALUE_REMOVED = "ENUM_VALUE_REMOVED"
    ELEMENT_RENAMED = "ELEMENT_RENAMED"

    # Indices de publicaciones. No describen un cambio de esquema sino de catalogo: el
    # organismo ha publicado, retirado o retitulado algo. Viven en el JSONB de
    # `structural_diff`, no en una columna, asi que anadirlos no toca la base.
    INDEX_ENTRY_ADDED = "INDEX_ENTRY_ADDED"
    INDEX_ENTRY_REMOVED = "INDEX_ENTRY_REMOVED"
    INDEX_ENTRY_UPDATED = "INDEX_ENTRY_UPDATED"


class UserRole(DomainEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    EDITOR = "EDITOR"
    OPERATOR = "OPERATOR"
    SUPERADMIN = "SUPERADMIN"


INTERNAL_ROLES = frozenset({UserRole.EDITOR, UserRole.OPERATOR, UserRole.SUPERADMIN})


class SubscriptionStatus(DomainEnum):
    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


class ScopeType(DomainEnum):
    JURISDICTION = "JURISDICTION"
    REGULATION_FAMILY = "REGULATION_FAMILY"
    DOCUMENT_TYPE = "DOCUMENT_TYPE"
    SOURCE = "SOURCE"


class DigestFrequency(DomainEnum):
    IMMEDIATE = "IMMEDIATE"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


class NotificationChannel(DomainEnum):
    EMAIL = "EMAIL"
    PUSH = "PUSH"
    WEBHOOK = "WEBHOOK"


class NotificationStatus(DomainEnum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    FAILED = "FAILED"


class IncidentKind(DomainEnum):
    COLLECTOR_FAILING = "COLLECTOR_FAILING"
    PARSER_PARTIAL = "PARSER_PARTIAL"
    WEBHOOK_DISABLED = "WEBHOOK_DISABLED"
