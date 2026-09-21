"""Elige el normalizador segun la fuente y el contenido, sin fiarse de la extension.

Facturae sirve sus XSD como `.xml`, la AEAT los sirve con `Content-Type` generico y el
paquete frances es un ZIP con varios XSD dentro. Mirar la extension o la cabecera da
resultados equivocados; se mira el contenido.

Lo que no se sabe normalizar no es un error: devuelve `NotNormalizable` con el motivo,
el artefacto se guarda igual y el servicio de ingesta emite un cambio de solo-hash para
que lo mire una persona.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from regwatch.core.enums import FormType, SourceKind
from regwatch.ingest.normalizers.html_index import IndexForm, normalize_html_index
from regwatch.ingest.normalizers.xsd import XsdForm, normalize_xsd

ContentKind = Literal["zip", "pdf", "html", "xml", "text", "binary"]

_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True, slots=True)
class Normalized:
    """Forma comparable lista para persistir en `normalized_form`."""

    form_type: str
    payload: dict[str, Any]
    parser_version: str
    is_partial: bool
    declared_version: str | None
    warnings: list[str] = field(default_factory=list)
    missing_dependencies: list[str] = field(default_factory=list)
    #: La forma tipada, para comparar sin volver a leer el payload. No se persiste.
    xsd_form: XsdForm | None = None
    index_form: IndexForm | None = None

    @property
    def typed_form(self) -> XsdForm | IndexForm | None:
        """La forma tipada, sea del tipo que sea. Quien compara no elige por atributo."""
        return self.xsd_form or self.index_form

    def parse_warnings(self) -> dict[str, Any] | None:
        """Lo que va a la columna `parse_warnings`. Nulo si no hay nada que contar."""
        if not self.warnings and not self.missing_dependencies:
            return None
        return {
            "warnings": list(self.warnings),
            "missing_dependencies": list(self.missing_dependencies),
        }


@dataclass(frozen=True, slots=True)
class NotNormalizable:
    """No hay normalizador para este contenido. El motivo se muestra al operador."""

    reason: str


NormalizationOutcome = Normalized | NotNormalizable


def sniff(content: bytes) -> ContentKind:
    """Clasifica el contenido por sus primeros bytes."""
    if content.startswith(b"PK\x03\x04"):
        return "zip"
    if content.startswith(b"%PDF"):
        return "pdf"

    head = content.removeprefix(_BOM)[:512].lstrip()
    lowered = head.lower()
    if lowered.startswith((b"<!doctype html", b"<html")):
        return "html"
    if lowered.startswith(b"<"):
        return "xml"

    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def normalize_content(
    source_kind: str,
    content: bytes,
    dependencies: dict[str, bytes] | None = None,
    *,
    base_url: str | None = None,
    config: dict[str, Any] | None = None,
) -> NormalizationOutcome:
    """Normaliza segun `source.source_kind`. Nunca lanza por contenido inesperado.

    `base_url` y `config` solo los usa el normalizador de indices: necesita resolver los
    enlaces relativos contra la pagina que los cita, y los selectores son propios de
    cada fuente.
    """
    if source_kind == SourceKind.SCHEMA:
        return _normalize_schema(content, dependencies)
    if source_kind == SourceKind.NARRATIVE:
        return NotNormalizable("normalizador de texto narrativo (PDF/HTML) pendiente: fase 2")
    if source_kind == SourceKind.INDEX:
        return _normalize_index(content, base_url, config)
    return NotNormalizable(f"sin normalizador para source_kind={source_kind!r}")


def _normalize_index(
    content: bytes, base_url: str | None, config: dict[str, Any] | None
) -> NormalizationOutcome:
    kind = sniff(content)
    if kind not in ("html", "xml"):
        # Una fuente de indice que un dia devuelve un PDF no es un indice ese dia. El
        # artefacto se guarda igual y el cambio de solo-hash lo pone delante de alguien.
        return NotNormalizable(f"contenido {kind}: no es una pagina de indice")

    try:
        form = normalize_html_index(content, base_url, config)
    except ValueError as error:
        # Un `link_xpath` mal escrito es culpa del operador, no del organismo: tiene que
        # verse como aviso de la fuente y no como una caida del colector.
        return NotNormalizable(f"indice que no se pudo normalizar: {error}")

    return Normalized(
        form_type=FormType.INDEX_ENTRIES.value,
        payload=form.to_json(),
        parser_version=form.parser_version,
        is_partial=form.is_partial,
        declared_version=form.declared_version,
        warnings=list(form.warnings),
        missing_dependencies=list(form.missing_dependencies),
        index_form=form,
    )


def _normalize_schema(
    content: bytes, dependencies: dict[str, bytes] | None
) -> NormalizationOutcome:
    kind = sniff(content)
    if kind == "zip":
        return NotNormalizable(
            "contenedor ZIP: el normalizador de paquetes (DGFiP, TicketBAI) esta pendiente"
        )
    if kind != "xml":
        return NotNormalizable(f"contenido {kind}: no es un XSD")

    try:
        form = normalize_xsd(content, dependencies)
    except ValueError as error:
        return NotNormalizable(f"XML que no es un XSD legible: {error}")

    return Normalized(
        form_type=FormType.XSD_ELEMENTS.value,
        payload=form.to_json(),
        parser_version=form.parser_version,
        is_partial=form.is_partial,
        declared_version=form.declared_version,
        warnings=list(form.warnings),
        missing_dependencies=list(form.missing_dependencies),
        xsd_form=form,
    )


def schema_locations(missing_dependencies: list[str]) -> list[str]:
    """Filtra las dependencias que son `schemaLocation` de verdad.

    El normalizador tambien anota `element:<nombre>` cuando una referencia apunta a un
    namespace que no tiene; eso no se puede descargar de ningun sitio.
    """
    return [item for item in missing_dependencies if not item.startswith("element:")]
