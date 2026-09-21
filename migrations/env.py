"""Entorno de Alembic.

La URL sale del entorno o del `.env` local, nunca del fichero .ini: los secretos no van
al repositorio. `DATABASE_URL` en el entorno manda sobre el `.env`, igual que en el
resto de la aplicacion.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from regwatch.core.settings import Settings
from regwatch.models import Base

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers` vale True por defecto, y eso apaga todos los loggers ya
    # creados, incluidos los de `regwatch`. Se nota cuando Alembic corre en proceso —los
    # tests de integracion migran su propia base— porque a partir de ahi los avisos del
    # pipeline se pierden en silencio, y uno de ellos es el del `User-Agent` anonimo.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", Settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
