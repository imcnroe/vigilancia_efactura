"""Artefactos y formas normalizadas.

Ninguna de estas dos tablas lleva borrado logico ni se actualiza nunca. El apartado 1
del contexto dice que nunca se borra un artefacto descargado; aqui eso lo garantiza un
trigger, no la disciplina del equipo.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from regwatch.catalog.models import Source
from regwatch.core.db import Base, TimestampMixin, enum_column, pk
from regwatch.core.enums import FormType


class Artifact(Base, TimestampMixin):
    """Cada captura concreta de una fuente. Inmutable."""

    __tablename__ = "artifact"
    __table_args__ = (
        # Si el hash no cambia no se crea artefacto: solo se toca `last_checked_at`.
        Index("uq_artifact_source_id_content_hash", "source_id", "content_hash", unique=True),
        # La consulta caliente: el ultimo artefacto de una fuente.
        Index("ix_artifact_source_id_captured_at", "source_id", text("captured_at DESC")),
        CheckConstraint("length(content_hash) = 64", name="content_hash_is_sha256"),
    )

    id: Mapped[uuid.UUID] = pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("source.id"), nullable=False
    )
    captured_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: En HTTP_HTML_INDEX la URL descargada no es la de `source.url`.
    fetched_url: Mapped[str] = mapped_column(Text, nullable=False)

    #: Version que declara el propio documento. Nula si no se detecta; nunca inferida.
    declared_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    source: Mapped[Source] = relationship()


class NormalizedForm(Base, TimestampMixin):
    """Representacion comparable de un artefacto."""

    __tablename__ = "normalized_form"
    __table_args__ = (
        # `parser_version` va en la clave: reprocesar el historico con un parser
        # mejorado tiene que convivir con la forma anterior, no destruirla.
        Index(
            "uq_normalized_form_artifact_id_form_type_parser_version",
            "artifact_id",
            "form_type",
            "parser_version",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("artifact.id"), nullable=False
    )
    form_type: Mapped[str] = enum_column(FormType, "form_type")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Dependencias sin resolver. No aborta el analisis: lo marca.
    is_partial: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    parse_warnings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    #: Ruta dentro del artefacto cuando este es un contenedor. El paquete frances es un
    #: ZIP con varios XSD dentro, y cada uno genera su propia forma.
    inner_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    artifact: Mapped[Artifact] = relationship()
