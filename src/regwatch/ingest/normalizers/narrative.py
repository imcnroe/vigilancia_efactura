"""Normalizador de documentos narrativos (PDF y HTML) -> forma `TEXT_BLOCKS`.

Son las guias, las FAQ y los historiales de versiones: documentos que no tienen
estructura que comparar, sino redaccion. Y aun asi son la mitad del catalogo, porque son
donde el organismo escribe **cuando entra en vigor** lo que los esquemas solo describen.

## El problema: el texto no tiene rutas canonicas

Un XSD se compara emparejando por ruta (`/Facturae/FileHeader/SchemaVersion`), que es
estable aunque el fichero se reordene. Un documento de texto no tiene nada equivalente:
si alguien inserta un parrafo en la pagina 3, todos los siguientes se desplazan, y
compararlos por posicion daria un documento entero de diferencias.

La salida es doble:

1. **El texto normalizado es la identidad del bloque.** Dos bloques con el mismo texto
   son el mismo bloque, esten donde esten. Mover un parrafo de sitio no es un cambio de
   contenido y no debe emitir nada.
2. **Lo que no empareja exacto se alinea con `difflib`**, que es determinista, y solo
   entonces se decide si es un alta, una baja o una reescritura.

La **seccion** (el encabezado bajo el que cuelga el bloque) se guarda como contexto para
que la ficha pueda decir «cambio el apartado 4.2», pero **no** forma parte de la clave:
renumerar los apartados renombraria todo el documento.

## Lo que se tira a proposito

Numeros de pagina, encabezados y pies repetidos en todas las paginas, y los espacios de
mas. Un PDF paginado mete el pie en cada pagina; si contaran, cambiar la fecha del pie
—que es justo lo que pasa cuando el organismo reexporta el documento— generaria tantos
cambios como paginas tenga.

## Lo que este normalizador NO hace

No interpreta. No sabe si un parrafo obliga a algo ni desde cuando. Eso es trabajo del
analista de la fase 2, y el apartado 7.4 lo pone en manos de una persona a proposito.
Aqui solo se acota **que texto cambio**, para que nadie tenga que leerse 60 paginas
buscandolo.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Final

from lxml import etree, html
from pypdf import PdfReader
from pypdf.errors import PdfReadError

#: Se sube a mano cuando cambia la semantica de la salida. Va en la clave unica de
#: `normalized_form`, de modo que el historico reprocesado convive con el anterior.
PARSER_VERSION: Final = "narrative/1"

#: Etiquetas HTML que producen un bloque. El resto se ignora: un `<div>` no dice nada
#: sobre el contenido y anidarlos produciria el mismo texto varias veces.
BLOCK_TAGS: Final = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "pre")

HEADING_TAGS: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

#: Ramas que nunca son contenido del documento.
DROP_TAGS: Final = ("script", "style", "nav", "header", "footer", "noscript", "form")

#: Un bloque mas corto que esto casi siempre es ruido de maquetacion: un numero de
#: pagina, una vineta suelta, el resto de una palabra partida.
MIN_BLOCK_CHARS: Final = 3

#: Lineas de cada borde de la pagina que se miran al buscar encabezados y pies.
EDGE_LINES: Final = 2

#: Tope de bloques. Un documento normativo tiene cientos; decenas de miles significa que
#: el extractor se ha encontrado algo que no es prosa.
DEFAULT_MAX_BLOCKS: Final = 5000

#: Un encabezado de PDF no viene etiquetado. Se reconoce por la forma: numeracion de
#: apartado al principio de una linea corta.
PDF_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[.\)]?\s+(\S.{0,110})$")

#: Un bloque cuya unica diferencia sea una cifra o una fecha es el que importa en un
#: documento normativo. Se detecta sobre el texto, no sobre el significado.
NUMERIC = re.compile(r"\d[\d.,/-]*")

_WHITESPACE = re.compile(r"\s+")
_DIGIT_RUN = re.compile(r"\d+")
_PAGE_NUMBER = re.compile(r"^(?:pag(?:ina)?\.?\s*)?\d{1,4}(?:\s*(?:/|de)\s*\d{1,4})?$", re.I)


@dataclass(frozen=True, slots=True)
class TextBlock:
    """Un parrafo, encabezado o celda del documento."""

    #: Texto con los espacios colapsados. Es la identidad del bloque.
    text: str
    kind: str
    #: Encabezado bajo el que cuelga. Contexto para la ficha, no parte de la clave.
    section: str | None
    #: Orden en el documento. Sirve para presentar, nunca para emparejar.
    position: int

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "section": self.section,
            "position": self.position,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TextBlock:
        return cls(
            text=data["text"],
            kind=data.get("kind", "paragraph"),
            section=data.get("section"),
            position=int(data.get("position", 0)),
        )


@dataclass(slots=True)
class TextForm:
    """Resultado de normalizar un documento narrativo."""

    blocks: list[TextBlock] = field(default_factory=list)
    source_format: str | None = None
    page_count: int | None = None
    declared_version: str | None = None
    is_partial: bool = False
    missing_dependencies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION

    def texts(self) -> list[str]:
        return [block.text for block in self.blocks]

    def by_text(self) -> dict[str, TextBlock]:
        """El primer bloque de cada texto. Los repetidos no aportan identidad."""
        found: dict[str, TextBlock] = {}
        for block in self.blocks:
            found.setdefault(block.text, block)
        return found

    def to_json(self) -> dict[str, Any]:
        return {
            "parser_version": self.parser_version,
            "source_format": self.source_format,
            "page_count": self.page_count,
            "declared_version": self.declared_version,
            "is_partial": self.is_partial,
            "missing_dependencies": list(self.missing_dependencies),
            "warnings": list(self.warnings),
            "blocks": [block.to_json() for block in self.blocks],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TextForm:
        return cls(
            blocks=[TextBlock.from_json(item) for item in data.get("blocks", [])],
            source_format=data.get("source_format"),
            page_count=data.get("page_count"),
            declared_version=data.get("declared_version"),
            is_partial=bool(data.get("is_partial", False)),
            missing_dependencies=list(data.get("missing_dependencies") or []),
            warnings=list(data.get("warnings") or []),
            parser_version=str(data.get("parser_version", PARSER_VERSION)),
        )


def normalize_narrative(
    content: bytes,
    content_kind: str,
    config: dict[str, Any] | None = None,
) -> TextForm:
    """Reduce un PDF o una pagina HTML a su lista de bloques de texto."""
    settings = dict(config or {})
    limit = _positive_int(settings.get("max_blocks"), DEFAULT_MAX_BLOCKS)

    if content_kind == "pdf":
        form = _from_pdf(content)
    elif content_kind in ("html", "xml"):
        form = _from_html(content)
    else:
        raise ValueError(f"no hay extractor de texto para contenido {content_kind}")

    if len(form.blocks) > limit:
        form.warnings.append(
            f"el documento dio {len(form.blocks)} bloques y el tope es {limit}: "
            f"probablemente no es prosa"
        )
        form.is_partial = True
        del form.blocks[limit:]

    if not form.blocks:
        form.warnings.append(
            "no se extrajo ningun bloque de texto: puede ser un PDF escaneado (imagenes "
            "sin capa de texto), que necesita OCR y todavia no se hace"
        )
        form.is_partial = True

    return form


# -- PDF --------------------------------------------------------------------------


def _from_pdf(content: bytes) -> TextForm:
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as error:
        # pypdf lanza de todo ante un PDF corrupto. Un documento ilegible no puede
        # tumbar la pasada: se marca y el artefacto ya esta guardado.
        raise ValueError(f"PDF ilegible: {error}") from error

    form = TextForm(source_format="pdf", page_count=len(pages))

    # Encabezados y pies: las lineas que se repiten en casi todas las paginas. Si
    # contaran, reexportar el documento con otra fecha al pie generaria tantos cambios
    # como paginas tenga.
    repeated = _repeated_templates(pages)
    if repeated:
        form.warnings.append(f"descartadas {len(repeated)} lineas de encabezado o pie repetidas")

    section: str | None = None
    position = 0
    for page in pages:
        # Los pies se quitan **antes** de unir lineas en parrafos. Si se quitaran despues,
        # un encabezado repetido en la parte de arriba de la pagina ya se habria pegado al
        # primer parrafo y lo contaminaria entero.
        for raw in _paragraphs(page, repeated):
            text = _collapse(raw)
            if not _is_content(text):
                continue

            heading = PDF_HEADING.match(text)
            if heading is not None:
                section = text
                kind = "heading"
            else:
                kind = "paragraph"

            form.blocks.append(TextBlock(text=text, kind=kind, section=section, position=position))
            position += 1

    return form


def _paragraphs(page_text: str, drop: set[str]) -> list[str]:
    """Une las lineas de un parrafo y separa por linea en blanco.

    Un PDF no tiene parrafos: tiene lineas colocadas. Sin unirlas, cualquier reflujo del
    texto —cambiar un margen, una fuente— movería el corte de cada linea y el documento
    entero saldria como modificado.

    `drop` son las plantillas de encabezado y pie. Una linea descartada **corta** el
    parrafo en vez de desaparecer sin mas: el pie separa dos parrafos que no tienen nada
    que ver, y pegarlos seria peor que dejarlo.
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in page_text.splitlines():
        stripped = line.strip()
        if stripped and _mask_digits(_collapse(stripped)) in drop:
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        if not stripped:
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        # Una linea corta que acaba en punto cierra parrafo; asi los titulos y las
        # entradas de lista no se pegan al parrafo siguiente.
        current.append(stripped)
        if len(stripped) < 60 and stripped.endswith((".", ":", "?")):
            blocks.append(" ".join(current))
            current = []
    if current:
        blocks.append(" ".join(current))
    return blocks


