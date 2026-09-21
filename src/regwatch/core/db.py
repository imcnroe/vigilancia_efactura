"""Base declarativa y convenciones compartidas por todos los modelos."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, MetaData, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from regwatch.core.enums import DomainEnum
from regwatch.core.ids import uuid7

#: Nombres deterministas para indices y restricciones. Sin esto, Alembic genera
#: nombres distintos en cada maquina y las migraciones dejan de ser reproducibles.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[dict[str, Any]]: JSONB,
    }


def pk() -> Mapped[uuid.UUID]:
    """Clave primaria UUIDv7, generada en Python.

    Se genera en la aplicacion y no con un DEFAULT de base para conocer el valor antes
    del INSERT: simplifica insertar un padre y sus hijos en la misma transaccion.
    """
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)


def created_at_column() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def updated_at_column() -> Mapped[dt.datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=func.now(),
    )


class TimestampMixin:
    """`created_at` y `updated_at`. El segundo lo mantiene un trigger, no la aplicacion."""

    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = updated_at_column()


class SoftDeleteMixin:
    """Borrado logico. Solo en entidades de negocio, nunca en artefactos ni cambios."""

    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


def enum_column(
    enum: type[DomainEnum],
    name: str,
    *,
    length: int = 32,
    nullable: bool = False,
    default: str | None = None,
    index: bool = False,
) -> Mapped[str]:
    """VARCHAR + CHECK en vez de un tipo ENUM de Postgres.

    Anadir un valor a un ENUM en produccion obliga a un ALTER TYPE fuera de
    transaccion; con un CHECK basta con reemplazar la restriccion.
    """
    values = ", ".join(f"'{value}'" for value in enum.values())
    return mapped_column(
        String(length),
        CheckConstraint(f"{name} IN ({values})", name=f"{name}_valid"),
        nullable=nullable,
        server_default=text(f"'{default}'") if default is not None else None,
        index=index,
    )


def i18n_column(nullable: bool = False) -> Mapped[dict[str, Any]]:
    """Texto por idioma: `{"es": ..., "fr": ..., "en": ...}`.

    Si falta una traduccion se muestra el original marcado, nunca cadena vacia. Eso lo
    resuelve `core.i18n`, no la base.
    """
    return mapped_column(JSONB, nullable=nullable)
