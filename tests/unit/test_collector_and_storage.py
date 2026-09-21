"""Colector y almacen, sin tocar la red ni el disco de verdad.

El transporte de httpx se sustituye por uno simulado. Un test que necesita internet
para pasar es un test que un dia falla por motivos que no tienen que ver con el codigo.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from regwatch.ingest.collectors.base import FetchResult, NotModified
from regwatch.ingest.collectors.http_file import (
    HTTPFileCollector,
    RobotsDisallowedError,
    headers_to_config,
)
from regwatch.ingest.storage.artifact_store import LocalArtifactStore, content_key

USER_AGENT = "regwatch/0.1 (+https://example.org/crawler; hola@example.org)"


def make_collector(handler, respect_robots: bool = True) -> HTTPFileCollector:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    return HTTPFileCollector(user_agent=USER_AGENT, respect_robots=respect_robots, client=client)


# -- almacen -------------------------------------------------------------------


def test_key_is_derived_from_content(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    key = store.put(b"<xs:schema/>")
    assert key.startswith("sha256/")
    assert store.get(key) == b"<xs:schema/>"


def test_putting_the_same_content_twice_is_idempotent(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    first = store.put(b"mismo contenido")
    second = store.put(b"mismo contenido")
    assert first == second
    assert len(list(tmp_path.rglob("*"))) == 4  # sha256/ab/cd/<hash>


def test_different_content_gets_different_keys(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    assert store.put(b"uno") != store.put(b"dos")


def test_missing_key_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        LocalArtifactStore(tmp_path).get(content_key(b"nunca guardado"))


# -- colector ------------------------------------------------------------------


def test_downloads_a_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(
            200, content=b"<xs:schema/>", headers={"content-type": "application/xml"}
        )

    result = make_collector(handler).fetch("https://example.org/x.xsd", {})
    assert isinstance(result, list)
    assert len(result) == 1

    fetched = result[0]
    assert isinstance(fetched, FetchResult)
    assert fetched.content == b"<xs:schema/>"
    assert fetched.mime_type == "application/xml"
    assert fetched.filename == "x.xsd"
    assert len(fetched.content_hash) == 64


def test_respects_robots_txt() -> None:
    """El portal de desarrolladores de la AEAT nos bloquea, asi que esto no es teorico."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /privado/\n")
        return httpx.Response(200, content=b"no deberia llegar aqui")

    with pytest.raises(RobotsDisallowedError):
        make_collector(handler).fetch("https://example.org/privado/x.xsd", {})


def test_missing_robots_txt_means_allowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"ok")

    result = make_collector(handler).fetch("https://example.org/x.xsd", {})
    assert isinstance(result, list)


def test_conditional_request_returns_not_modified() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        seen.update(request.headers)
        return httpx.Response(304)

    result = make_collector(handler).fetch("https://example.org/x.xsd", {"etag": '"abc"'})
    assert isinstance(result, NotModified)
    assert seen["if-none-match"] == '"abc"'


def test_user_agent_is_sent() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("user-agent", ""))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"ok")

    make_collector(handler).fetch("https://example.org/x.xsd", {})
    assert all(agent == USER_AGENT for agent in captured)


def test_validator_headers_are_kept_for_next_time() -> None:
    kept = headers_to_config(
        {"etag": '"v7"', "last-modified": "Mon, 01 Sep 2026 00:00:00 GMT", "server": "nginx"}
    )
    assert kept == {"etag": '"v7"', "last_modified": "Mon, 01 Sep 2026 00:00:00 GMT"}


def test_filename_extension_does_not_imply_format() -> None:
    """Facturae sirve sus XSD como .xml: deducir el tipo de la extension enganaria."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"<xs:schema/>", headers={"content-type": "text/xml"})

    result = make_collector(handler).fetch(
        "https://www.facturae.gob.es/content/dam/f/Facturaev3_2_1.xml", {}
    )
    assert isinstance(result, list)
    assert result[0].filename == "Facturaev3_2_1.xml"
    assert result[0].mime_type == "text/xml"
