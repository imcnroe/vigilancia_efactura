"""Suscripciones a alertas, preferencias de notificacion, webhooks y log de envios."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from regwatch.core.db import Base, SoftDeleteMixin, TimestampMixin, enum_column, pk
from regwatch.core.enums import (
    DigestFrequency,
    IncidentKind,
    NotificationChannel,
    NotificationStatus,
    ScopeType,
    Severity,
)


class AlertSubscription(Base, TimestampMixin, SoftDeleteMixin):
    """Que quiere vigilar cada usuario.

    El contexto definia el ambito como `scope_type` + `scope_id` polimorfico. Aqui van
    cuatro columnas nulables con FK autentica y un CHECK de exactamente una: el
    polimorfismo sin FK garantiza que antes o despues haya suscripciones apuntando a
    fuentes borradas, y esa es la clase de fallo mas silenciosa del apartado 8.
    """

    __tablename__ = "alert_subscription"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(jurisdiction_id, regulation_family_id, document_type_id, source_id) = 1",
            name="exactly_one_scope",
        ),
        Index(
            "ix_alert_subscription_user_id_active", "user_id", postgresql_where=text("is_active")
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id"), nullable=False
    )

    #: Derivada de cual de las cuatro esta informada. Se mantiene para simplificar
    #: consultas y filtros de interfaz.
    scope_type: Mapped[str] = enum_column(ScopeType, "scope_type")

    jurisdiction_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jurisdiction.id"), nullable=True
    )
    regulation_family_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("regulation_family.id"), nullable=True
    )
    document_type_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("document_type.id"), nullable=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("source.id"), nullable=True
    )

    min_severity: Mapped[str] = enum_column(Severity, "min_severity", default="INFO")
    channels: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{\"email\": true}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    #: Visible en la interfaz cuando el plan no da para esta suscripcion ("requiere
    #: plan Pro"). Al contratar, se reactiva sola.
    inactive_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)


class NotificationPreference(Base, TimestampMixin):
    """Uno por usuario."""

    __tablename__ = "notification_preference"
    __table_args__ = (
        CheckConstraint("digest_hour BETWEEN 0 AND 23", name="digest_hour_in_range"),
        CheckConstraint(
            "digest_weekday IS NULL OR digest_weekday BETWEEN 0 AND 6",
            name="digest_weekday_in_range",
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id"), unique=True, nullable=False
    )
    digest_frequency: Mapped[str] = enum_column(
        DigestFrequency, "digest_frequency", default="DAILY"
    )
    digest_hour: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8"))
    digest_weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Un cambio bloqueante sale ya, aunque el usuario tenga digest semanal.
    blocking_bypasses_digest: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )


class WebhookEndpoint(Base, TimestampMixin):
    """Solo planes de pago. POST JSON firmado con HMAC-SHA256."""

    __tablename__ = "webhook_endpoint"

    id: Mapped[uuid.UUID] = pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    last_delivery_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class NotificationLog(Base):
    """Auditoria de todo lo enviado. Sin excepciones.

    Sirve para soporte ("no me llego") y para no duplicar envios: la clave unica es lo
    que hace idempotente el motor de notificaciones.
    """

    __tablename__ = "notification_log"
    __table_args__ = (
        Index(
            "uq_notification_log_user_id_change_note_id_channel",
            "user_id",
            "change_note_id",
            "channel",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id"), nullable=False
    )
    change_note_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("change_note.id"), nullable=False
    )
    channel: Mapped[str] = enum_column(NotificationChannel, "channel")
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = enum_column(NotificationStatus, "status", default="QUEUED")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PipelineIncident(Base, TimestampMixin):
    """Fallo del pipeline abierto por el heartbeat.

    El fallo silencioso de un colector es el unico riesgo existencial del producto
    (apartado 7.1), asi que queda registrado, no solo logueado.
    """

    __tablename__ = "pipeline_incident"
    __table_args__ = (
        Index(
            "ix_pipeline_incident_open",
            "source_id",
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("source.id"), nullable=True
    )
    kind: Mapped[str] = enum_column(IncidentKind, "kind")
    opened_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
