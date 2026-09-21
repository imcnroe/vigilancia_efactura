"""Cambios detectados y fichas publicables.

Aqui vive la regla innegociable del apartado 4.2: una ficha nunca pasa a `PUBLISHED`
sin revisor. Se implementa como CHECK de base de datos, no como validacion de
aplicacion, porque el contexto exige que sea imposible saltarsela **incluso por API**.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from regwatch.core.db import Base, TimestampMixin, enum_column, i18n_column, pk
from regwatch.core.enums import ChangeStatus, Severity


class ChangeEvent(Base, TimestampMixin):
    """Un cambio detectado entre dos artefactos de la misma fuente."""

    __tablename__ = "change_event"
    __table_args__ = (
        # Idempotencia del reproceso: el mismo par de artefactos no genera dos eventos.
        Index(
            "uq_change_event_artifact_from_id_artifact_to_id",
            "artifact_from_id",
            "artifact_to_id",
            unique=True,
        ),
        # La bandeja de revision solo mira los DETECTED.
        Index(
            "ix_change_event_detected_at_pending",
            "detected_at",
            postgresql_where=text("status = 'DETECTED'"),
        ),
        CheckConstraint(
            "status <> 'DISCARDED' OR discard_reason IS NOT NULL",
            name="discard_needs_a_reason",
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("source.id"), nullable=False, index=True
    )
    #: Nulo en el primer artefacto de una fuente: no hay contra que comparar.
    artifact_from_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("artifact.id"), nullable=True
    )
    artifact_to_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("artifact.id"), nullable=False
    )
    detected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: La fuente de verdad. Todo lo demas se deriva de aqui.
    structural_diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    diff_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Sugerencia del detector. El editor humano puede cambiarla en la ficha.
    severity_suggested: Mapped[str] = enum_column(Severity, "severity_suggested")

    status: Mapped[str] = enum_column(ChangeStatus, "status", default="DETECTED")
    discard_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChangeNote(Base, TimestampMixin):
    """La ficha publicable. Uno a uno con el evento, pero con ciclo editorial propio."""

    __tablename__ = "change_note"
    __table_args__ = (
        # La regla innegociable. No existe publicacion automatica.
        CheckConstraint(
            "status <> 'PUBLISHED' OR "
            "(reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND published_at IS NOT NULL)",
            name="publication_requires_human_review",
        ),
        Index(
            "ix_change_note_published_at_public",
            text("published_at DESC"),
            postgresql_where=text("status = 'PUBLISHED' AND is_public"),
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    change_event_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("change_event.id"), unique=True, nullable=False
    )
    status: Mapped[str] = enum_column(ChangeStatus, "status", default="DETECTED", index=True)
    severity: Mapped[str] = enum_column(Severity, "severity", index=True)

    title_i18n: Mapped[dict[str, Any]] = i18n_column()
    summary_i18n: Mapped[dict[str, Any] | None] = i18n_column(nullable=True)
    impact_i18n: Mapped[dict[str, Any] | None] = i18n_column(nullable=True)
    action_required_i18n: Mapped[dict[str, Any] | None] = i18n_column(nullable=True)

    #: Nula si el documento no la declara explicitamente. Prohibido inferirla.
    effective_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    effective_date_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Lista de rutas XML afectadas, extraida del diff.
    affected_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: Generados de forma programatica a partir del diff, nunca por el modelo.
    xml_example_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    xml_example_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Se conserva integro para poder auditar que dijo el modelo frente a lo que
    #: publico el humano.
    llm_draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id"), nullable=True
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: El escaparate publico: un ~20% de los cambios INFO, con retardo de 30 dias.
    is_public: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    change_event: Mapped[ChangeEvent] = relationship()
