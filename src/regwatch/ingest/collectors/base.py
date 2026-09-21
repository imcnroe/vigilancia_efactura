"""Interfaz de los colectores.

Decision de diseno importante: **un colector no toca la base de datos ni S3**. Recibe
una fuente, devuelve bytes y metadatos, y ahi acaba su trabajo. Calcular el hash,
decidir si hay artefacto nuevo y persistir es cosa del servicio de ingesta.

Asi los colectores son funciones puras sobre una respuesta HTTP y se prueban sin
levantar infraestructura, que es la unica forma de tener tests que alguien ejecute.
"""

from __future__ import annotations

import abc
import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Un fichero descargado. Todavia no es un artefacto."""

    url: str
    content: bytes
    mime_type: str | None = None
    http_status: int | None = None
    http_headers: dict[str, str] = field(default_factory=dict)
    filename: str | None = None
    #: Version que declara el documento, si el colector sabe leerla sin parsearlo.
    declared_version: str | None = None
    fetched_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class NotModified:
    """El servidor respondio 304. No hay nada que descargar ni que comparar."""

    url: str
    reason: str = "304 Not Modified"


CollectorOutput = list[FetchResult] | NotModified


class CollectorError(Exception):
    """Fallo recuperable de un colector. Cuenta para `consecutive_failures`."""


class Collector(abc.ABC):
    """Contrato comun de todos los colectores."""

    #: Valor de `source.collector_type` que atiende esta implementacion.
    collector_type: str

    @abc.abstractmethod
    def fetch(self, url: str, config: dict[str, Any]) -> CollectorOutput:
        """Descarga lo que corresponda a esta fuente.

        `config` es el `collector_config` de la fuente: selectores, patrones,
        cabeceras, y el `etag`/`last_modified` de la ultima respuesta.

        Devuelve una lista porque un colector de indice descubre varias entradas de
        una sola pasada. Un colector de fichero devuelve una lista de un elemento.
        """


class CollectorRegistry:
    """Resuelve el colector que atiende cada `collector_type`."""

    def __init__(self) -> None:
        self._collectors: dict[str, Collector] = {}

    def register(self, collector: Collector) -> None:
        self._collectors[collector.collector_type] = collector

    def get(self, collector_type: str) -> Collector:
        try:
            return self._collectors[collector_type]
        except KeyError:
            raise CollectorError(f"no hay colector para {collector_type!r}") from None

    def known_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._collectors))
