"""Normalizador HTML -> forma `INDEX_ENTRIES`.

Una pagina de indice de un organismo cambia constantemente por motivos que no
interesan a nadie: el aviso de cookies, un banner, el contador de visitas, la fecha de
«pagina actualizada», el orden de un menu lateral. Vigilar su SHA-256 produce una
alarma por semana y ninguna de ellas significa nada; a la tercera, el operador deja de
mirarlas. Eso es exactamente el falso positivo que el apartado 1 declara inaceptable.

Lo que si importa de un indice es **la lista de publicaciones**: que entradas hay, a
que apuntan y como se llaman. Este normalizador reduce la pagina a esa lista y tira el
resto. Dos versiones de la misma pagina con distinto banner dan la misma forma, y el
servicio de ingesta ya sabe contar eso como cambio cosmetico.

La clave de cada entrada es la **URL absoluta**, no el texto del enlace: el texto lo
retoca cualquiera («Version 3.2.2» pasa a «Version 3.2.2 (vigente)») y usarlo como
identidad convertiria una correccion de estilo en un alta mas una baja.

Todo lo que selecciona entradas sale de `collector_config`, no del codigo: cada
organismo maqueta como quiere y una fuente cuyo selector solo se pueda cambiar tocando
el codigo es una fuente que nadie ajusta.

    {
      "link_xpath":      "//div[@id='contenido']//a[@href]",   # por defecto: //a[@href]
      "url_pattern":     "/versiones/.*\\\\.(xml|pdf)$",        # se queda con estas
      "exclude_pattern": "/(accesibilidad|mapa-web)",          # y descarta estas
      "version_pattern": "v(\\\\d+_\\\\d+(?:_\\\\d+)?)",         # grupo 1 = version
      "max_entries":     200
    }
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import etree, html

#: Se sube a mano cuando cambia la semantica de la salida. Va en la clave unica de
#: `normalized_form`, de modo que el historico reprocesado convive con el anterior.
PARSER_VERSION: Final = "html-index/1"

#: Todos los enlaces con destino. Es el punto de partida razonable: una pagina que no
#: necesita afinar no deberia tener que configurar nada.
DEFAULT_LINK_XPATH: Final = "//a[@href]"

#: Tope de entradas. Un indice de publicaciones tiene decenas; si de pronto salen
#: miles, o el selector esta mal o la pagina ha cambiado de naturaleza. En cualquiera
#: de los dos casos, mejor truncar y avisar que guardar un diff de 4000 lineas.
DEFAULT_MAX_ENTRIES: Final = 200

#: Esquemas que se pueden descargar. `javascript:`, `mailto:` y `tel:` no son
#: publicaciones.
FETCHABLE_SCHEMES: Final = frozenset({"http", "https"})

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """Una publicacion enlazada desde el indice."""

    #: URL absoluta, sin fragmento. Es la identidad de la entrada.
    url: str
    text: str
    filename: str | None
    #: Version leida del enlace con `version_pattern`, si la fuente lo configura.
    version: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "text": self.text,
            "filename": self.filename,
            "version": self.version,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> IndexEntry:
        return cls(
            url=data["url"],
            text=data.get("text", ""),
            filename=data.get("filename"),
            version=data.get("version"),
        )


@dataclass(slots=True)
class IndexForm:
    """Resultado de normalizar un indice."""

    entries: list[IndexEntry] = field(default_factory=list)
    base_url: str | None = None
    #: Se rellena si la pagina declara una fecha de actualizacion aprovechable. La sede
    #: de la AEAT la pone al pie, y es el dato que el analista necesita citar.
    declared_version: str | None = None
    is_partial: bool = False
    missing_dependencies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION

    def by_url(self) -> dict[str, IndexEntry]:
        return {entry.url: entry for entry in self.entries}

    def to_json(self) -> dict[str, Any]:
        return {
            "parser_version": self.parser_version,
            "base_url": self.base_url,
            "declared_version": self.declared_version,
            "is_partial": self.is_partial,
            "missing_dependencies": list(self.missing_dependencies),
            "warnings": list(self.warnings),
            "entries": [entry.to_json() for entry in self.entries],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> IndexForm:
        """Inversa de `to_json`, para comparar contra la forma guardada sin volver a
        parsear el HTML."""
        return cls(
            entries=[IndexEntry.from_json(item) for item in data.get("entries", [])],
            base_url=data.get("base_url"),
            declared_version=data.get("declared_version"),
            is_partial=bool(data.get("is_partial", False)),
            missing_dependencies=list(data.get("missing_dependencies") or []),
            warnings=list(data.get("warnings") or []),
            parser_version=str(data.get("parser_version", PARSER_VERSION)),
        )


def normalize_html_index(
    content: bytes,
    base_url: str | None = None,
    config: dict[str, Any] | None = None,
) -> IndexForm:
    """Reduce una pagina de indice a su lista de publicaciones.

    No lanza por HTML mal formado: lxml lo recompone como hace un navegador, que es lo
    que hay que hacer con el HTML de produccion de cualquier portal.
    """
    settings = dict(config or {})
    form = IndexForm(base_url=base_url)

    try:
        root = html.fromstring(content)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as error:
        raise ValueError(f"HTML ilegible: {error}") from error

    # `<base href>` manda sobre la URL de descarga, igual que en un navegador.
    resolve_from = _declared_base(root) or base_url

    link_xpath = str(settings.get("link_xpath") or DEFAULT_LINK_XPATH)
    try:
        nodes = root.xpath(link_xpath)
    except etree.XPathError as error:
        raise ValueError(f"link_xpath invalido ({link_xpath!r}): {error}") from error
    if not isinstance(nodes, list):
        raise ValueError(f"link_xpath no devuelve nodos: {link_xpath!r}")

    keep = _compiled(settings, "url_pattern")
    drop = _compiled(settings, "exclude_pattern")
    version = _compiled(settings, "version_pattern")
    limit = _positive_int(settings.get("max_entries"), DEFAULT_MAX_ENTRIES)

    # La propia pagina no es una publicacion suya. Sin esto, el enlace «Saltar al
    # contenido principal» de la sede de la AEAT —un ancla a `#contenido` de la misma
    # pagina— entra en el catalogo como si fuera un documento publicado.
    itself = _absolute(resolve_from, resolve_from) if resolve_from else None

    seen: set[str] = set()
    for node in nodes:
        href = node.get("href") if isinstance(node, html.HtmlElement) else None
        if not href:
            continue

        url = _absolute(resolve_from, href)
        if url is None or url in seen or url == itself:
            continue
        if keep is not None and not keep.search(url):
            continue
        if drop is not None and drop.search(url):
            continue

        seen.add(url)
        text = _text_of(node)
        form.entries.append(
            IndexEntry(
                url=url,
                text=text,
                filename=_filename_of(url),
                version=_extract_version(version, url, text),
            )
        )

    if len(form.entries) > limit:
        form.warnings.append(
            f"el indice devolvio {len(form.entries)} entradas y el tope es {limit}: "
            f"o el selector es demasiado ancho o la pagina ha cambiado de naturaleza"
        )
        form.is_partial = True
        del form.entries[limit:]

    if not form.entries:
        # Un indice sin entradas casi nunca es un indice vacio de verdad: suele ser un
        # selector que ya no casa porque el organismo remaqueto. Marcarlo parcial evita
        # que el detector lo lea como «han retirado todas las publicaciones».
        form.warnings.append(
            f"ninguna entrada casa con la configuracion (link_xpath={link_xpath!r}): "
            f"revisa el selector antes de creerte el resultado"
        )
        form.is_partial = True

    # Orden estable por URL: el orden del documento no es semantico y reordenar un
    # listado no puede contar como cambio.
    form.entries.sort(key=lambda entry: entry.url)
    return form


def _declared_base(root: html.HtmlElement) -> str | None:
    for node in root.iter("base"):
        href = node.get("href")
        if href:
            return str(href)
    return None


def _absolute(base_url: str | None, href: str) -> str | None:
    """URL absoluta y sin fragmento, o nulo si no se puede descargar.

    El fragmento se quita porque `#seccion` no identifica un documento distinto: sin
    esto, dos anclas de la misma pagina serian dos publicaciones.
    """
    candidate = urljoin(base_url, href.strip()) if base_url else href.strip()
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in FETCHABLE_SCHEMES:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _text_of(node: html.HtmlElement) -> str:
    """Texto visible del enlace, con los espacios colapsados.

    Un salto de linea de mas en el HTML no es un cambio de contenido, y sin colapsar
    seria indistinguible de uno.
    """
    text = _WHITESPACE.sub(" ", node.text_content() or "").strip()
    if text:
        return text
    # Enlaces que solo envuelven una imagen: el alt es lo unico que describe el destino.
    for image in node.iter("img"):
        alt = _WHITESPACE.sub(" ", image.get("alt") or "").strip()
        if alt:
            return alt
    return ""


def _filename_of(url: str) -> str | None:
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or None


def _extract_version(pattern: re.Pattern[str] | None, url: str, text: str) -> str | None:
    """Primero la URL, despues el texto.

    La URL la escribe quien publica el fichero y cambia poco; el texto lo escribe quien
    redacta la pagina y cambia mas. Si las dos dan version, la de la URL es la fiable.
    """
    if pattern is None:
        return None
    for candidate in (url, text):
        match = pattern.search(candidate)
        if match is None:
            continue
        groups = match.groups()
        return str(groups[0] if groups else match.group(0))
    return None


def _compiled(settings: dict[str, Any], key: str) -> re.Pattern[str] | None:
    raw = settings.get(key)
    if not raw:
        return None
    try:
        return re.compile(str(raw), re.IGNORECASE)
    except re.error as error:
        raise ValueError(f"{key} no es una expresion regular valida: {error}") from error


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
