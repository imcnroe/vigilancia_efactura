"""Composicion del servicio de ingesta a partir de la configuracion.

Es el unico sitio donde se juntan las piezas concretas (Postgres, S3, httpx). Los
tests construyen `IngestService` a mano con dobles; este modulo no se importa desde
ningun test de unidad.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session, sessionmaker

from regwatch.core.session import make_engine, make_session_factory
from regwatch.core.settings import Settings
from regwatch.ingest.collectors.base import CollectorRegistry
from regwatch.ingest.collectors.http_file import HTTPFileCollector, RobotsCache
from regwatch.ingest.collectors.http_html_index import HTTPHtmlIndexCollector
from regwatch.ingest.service import IngestService
from regwatch.ingest.storage.factory import build_store

log = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    """La configuracion no permite arrancar. El mensaje dice que hay que tocar."""


def build_session_factory(settings: Settings) -> sessionmaker[Session]:
    return make_session_factory(make_engine(settings.database_url))


def build_collectors(settings: Settings) -> CollectorRegistry:
    """Los colectores que se saben construir hoy: `HTTP_FILE` y `HTTP_HTML_INDEX`."""
    if problem := settings.user_agent_problem():
        raise ConfigurationError(problem)

    if settings.user_agent_is_anonymous():
        # Deja rastro: una bandera que relaja una norma de cortesia no puede activarse
        # en produccion sin que se note en los logs.
        log.warning(
            "descargando con un User-Agent sin datos de contacto por "
            "COLLECTOR_ALLOW_ANONYMOUS. Solo para desarrollo supervisado: la vigilancia "
            "continua necesita un agente identificable (apartado 7.1)",
            extra={"user_agent": settings.collector_user_agent},
        )
    # Un solo cliente y una sola cache de `robots.txt` para todos los colectores: dos
    # fuentes del mismo organismo, una de indice y otra de esquema, no tienen por que
    # pedirle el `robots.txt` dos veces en la misma pasada.
    client = httpx.Client(
        timeout=httpx.Timeout(settings.collector_timeout_seconds),
        follow_redirects=True,
        headers={"User-Agent": settings.collector_user_agent},
    )
    robots = RobotsCache(client, settings.collector_user_agent)

    registry = CollectorRegistry()
    for collector_class in (HTTPFileCollector, HTTPHtmlIndexCollector):
        registry.register(
            collector_class(
                user_agent=settings.collector_user_agent,
                timeout_seconds=settings.collector_timeout_seconds,
                max_attempts=settings.collector_max_attempts,
                client=client,
                robots=robots,
            )
        )
    return registry


def build_service(
    settings: Settings, session_factory: sessionmaker[Session] | None = None
) -> IngestService:
    # Los colectores primero: validan la configuracion sin tocar nada. Construir el
    # almacen antes haria que un `User-Agent` sin contacto se manifestase como un error
    # de conexion a S3, que es el diagnostico equivocado.
    collectors = build_collectors(settings)
    return IngestService(
        session_factory or build_session_factory(settings),
        build_store(settings),
        collectors,
        heartbeat_threshold=settings.heartbeat_failure_threshold,
    )