def _repeated_templates(pages: list[str]) -> set[str]:
    """Pies y encabezados, reconocidos **ignorando las cifras**.

    Compararlos literalmente no sirve: el pie de la FAQ de la AEAT es
    `«<numero de pagina> 4 de diciembre de 2025»`, que es distinto en cada pagina y aun
    asi es la misma linea. Enmascarando los digitos, las 52 paginas comparten plantilla y
    se descarta de una vez. Sin esto, cada pie entra como bloque propio y ademas la
    numeracion inicial lo hace pasar por encabezado de apartado.
    """
    if len(pages) < 4:
        return set()

    counter: Counter[str] = Counter()
    for page in pages:
        lines = [_collapse(line) for line in page.splitlines() if _collapse(line)]
        # Solo los bordes de la pagina. Un encabezado esta arriba y un pie abajo, por
        # definicion; mirar la pagina entera descartaria una fila de tabla que se repita
        # con solo un numero distinto, que si es contenido.
        edges = lines[:EDGE_LINES] + lines[-EDGE_LINES:]
        counter.update({_mask_digits(line) for line in edges})

    threshold = len(pages) // 2
    return {template for template, count in counter.items() if count > threshold}


def _mask_digits(text: str) -> str:
    return _DIGIT_RUN.sub("#", text)


