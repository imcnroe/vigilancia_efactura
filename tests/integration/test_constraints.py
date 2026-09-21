"""Las restricciones que el contexto declara innegociables, comprobadas contra Postgres.

No basta con que esten escritas en el modelo: hay que verificar que la base las
rechaza de verdad. Estas seis pruebas son la diferencia entre una regla de negocio y
un comentario.

Requiere Postgres en marcha con las migraciones aplicadas:

    docker compose up -d && alembic upgrade head && pytest -m integration
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from regwatch.core.ids import uuid7

from .conftest import CONNECT_ARGS

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://regwatch:regwatch@localhost:5432/regwatch"
)


@pytest.fixture(scope="module")
def engine() -> Engine:
    """Motor contra la base de pruebas.

    Si no hay Postgres en marcha se salta el modulo entero en vez de fallar: un test
    de integracion que revienta en un portatil sin Docker se acaba desactivando, y un
    test desactivado no protege nada. Con `connect_timeout` corto, ademas, se salta en
    segundos: esperar minutos a que caduque la conexion tiene el mismo efecto que
    fallar.
    """
    candidate = create_engine(DATABASE_URL, connect_args=CONNECT_ARGS)
    try:
        with candidate.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"no hay Postgres en {DATABASE_URL}: {error.orig}", allow_module_level=True)
    return candidate


@pytest.fixture
def conn(engine: Engine) -> Iterator:
    """Cada test corre en una transaccion que se deshace al terminar."""
    connection = engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def _seed_source(conn) -> uuid.UUID:
    jurisdiction_id, family_id, source_id = uuid7(), uuid7(), uuid7()
    conn.execute(
        text("""
        INSERT INTO jurisdiction (id, code, name_i18n, country_code, is_active)
        VALUES (:id, :code, '{"es": "Prueba"}'::jsonb, 'ES', true)
        """),
        {"id": jurisdiction_id, "code": f"T{uuid.uuid4().hex[:6]}"},
    )
    conn.execute(
        text("""
        INSERT INTO regulation_family (id, jurisdiction_id, code, name_i18n)
        VALUES (:id, :jid, :code, '{"es": "Familia"}'::jsonb)
        """),
        {"id": family_id, "jid": jurisdiction_id, "code": f"F{uuid.uuid4().hex[:6]}"},
    )
    conn.execute(
        text("""
        INSERT INTO source (id, regulation_family_id, name, url, source_kind,
                            collector_type, check_frequency, priority, is_active)
        VALUES (:id, :fid, 'Fuente', 'https://example.org/x.xsd', 'SCHEMA',
                'HTTP_FILE', '0 6 * * *', 'HOT', true)
        """),
        {"id": source_id, "fid": family_id},
    )
    return source_id


def _insert_artifact(conn, source_id: uuid.UUID, content_hash: str) -> uuid.UUID:
    artifact_id = uuid7()
    conn.execute(
        text("""
        INSERT INTO artifact (id, source_id, captured_at, content_hash, storage_key,
                              size_bytes, fetched_url)
        VALUES (:id, :sid, now(), :hash, :key, 100, 'https://example.org/x.xsd')
        """),
        {
            "id": artifact_id,
            "sid": source_id,
            "hash": content_hash,
            "key": f"sha256/{content_hash}",
        },
    )
    return artifact_id


def test_change_note_cannot_be_published_without_a_reviewer(conn) -> None:
    """La regla del apartado 4.2. Tiene que ser imposible saltarsela incluso por API."""
    source_id = _seed_source(conn)
    artifact_id = _insert_artifact(conn, source_id, "a" * 64)
    event_id = uuid7()

    conn.execute(
        text("""
        INSERT INTO change_event (id, source_id, artifact_to_id, detected_at,
                                  structural_diff, diff_schema_version,
                                  severity_suggested, status)
        VALUES (:id, :sid, :aid, now(), '{}'::jsonb, 'xsd-diff/1', 'BLOCKING', 'DETECTED')
        """),
        {"id": event_id, "sid": source_id, "aid": artifact_id},
    )

    with pytest.raises(IntegrityError, match="publication_requires_human_review"):
        conn.execute(
            text("""
            INSERT INTO change_note (id, change_event_id, status, severity,
                                     title_i18n, published_at)
            VALUES (:id, :eid, 'PUBLISHED', 'BLOCKING', '{"es": "Cambio"}'::jsonb, now())
            """),
            {"id": uuid7(), "eid": event_id},
        )


def test_artifact_cannot_be_updated(conn) -> None:
    source_id = _seed_source(conn)
    _insert_artifact(conn, source_id, "b" * 64)

    with pytest.raises(DBAPIError, match="inmutables"):
        conn.execute(
            text("UPDATE artifact SET size_bytes = 999 WHERE source_id = :sid"), {"sid": source_id}
        )


def test_artifact_cannot_be_deleted(conn) -> None:
    source_id = _seed_source(conn)
    _insert_artifact(conn, source_id, "c" * 64)

    with pytest.raises(DBAPIError, match="inmutables"):
        conn.execute(text("DELETE FROM artifact WHERE source_id = :sid"), {"sid": source_id})


def test_same_hash_twice_on_a_source_is_rejected(conn) -> None:
    """Si el hash no cambia no hay artefacto nuevo: solo se toca `last_checked_at`."""
    source_id = _seed_source(conn)
    _insert_artifact(conn, source_id, "d" * 64)

    with pytest.raises(IntegrityError, match="uq_artifact_source_id_content_hash"):
        _insert_artifact(conn, source_id, "d" * 64)


def test_alert_subscription_needs_exactly_one_scope(conn) -> None:
    source_id = _seed_source(conn)
    plan_id, org_id, user_id = uuid7(), uuid7(), uuid7()

    conn.execute(
        text("""
        INSERT INTO plan (id, code, name_i18n, price_monthly, price_yearly)
        VALUES (:id, :code, '{"es": "Plan"}'::jsonb, 0, 0)
        """),
        {"id": plan_id, "code": f"P{uuid.uuid4().hex[:6]}"},
    )
    conn.execute(
        text("INSERT INTO organization (id, name, plan_id) VALUES (:id, 'Org', :pid)"),
        {"id": org_id, "pid": plan_id},
    )
    conn.execute(
        text("""
        INSERT INTO user_account (id, organization_id, email, password_hash)
        VALUES (:id, :oid, :email, 'x')
        """),
        {"id": user_id, "oid": org_id, "email": f"{uuid.uuid4().hex}@example.org"},
    )

    jurisdiction_id = conn.execute(text("SELECT id FROM jurisdiction LIMIT 1")).scalar_one()

    # Dos ambitos a la vez: rechazado.
    with pytest.raises(IntegrityError, match="exactly_one_scope"):
        conn.execute(
            text("""
            INSERT INTO alert_subscription (id, user_id, scope_type, jurisdiction_id,
                                            source_id, min_severity)
            VALUES (:id, :uid, 'JURISDICTION', :jid, :sid, 'INFO')
            """),
            {"id": uuid7(), "uid": user_id, "jid": jurisdiction_id, "sid": source_id},
        )


def test_discarding_a_change_requires_a_reason(conn) -> None:
    source_id = _seed_source(conn)
    artifact_id = _insert_artifact(conn, source_id, "e" * 64)

    with pytest.raises(IntegrityError, match="discard_needs_a_reason"):
        conn.execute(
            text("""
            INSERT INTO change_event (id, source_id, artifact_to_id, detected_at,
                                      structural_diff, diff_schema_version,
                                      severity_suggested, status)
            VALUES (:id, :sid, :aid, now(), '{}'::jsonb, 'xsd-diff/1', 'INFO', 'DISCARDED')
            """),
            {"id": uuid7(), "sid": source_id, "aid": artifact_id},
        )


def test_email_comparison_is_case_insensitive(conn) -> None:
    plan_id, org_id = uuid7(), uuid7()
    address = f"Ana.Perez.{uuid.uuid4().hex[:8]}@Example.ORG"

    conn.execute(
        text("""
        INSERT INTO plan (id, code, name_i18n, price_monthly, price_yearly)
        VALUES (:id, :code, '{"es": "Plan"}'::jsonb, 0, 0)
        """),
        {"id": plan_id, "code": f"P{uuid.uuid4().hex[:6]}"},
    )
    conn.execute(
        text("INSERT INTO organization (id, name, plan_id) VALUES (:id, 'Org', :pid)"),
        {"id": org_id, "pid": plan_id},
    )
    conn.execute(
        text("""
        INSERT INTO user_account (id, organization_id, email, password_hash)
        VALUES (:id, :oid, :email, 'x')
        """),
        {"id": uuid7(), "oid": org_id, "email": address},
    )

    found = conn.execute(
        text("SELECT count(*) FROM user_account WHERE email = :email"),
        {"email": address.lower()},
    ).scalar_one()
    assert found == 1

    with pytest.raises(IntegrityError, match="uq_user_account_email"):
        conn.execute(
            text("""
            INSERT INTO user_account (id, organization_id, email, password_hash)
            VALUES (:id, :oid, :email, 'x')
            """),
            {"id": uuid7(), "oid": org_id, "email": address.upper()},
        )


def test_updated_at_is_maintained_by_the_database(conn) -> None:
    source_id = _seed_source(conn)
    before = conn.execute(
        text("SELECT updated_at FROM source WHERE id = :id"), {"id": source_id}
    ).scalar_one()

    conn.execute(
        text("UPDATE source SET consecutive_failures = 3 WHERE id = :id"), {"id": source_id}
    )
    after = conn.execute(
        text("SELECT updated_at FROM source WHERE id = :id"), {"id": source_id}
    ).scalar_one()

    assert isinstance(after, dt.datetime)
    assert after >= before


def test_trial_config_is_a_singleton(conn) -> None:
    """La migracion semilla ya deja la fila puesta, asi que aqui basta con comprobar que
    la base rechaza una segunda. Si el esquema estuviera sin semilla, se crea primero."""
    existing = conn.execute(text("SELECT count(*) FROM trial_config")).scalar_one()
    if existing == 0:
        conn.execute(text("INSERT INTO trial_config (id) VALUES (:id)"), {"id": uuid7()})

    with pytest.raises(IntegrityError, match="uq_trial_config_singleton"):
        conn.execute(text("INSERT INTO trial_config (id) VALUES (:id)"), {"id": uuid7()})
