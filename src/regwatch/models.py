"""Punto unico de importacion de todos los modelos.

Alembic necesita que todas las tablas esten registradas en `Base.metadata` antes de
autogenerar. Importar desde aqui evita que una tabla se quede fuera de una migracion
solo porque nadie importo su modulo.
"""

from regwatch.accounts.models import (
    AuditLog,
    BlogPost,
    Invitation,
    Organization,
    Plan,
    TrialConfig,
    User,
)
from regwatch.alerts.models import (
    AlertSubscription,
    NotificationLog,
    NotificationPreference,
    PipelineIncident,
    WebhookEndpoint,
)
from regwatch.catalog.models import DocumentType, Jurisdiction, RegulationFamily, Source
from regwatch.changes.models import ChangeEvent, ChangeNote
from regwatch.core.db import Base
from regwatch.ingest.models import Artifact, NormalizedForm

__all__ = [
    "AlertSubscription",
    "Artifact",
    "AuditLog",
    "Base",
    "BlogPost",
    "ChangeEvent",
    "ChangeNote",
    "DocumentType",
    "Invitation",
    "Jurisdiction",
    "NormalizedForm",
    "NotificationLog",
    "NotificationPreference",
    "Organization",
    "PipelineIncident",
    "Plan",
    "RegulationFamily",
    "Source",
    "TrialConfig",
    "User",
    "WebhookEndpoint",
]
