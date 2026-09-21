"""Base de datos temporal para los tests de integracion que confirman transacciones.

`test_constraints.py` trabaja sobre la base de desarrollo dentro de una transaccion que
se deshace. El servicio de ingesta no puede probarse asi: confirma sus propias
transacciones, y los artefactos que crea no se pueden borrar (los protege un trigger).
La salida limpia es una base nueva por sesion de tests, migrada con Alembic y destruida
al terminar.

Requiere Postgres en marcha en `DATABASE_URL`; si no lo hay, se salta todo el paquete.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from regwatch.core.session import make_engine, make_session_factory

REPO_ROOT = Path(__file__).resolve().parents[2]

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://regwatch:regwatch@localhost:5432/regwatch"
)

#: Un portatil sin Docker no debe esperar minutos para saltarse los tests.
CONNECT_ARGS = {"connect_timeout": 3}


def _reachable(url: str) -> str | None:
    """Devuelve el motivo si no se puede conectar; nulo si se puede."""
    engine = create_engine(url, connect_args=CONNECT_ARGS)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as error:
        return str(error.orig)
    finally:
        engine.dispose()
    return None


@pytest.fixture(scope="session")
def temp_database_url() -> Iterator[str]:
    """Crea `regwatch_test_<hex>`, la migra a `head` y la destruye al final."""
    reason = _reachable(DATABASE_URL)
    if reason is not None:
        pytest.skip(f"no hay Postgres en {DATABASE_URL}: {reason}")

    admin_url = make_url(DATABASE_URL)
    database_name = f"regwatch_test_{uuid.uuid4().hex[:10]}"
    test_url = admin_url.set(database=database_name)

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", connect_args=CONNECT_ARGS)
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))

    try:
        _migrate(test_url.render_as_string(hide_password=False))
        yield test_url.render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


def _migrate(url: str) -> None:
    """Aplica las migraciones reales. Lo que se prueba es el esquema que hay en
    produccion, no `create_all`, que no sabe de triggers ni de citext."""
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def temp_engine(temp_database_url: str) -> Iterator[Engine]:
    engine = make_engine(temp_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(temp_engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(temp_engine)
