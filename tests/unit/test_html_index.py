"""Colector, normalizador y detector de indices de publicaciones.

El HTML de estos tests imita el de un portal de verdad: menu de navegacion, aviso de
cookies, fecha de actualizacion al pie y los enlaces que importan mezclados con los que
no. Un fixture limpio probaria el caso que nunca se da.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from regwatch.core.enums import ChangeType, Severity, SourceKind
from regwatch.ingest.collectors.http_html_index import HTTPHtmlIndexCollector
from regwatch.ingest.diff.index_diff import compare_index
from regwatch.ingest.normalizers.dispatch import Normalized, NotNormalizable, normalize_content
from regwatch.ingest.normalizers.html_index import IndexForm, normalize_html_index

USER_AGENT = "regwatch/0.1 (+https://example.org/bot; hola@example.org)"
BASE = "https://www.organismo.example/formato/ultima-version"

#: Configuracion de una fuente de indice real: solo los ficheros publicados, y la
#: version sale del nombre del fichero.
CONFIG = {
    "url_pattern": r"/versiones/.*\.(xml|pdf)$",
    "version_pattern": r"v(\d+_\d+(?:_\d+)?)",
}


def page(entries: str, banner: str = "Aviso de cookies") -> bytes:
    """Una pagina de indice con su ruido alrededor."""
    return f"""<!DOCTYPE html>
<html lang="es"><head><title>Versiones</title></head>
<body>
  <div id="cookies">{banner}</div>
  <nav><a href="/">Inicio</a> <a href="/accesibilidad">Accesibilidad</a>
       <a href="mailto:info@organismo.example">Contacto</a>
       <a href="javascript:print()">Imprimir</a></nav>
  <div id="contenido">
    <ul>
{entries}
    </ul>
  </div>
  <footer>Pagina actualizada: 26/marzo/2026</footer>
