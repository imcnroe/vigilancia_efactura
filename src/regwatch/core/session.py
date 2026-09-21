"""Motor y fabrica de sesiones de SQLAlchemy.

Separado de `core.db` para que los modelos no arrastren la creacion de un motor al
importarse: Alembic y los tests de unidad importan los modelos sin base de datos.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    """Motor con `pool_pre_ping`: un cron que arranca tras reiniciar Postgres no debe
    fallar por una conexion muerta en el pool."""
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """`expire_on_commit=False`: los informes que devuelve el servicio de ingesta se leen
    despues del commit, y sin esto cada lectura dispararia una consulta sobre una sesion
    ya cerrada."""
    return sessionmaker(engine, expire_on_commit=False)
