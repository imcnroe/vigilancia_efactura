"""Catalogo normativo.

`Source` es la entidad mas importante del sistema: el producto es este catalogo
curado, y el software solo la maquinaria que lo explota.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from regwatch.core.db import Base, SoftDeleteMixin, TimestampMixin, enum_column, i18n_column, pk
from regwatch.core.enums import CollectorType, Priority, SourceKind


class Jurisdiction(Base, TimestampMixin):
    """Ambito con potestad normativa propia.

    Las tres haciendas forales vascas son tres jurisdicciones distintas colgando de
    `ES`, no una. Comparten esquema TicketBAI pero no la obligatoriedad de los campos.
    """

    __tablename__ = "jurisdiction"

    id: Mapped[uuid.UUID] = pk()
    code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    name_i18n: Mapped[dict[str, Any]] = i18n_column()
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jurisdiction.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    parent: Mapped[Jurisdiction | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Jurisdiction]] = relationship(back_populates="parent")


class RegulationFamily(Base, TimestampMixin):
    """Familia normativa dentro de una jurisdiccion (Verifactu, SII, TicketBAI...)."""

    __tablename__ = "regulation_family"
    __table_args__ = (
        Index("uq_regulation_family_jurisdiction_id_code", "jurisdiction_id", "code", unique=True),
    )

    id: Mapped[uuid.UUID] = pk()
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jurisdiction.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    name_i18n: Mapped[dict[str, Any]] = i18n_column()
    description_i18n: Mapped[dict[str, Any] | None] = i18n_column(nullable=True)
    authority: Mapped[str | None] = mapped_column(String(120), nullable=True)
    official_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    jurisdiction: Mapped[Jurisdiction] = relationship()


class DocumentType(Base, TimestampMixin):
    """Documento o formato concreto afectado (FACTURAE_322, FACTUR_X, UBL_INVOICE...)."""

    __tablename__ = "document_type"
    __table_args__ = (
        Index(
            "uq_document_type_regulation_family_id_code",
            "regulation_family_id",
            "code",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    regulation_family_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("regulation_family.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    name_i18n: Mapped[dict[str, Any]] = i18n_column()

    regulation_family: Mapped[RegulationFamily] = relationship()


class Source(Base, TimestampMixin, SoftDeleteMixin):
    """Una fuente vigilada."""

    __tablename__ = "source"
    __table_args__ = (
        # El planificador saca las fuentes vencidas por aqui. Indice parcial: las
        # inactivas y borradas no se miran nunca.
        Index(
            "ix_source_next_check_at_due",
            "next_check_at",
            postgresql_where=text("is_active AND deleted_at IS NULL"),
        ),
        Index("ix_source_regulation_family_id", "regulation_family_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    regulation_family_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("regulation_family.id"), nullable=False
    )
    document_type_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("document_type.id"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = enum_column(SourceKind, "source_kind")
    collector_type: Mapped[str] = enum_column(CollectorType, "collector_type")

    #: Selectores CSS, XPath, patrones de nombre, cabeceras. Tambien el `etag` y el
    #: `last_modified` de la ultima respuesta, para peticiones condicionales.
    collector_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    check_frequency: Mapped[str] = mapped_column(String(64), nullable=False)
    #: El cron no se puede evaluar en SQL: `collect run-due` necesita esto
    #: materializado. Se recalcula al terminar cada ejecucion.
    next_check_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    priority: Mapped[str] = enum_column(Priority, "priority", default="WARM")

    last_checked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    #: Texto libre del operador: por que existe esta fuente y que hay que mirar en ella.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    regulation_family: Mapped[RegulationFamily] = relationship()
    document_type: Mapped[DocumentType | None] = relationship()
