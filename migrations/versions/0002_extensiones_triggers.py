"""extensiones, triggers e indices de texto

Todo lo que Alembic no sabe autogenerar y que, sin embargo, es donde viven las
garantias del apartado 4: inmutabilidad de artefactos y comparacion de email
insensible a mayusculas.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: Tablas con `updated_at`. El trigger lo mantiene la base, no la aplicacion: si
#: dependiera del ORM, cualquier UPDATE escrito a mano lo dejaria obsoleto.
TABLES_WITH_UPDATED_AT = (
    "alert_subscription",
    "artifact",
    "blog_post",
    "change_event",
    "change_note",
    "document_type",
    "invitation",
    "jurisdiction",
    "normalized_form",
    "notification_preference",
    "organization",
    "pipeline_incident",
    "plan",
    "regulation_family",
    "source",
    "trial_config",
    "user_account",
    "webhook_endpoint",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Email insensible a mayusculas sin que nadie tenga que acordarse de lower().
    op.execute("ALTER TABLE user_account ALTER COLUMN email TYPE citext")
    op.execute("ALTER TABLE invitation ALTER COLUMN email TYPE citext")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in TABLES_WITH_UPDATED_AT:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_touch_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
            """
        )

    # El apartado 1 dice que nunca se borra un artefacto descargado. Que lo garantice
    # la base y no la disciplina del equipo. `updated_at` queda excluido para que el
    # trigger anterior no choque con este.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_artifact_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'los artefactos son inmutables: % sobre artifact esta prohibido',
                TG_OP
              USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_artifact_immutable
        BEFORE UPDATE OR DELETE ON artifact
        FOR EACH ROW EXECUTE FUNCTION forbid_artifact_mutation()
        """
    )
    op.execute("DROP TRIGGER trg_artifact_touch_updated_at ON artifact")

    # Busqueda de texto completo del explorador de cambios (apartado 6.2). Se usa la
    # configuracion `simple` y no `spanish`/`french`: el stemming por idioma exigiria
    # un vector por idioma, y a este volumen no compensa. Se revisara en la fase 3.
    op.execute(
        """
        ALTER TABLE change_note
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            to_tsvector(
                'simple',
                coalesce(title_i18n->>'es', '') || ' ' ||
                coalesce(title_i18n->>'fr', '') || ' ' ||
                coalesce(title_i18n->>'en', '') || ' ' ||
                coalesce(summary_i18n->>'es', '') || ' ' ||
                coalesce(summary_i18n->>'fr', '') || ' ' ||
                coalesce(summary_i18n->>'en', '')
            )
        ) STORED
        """
    )
    op.execute("CREATE INDEX ix_change_note_search_vector ON change_note USING gin (search_vector)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_change_note_search_vector")
    op.execute("ALTER TABLE change_note DROP COLUMN IF EXISTS search_vector")

    op.execute("DROP TRIGGER IF EXISTS trg_artifact_immutable ON artifact")
    op.execute("DROP FUNCTION IF EXISTS forbid_artifact_mutation()")

    for table in TABLES_WITH_UPDATED_AT:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_touch_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS touch_updated_at()")

    op.execute("ALTER TABLE invitation ALTER COLUMN email TYPE varchar(320)")
    op.execute("ALTER TABLE user_account ALTER COLUMN email TYPE varchar(320)")