# -- HTML -------------------------------------------------------------------------


def _from_html(content: bytes) -> TextForm:
    try:
        root = html.fromstring(content)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError) as error:
        raise ValueError(f"HTML ilegible: {error}") from error

    for tag in DROP_TAGS:
        for node in root.iter(tag):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

    form = TextForm(source_format="html")
    section: str | None = None
    position = 0
    seen: set[str] = set()

    for node in root.iter(*BLOCK_TAGS):
        if not isinstance(node, html.HtmlElement):
            continue
        text = _collapse(node.text_content() or "")
        if not _is_content(text):
            continue

        tag = str(node.tag)
        if tag in HEADING_TAGS:
            section = text
            kind = "heading"
        elif tag == "li":
            kind = "list_item"
        elif tag in ("td", "th"):
            kind = "table_cell"
        else:
            kind = "paragraph"

        # Un `<li>` que contiene un `<p>` produciria el texto dos veces.
        if text in seen:
            continue
        seen.add(text)

        form.blocks.append(TextBlock(text=text, kind=kind, section=section, position=position))
        position += 1

    return form


# -- utilidades -------------------------------------------------------------------


def _collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _is_content(text: str) -> bool:
    """Descarta el ruido de maquetacion: numeros de pagina y restos sueltos."""
    if len(text) < MIN_BLOCK_CHARS:
        return False
    return not _PAGE_NUMBER.match(text)


def numbers_in(text: str) -> list[str]:
    """Cifras y fechas del bloque. Es lo que separa una reescritura de un cambio real."""
    return NUMERIC.findall(text)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
