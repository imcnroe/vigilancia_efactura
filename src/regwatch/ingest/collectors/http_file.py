"""Colector `HTTP_FILE`: descarga directa de un XSD, ZIP o PDF.

Tres cosas que no son opcionales y que estan aqui por el apartado 7.1:

- **User-Agent identificable y honesto.** Con URL de contacto. No somos un scraper
  anonimo.
- **robots.txt.** Se comprueba y se cachea. Si nos bloquean, se acabo el producto; y
  ya sabemos que el portal de desarrolladores de la AEAT nos bloquea, asi que esto no
  es teorico.
- **Timeout duro.** Un portal caido no puede bloquear el resto de la cola.

Las peticiones condicionales (`If-None-Match`, `If-Modified-Since`) no son solo
educacion: ahorran ancho de banda de un organismo publico y reducen la probabilidad de
que nos vean como un problema.
"""

from __future__ import annotations

import urllib.robotparser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from regwatch.ingest.collectors.base import (
    Collector,
    CollectorError,
    CollectorOutput,
    FetchResult,
    NotModified,
)


class RobotsDisallowedError(CollectorError):
    """El `robots.txt` del organismo prohibe esta ruta. No se insiste."""


class RobotsCache:
    """Cachea el `robots.txt` por host durante el proceso.

    No persiste entre ejecuciones a proposito: una politica que cambia debe notarse en
    la siguiente pasada, no dentro de una semana.
    """

    def __init__(self, client: httpx.Client, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allows(self, url: str) -> bool:
        origin = self._origin_of(url)
        if origin not in self._parsers:
            self._parsers[origin] = self._load(origin)

        parser = self._parsers[origin]
        if parser is None:
            # Sin robots.txt legible, se asume permitido: es la lectura estandar.
            return True
        return parser.can_fetch(self._user_agent, url)

    @staticmethod
    def _origin_of(url: str) -> str:
        parts = urlparse(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _load(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        try:
            response = self._client.get(urljoin(origin, "/robots.txt"))
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None

        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser


class HTTPFileCollector(Collector):
    """Descarga un unico fichero por HTTP."""

    collector_type = "HTTP_FILE"

    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        respect_robots: bool = True,
        client: httpx.Client | None = None,
        robots: RobotsCache | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._max_attempts = max_attempts
        self._respect_robots = respect_robots
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
        # La cache se puede compartir entre colectores: dos que vigilan el mismo host no
        # tienen por que pedirle el `robots.txt` dos veces en la misma pasada.
        self._robots = robots or RobotsCache(self._client, user_agent)

    def fetch(self, url: str, config: dict[str, Any]) -> CollectorOutput:
        if self._respect_robots and not self._robots.allows(url):
            raise RobotsDisallowedError(
                f"robots.txt prohibe {url}. No se reintenta: hay que resolverlo con el "
                f"organismo, no insistiendo."
            )

        response = self._get_with_retries(url, self._conditional_headers(config))

        if response.status_code == 304:
            return NotModified(url=url)
        if response.status_code >= 400:
            raise CollectorError(f"{url} devolvio {response.status_code}")

        return [
            FetchResult(
                url=str(response.url),
                content=response.content,
                mime_type=response.headers.get("content-type", "").split(";")[0] or None,
                http_status=response.status_code,
                http_headers=dict(response.headers),
                filename=self._filename_of(str(response.url)),
            )
        ]

    @staticmethod
    def _conditional_headers(config: dict[str, Any]) -> dict[str, str]:
        headers: dict[str, str] = dict(config.get("headers", {}))
        if etag := config.get("etag"):
            headers["If-None-Match"] = str(etag)
        if last_modified := config.get("last_modified"):
            headers["If-Modified-Since"] = str(last_modified)
        return headers

    def _get_with_retries(self, url: str, headers: dict[str, str]) -> httpx.Response:
        @retry(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(httpx.HTTPError),
            reraise=True,
        )
        def attempt() -> httpx.Response:
            return self._client.get(url, headers=headers)

        try:
            return attempt()
        except httpx.HTTPError as error:
            raise CollectorError(f"no se pudo descargar {url}: {error}") from error

    @staticmethod
    def _filename_of(url: str) -> str | None:
        """Ultimo segmento de la ruta.

        Ojo: el nombre no dice el tipo. Facturae sirve sus XSD con extension `.xml`,
        asi que deducir el formato de la extension da un resultado equivocado.
        """
        path = urlparse(url).path.rstrip("/")
        return path.rsplit("/", 1)[-1] or None

    def close(self) -> None:
        self._client.close()


def headers_to_config(response_headers: dict[str, str]) -> dict[str, str]:
    """Extrae de una respuesta lo que hay que guardar para la siguiente peticion."""
    keep: dict[str, str] = {}
    if etag := response_headers.get("etag"):
        keep["etag"] = etag
    if last_modified := response_headers.get("last-modified"):
        keep["last_modified"] = last_modified
    return keep