</body></html>""".encode()


ENTRIES_321 = """      <li><a href="/dam/versiones/Esquemav3_2_1.xml">Esquema version 3.2.1</a></li>
      <li><a href="/dam/versiones/Historialv3_2_1.pdf">Historial de versiones 3.2.1</a></li>"""

ENTRIES_322 = (
    ENTRIES_321
    + """
      <li><a href="/dam/versiones/Esquemav3_2_2.xml">Esquema version 3.2.2</a></li>"""
)


def form_of(html: bytes, config: dict[str, object] | None = None) -> IndexForm:
    return normalize_html_index(html, BASE, config if config is not None else CONFIG)


# -- normalizador ----------------------------------------------------------------


def test_only_the_publications_survive_the_page() -> None:
    """El menu, el aviso de cookies y el pie no son publicaciones."""
    form = form_of(page(ENTRIES_321))
    assert [entry.url for entry in form.entries] == [
        "https://www.organismo.example/dam/versiones/Esquemav3_2_1.xml",
        "https://www.organismo.example/dam/versiones/Historialv3_2_1.pdf",
    ]
    assert not form.is_partial


def test_relative_links_are_resolved_against_the_page() -> None:
    form = form_of(page('<li><a href="../dam/versiones/Esquemav1_0.xml">Otro</a></li>'))
    assert form.entries[0].url == "https://www.organismo.example/dam/versiones/Esquemav1_0.xml"


def test_a_declared_base_wins_over_the_download_url() -> None:
    """Igual que en un navegador: si la pagina declara `<base>`, manda."""
    html = page('<li><a href="Esquemav2_0.xml">Dos</a></li>').replace(
        b"<head>", b'<head><base href="https://cdn.organismo.example/ficheros/versiones/">'
    )
    form = form_of(html)
    assert form.entries[0].url == "https://cdn.organismo.example/ficheros/versiones/Esquemav2_0.xml"


def test_unfetchable_schemes_are_not_publications() -> None:
    form = form_of(page(ENTRIES_321), config={})
    schemes = {entry.url.split(":", 1)[0] for entry in form.entries}
    assert schemes == {"https"}


def test_the_fragment_is_dropped_and_duplicates_collapse() -> None:
    """`#seccion` no identifica otro documento: sin quitarlo, dos anclas de la misma
    pagina serian dos publicaciones."""
    form = form_of(
        page(
            '<li><a href="/dam/versiones/Esquemav3_2_1.xml#top">Arriba</a></li>'
            '<li><a href="/dam/versiones/Esquemav3_2_1.xml">Otra vez</a></li>'
        )
    )
    assert len(form.entries) == 1
    assert form.entries[0].url.endswith("Esquemav3_2_1.xml")


def test_a_link_to_the_page_itself_is_not_one_of_its_publications() -> None:
    """Caso real de la sede de la AEAT: el enlace «Saltar al contenido principal» es un
    ancla a la propia pagina, y al quitarle el fragmento se convertia en una entrada."""
    form = form_of(
        page(
            '<li><a href="#contenido">Saltar al contenido principal</a></li>' + ENTRIES_321,
        ),
        config={},
    )
    assert BASE not in [entry.url for entry in form.entries]
    # Sin `url_pattern` entran los dos ficheros y los dos enlaces del menu, pero no el
    # ancla a la propia pagina.
    assert len(form.entries) == 4


def test_the_exclude_pattern_drops_what_the_url_pattern_let_through() -> None:
    form = form_of(
        page(ENTRIES_322),
        config=dict(CONFIG, exclude_pattern=r"Historial"),
    )
    assert all("Historial" not in entry.url for entry in form.entries)
    assert len(form.entries) == 2


def test_the_version_comes_from_the_url_before_the_text() -> None:
    """La URL la escribe quien publica el fichero; el texto, quien redacta la pagina."""
    form = form_of(page('<li><a href="/dam/versiones/Esquemav3_2_2.xml">Version 9.9</a></li>'))
    assert form.entries[0].version == "3_2_2"


def test_without_a_version_pattern_there_is_no_version() -> None:
    form = form_of(page(ENTRIES_321), config={"url_pattern": r"\.xml$"})
    assert form.entries[0].version is None


def test_link_text_is_collapsed_and_images_fall_back_to_alt() -> None:
    form = form_of(
        page(
            '<li><a href="/dam/versiones/Esquemav1_0.xml">Esquema\n\n   version   1.0</a></li>'
            '<li><a href="/dam/versiones/Historialv1_0.pdf">'
            '<img src="/pdf.png" alt="Historial en PDF"></a></li>'
        )
    )
    assert form.entries[0].text == "Esquema version 1.0"
    assert form.entries[1].text == "Historial en PDF"


def test_entries_are_sorted_by_url_so_reordering_the_page_is_not_a_change() -> None:
    forward = form_of(page(ENTRIES_322))
    lines = ENTRIES_322.splitlines()
    backward = form_of(page("\n".join(reversed(lines))))
    assert [entry.url for entry in forward.entries] == [entry.url for entry in backward.entries]


def test_too_many_entries_are_truncated_and_the_form_is_marked_partial() -> None:
    many = "\n".join(
        f'<li><a href="/dam/versiones/Esquemav{n}_0.xml">n {n}</a></li>' for n in range(30)
    )
    form = form_of(page(many), config=dict(CONFIG, max_entries=10))
    assert len(form.entries) == 10
    assert form.is_partial
    assert any("tope" in warning for warning in form.warnings)


def test_a_selector_that_matches_nothing_is_partial_not_an_empty_index() -> None:
    """Un indice vacio casi siempre es un selector roto, no un organismo que ha
    retirado todo lo que publicaba."""
    form = form_of(page(ENTRIES_321), config={"url_pattern": r"no-existe"})
    assert form.entries == []
    assert form.is_partial
    assert any("selector" in warning for warning in form.warnings)


def test_broken_html_is_recomposed_like_a_browser_would() -> None:
    form = normalize_html_index(
        b"<html><body><ul><li><a href='/dam/versiones/Esquemav1_0.xml'>Sin cerrar",
        BASE,
        CONFIG,
    )
    assert len(form.entries) == 1


def test_an_invalid_selector_is_the_operators_fault_not_a_collector_failure() -> None:
    outcome = normalize_content(
        SourceKind.INDEX, page(ENTRIES_321), base_url=BASE, config={"link_xpath": "//a[["}
    )
    assert isinstance(outcome, NotNormalizable)
    assert "link_xpath" in outcome.reason


def test_a_pdf_served_by_an_index_source_is_not_forced_through_the_parser() -> None:
    outcome = normalize_content(SourceKind.INDEX, b"%PDF-1.7\n", base_url=BASE, config=CONFIG)
    assert isinstance(outcome, NotNormalizable)
    assert "pdf" in outcome.reason


def test_the_dispatcher_produces_an_index_entries_form() -> None:
    outcome = normalize_content(SourceKind.INDEX, page(ENTRIES_322), base_url=BASE, config=CONFIG)
    assert isinstance(outcome, Normalized)
    assert outcome.form_type == "INDEX_ENTRIES"
    assert outcome.parser_version == "html-index/1"
    assert len(outcome.payload["entries"]) == 3
    assert outcome.index_form is not None
    assert outcome.typed_form is outcome.index_form


def test_the_form_survives_a_round_trip_through_json() -> None:
    """Es lo que permite comparar contra la forma guardada sin volver a parsear."""
    original = form_of(page(ENTRIES_322))
    restored = IndexForm.from_json(original.to_json())
    assert restored.to_json() == original.to_json()


# -- detector --------------------------------------------------------------------


def test_a_new_publication_is_the_event_the_product_exists_for() -> None:
    diff = compare_index(form_of(page(ENTRIES_321)), form_of(page(ENTRIES_322)))

    (item,) = diff.items
    assert item.change_type is ChangeType.INDEX_ENTRY_ADDED
    assert item.path == "https://www.organismo.example/dam/versiones/Esquemav3_2_2.xml"
    assert item.severity is Severity.REQUIRES_CHANGE
    assert item.detail["version"] == "3_2_2"
    assert diff.diff_schema_version == "index-diff/1"


def test_a_withdrawn_publication_is_reported_too() -> None:
    diff = compare_index(form_of(page(ENTRIES_322)), form_of(page(ENTRIES_321)))

    (item,) = diff.items
    assert item.change_type is ChangeType.INDEX_ENTRY_REMOVED
    assert item.severity is Severity.REQUIRES_CHANGE


def test_cosmetic_page_changes_produce_no_items_at_all() -> None:
    """Es el motivo entero de que exista este normalizador: vigilar el SHA-256 de una
    pagina de portal da una alarma por semana y ninguna significa nada."""
    diff = compare_index(
        form_of(page(ENTRIES_321, banner="Aviso de cookies")),
        form_of(page(ENTRIES_321, banner="Usamos cookies propias y de terceros")),
    )
    assert diff.is_empty
    assert diff.max_severity() is Severity.INFO


def test_a_version_that_changes_on_a_stable_url_is_the_gipuzkoa_case() -> None:
    """El organismo resustituye el fichero sin cambiar el enlace. La vigilancia por
    numero de version declarada no lo ve; esta si."""
    before = form_of(page('<li><a href="/dam/versiones/Esquemav1_1.xml">Esquema</a></li>'))
    after = form_of(page('<li><a href="/dam/versiones/Esquemav1_1.xml">Esquema v1_2</a></li>'))
    # Mismo destino, version distinta leida del texto porque la URL no la trae.
    after.entries[0] = type(after.entries[0])(
        url=after.entries[0].url, text="Esquema", filename=after.entries[0].filename, version="1_2"
    )

    (item,) = compare_index(before, after).items
    assert item.change_type is ChangeType.INDEX_ENTRY_UPDATED
    assert item.severity is Severity.REQUIRES_CHANGE
    assert item.detail["reason"] == "version_changed_on_stable_url"


def test_retitling_a_link_is_only_informative() -> None:
    before = form_of(page('<li><a href="/dam/versiones/Esquemav3_2_2.xml">Esquema</a></li>'))
    after = form_of(
        page('<li><a href="/dam/versiones/Esquemav3_2_2.xml">Esquema (vigente)</a></li>')
    )

    (item,) = compare_index(before, after).items
    assert item.change_type is ChangeType.INDEX_ENTRY_UPDATED
    assert item.severity is Severity.INFO
    assert "version" not in item.detail["changed"]


def test_a_broken_selector_does_not_report_the_whole_catalogue_as_withdrawn() -> None:
    """El falso positivo mas caro posible: el selector deja de casar y el detector
    anuncia que el organismo ha retirado todas sus publicaciones."""
    diff = compare_index(
        form_of(page(ENTRIES_322)),
        form_of(page(ENTRIES_322), config={"url_pattern": r"ya-no-casa"}),
    )
    assert diff.items == []
    assert diff.is_partial
    assert any("vacia" in note for note in diff.notes)


def test_severity_ordering_puts_the_publications_first() -> None:
    before = form_of(page(ENTRIES_321))
    after = form_of(
        page(ENTRIES_322.replace("Esquema version 3.2.1", "Esquema version 3.2.1 (anterior)"))
    )
    types = [item.change_type for item in compare_index(before, after).items]
    assert types == [ChangeType.INDEX_ENTRY_ADDED, ChangeType.INDEX_ENTRY_UPDATED]


# -- colector --------------------------------------------------------------------


def make_collector(
    handler: Callable[[httpx.Request], httpx.Response],
) -> HTTPHtmlIndexCollector:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    return HTTPHtmlIndexCollector(user_agent=USER_AGENT, client=client)


def test_the_collector_downloads_the_page_and_nothing_it_links() -> None:
    """Descubrir no es capturar: la pasada es una peticion, no treinta."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, content=page(ENTRIES_322), headers={"content-type": "text/html"})

    output = make_collector(handler).fetch(BASE, {})

    assert isinstance(output, list)
    assert len(output) == 1
    assert output[0].mime_type == "text/html"
    assert requested == ["/robots.txt", "/formato/ultima-version"]


def test_the_collector_is_registered_under_its_own_type() -> None:
    assert HTTPHtmlIndexCollector.collector_type == "HTTP_HTML_INDEX"


def test_robots_and_conditional_requests_are_inherited() -> None:
    """No hay dos formas de salir a la red en este proyecto."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=page(ENTRIES_321), headers={"etag": '"v1"'})

    collector = make_collector(handler)
    first = collector.fetch(BASE, {})
    assert isinstance(first, list)

    second = collector.fetch(BASE, {"etag": '"v1"'})
    assert not isinstance(second, list)
    assert second.reason == "304 Not Modified"


def test_robots_disallow_stops_the_index_too() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /formato\n")
        raise AssertionError("no deberia haberse pedido la pagina")

    from regwatch.ingest.collectors.http_file import RobotsDisallowedError

    with pytest.raises(RobotsDisallowedError):
        make_collector(handler).fetch(BASE, {})
