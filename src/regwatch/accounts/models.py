"""Organizaciones, usuarios, planes y configuracion del periodo de prueba.

El SaaS es B2B: se factura a la organizacion, no a la persona. La prueba tambien se
concede a la organizacion (apartado 9.4), de modo que invitar a un companero no genera
una prueba nueva.
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
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from regwatch.core.db import Base, SoftDeleteMixin, TimestampMixin, enum_column, i18n_column, pk
from regwatch.core.enums import SubscriptionStatus, UserRole


class Plan(Base, TimestampMixin):
    """Los limites viven en datos, nunca en codigo.

    Debe ser posible pasar de gratis a pago, o de por usuario a por jurisdiccion,
    editando registros y sin desplegar.
    """

    __tablename__ = "plan"

    id: Mapped[uuid.UUID] = pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name_i18n: Mapped[dict[str, Any]] = i18n_column()
    price_monthly: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    price_yearly: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'EUR'"))

    #: max_users, max_jurisdictions, webhooks_enabled, api_access, history_days,
    #: sandbox_alerts. Se valida contra un esquema Pydantic al escribir, no con CHECK.
    limits: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: Falso para planes a medida que no se listan en la pagina de precios.
    is_public: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))


class Organization(Base, TimestampMixin, SoftDeleteMixin):
    """La unidad que contrata."""

    __tablename__ = "organization"

    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vat_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    billing_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    plan_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("plan.id"), nullable=False
    )
    subscription_status: Mapped[str] = enum_column(
        SubscriptionStatus, "subscription_status", default="TRIALING", index=True
    )

    payment_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # -- prueba (apartado 9.4) -------------------------------------------------
    trial_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("plan.id"), nullable=True
    )
    trial_started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_ends_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    trial_extension_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    trial_extended_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("user_account.id", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    #: Se pone a true al iniciar la prueba y no se revierte jamas de forma automatica.
    has_used_trial: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    # -- antiabuso minimo, sin paranoia ----------------------------------------
    signup_email_domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    signup_ip: Mapped[str | None] = mapped_column(INET, nullable=True, index=True)

    #: Las cuentas del equipo del servicio viven en una organizacion marcada asi.
    is_internal: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    plan: Mapped[Plan] = relationship(foreign_keys=[plan_id])


class User(Base, TimestampMixin, SoftDeleteMixin):
    """Usuario.

    La tabla no se llama `user` porque es palabra reservada en Postgres y obliga a
    entrecomillarla en cada consulta escrita a mano.
    """

    __tablename__ = "user_account"

    id: Mapped[uuid.UUID] = pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id"), nullable=False, index=True
    )
    #: CITEXT en vez de un indice sobre lower(email): la comparacion insensible se
    #: aplica en todas las consultas sin que nadie tenga que acordarse.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    locale: Mapped[str] = mapped_column(String(5), nullable=False, server_default=text("'es'"))
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'Europe/Madrid'")
    )
    role: Mapped[str] = enum_column(UserRole, "role", default="MEMBER")

    email_verified_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    mfa_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    organization: Mapped[Organization] = relationship(foreign_keys=[organization_id])


class Invitation(Base, TimestampMixin):
    """Alta de companeros dentro de una organizacion."""

    __tablename__ = "invitation"
    __table_args__ = (Index("ix_invitation_organization_id_email", "organization_id", "email"),)

    id: Mapped[uuid.UUID] = pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organization.id"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = enum_column(UserRole, "role", default="MEMBER")
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrialConfig(Base, TimestampMixin):
    """Fila unica, editable desde /admin.

    Cambiar la duracion de 14 a 30 dias, el plan que se prueba, o desactivar la prueba
    entera, no puede requerir un despliegue (criterio de aceptacion 10).
    """

    __tablename__ = "trial_config"
    __table_args__ = (
        # Singleton: solo puede existir una fila.
        Index("uq_trial_config_singleton", text("(true)"), unique=True),
        CheckConstraint("duration_days > 0", name="duration_is_positive"),
    )

    id: Mapped[uuid.UUID] = pk()
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("14"))
    trial_plan_code: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'PRO'")
    )
    #: Pedir tarjeta multiplica la calidad del lead pero hunde el volumen. En la fase
    #: de validacion hacen falta conversaciones, no ingresos.
    requires_card: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    fallback_plan_code: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'FREE'")
    )


class AuditLog(Base):
    """Registro de acciones sensibles: extension de prueba, suplantacion, cambios de plan.

    Sin `updated_at` y sin borrado: una auditoria que se puede editar no es una
    auditoria.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity_type_entity_id", "entity_type", "entity_id"),)

    id: Mapped[uuid.UUID] = pk()
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("user_account.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    #: Obligatorio en las acciones que lo exigen; se valida en el servicio.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)


class BlogPost(Base, TimestampMixin, SoftDeleteMixin):
    """Notas en Markdown desde base de datos (apartado 6.1). No se toca hasta la fase 5."""

    __tablename__ = "blog_post"

    id: Mapped[uuid.UUID] = pk()
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    title_i18n: Mapped[dict[str, Any]] = i18n_column()
    body_md_i18n: Mapped[dict[str, Any]] = i18n_column()
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_public: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


__all__ = [
    "AuditLog",
    "BlogPost",
    "Date",
    "Invitation",
    "Organization",
    "Plan",
    "TrialConfig",
    "User",
]
