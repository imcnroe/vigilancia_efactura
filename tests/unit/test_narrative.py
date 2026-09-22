"""Normalizador y detector de documentos narrativos.

El PDF de los tests se construye aqui, byte a byte, en vez de descargarse: un test que
necesita internet para pasar es un test que un dia falla por motivos que no tienen que
ver con el codigo, y un PDF de 700 KB en el repositorio no dice nada que no diga uno de
veinte lineas que podemos leer entero.
"""

from __future__ import annotations

import pytest

from regwatch.core.enums import ChangeType, Severity, SourceKind
from regwatch.ingest.diff.text_diff import MAX_ITEMS, compare_text
from regwatch.ingest.normalizers.dispatch import Normalized, NotNormalizable, normalize_content
from regwatch.ingest.normalizers.narrative import TextForm, normalize_narrative

# -- un PDF de verdad, hecho a mano ----------------------------------------------


def make_pdf(pages: list[list[str]]) -> bytes:
    """Un PDF valido y minimo con las lineas dadas, una por posicion vertical.

    Sin dependencias de generacion: se escriben los objetos y la tabla `xref` con sus
    desplazamientos reales, que es lo unico que un lector necesita.
    """

    def escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    font_id = 3
    objects: dict[int, bytes] = {
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }

    page_ids: list[int] = []
    next_id = 4
    for lines in pages:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)

        drawn = ["BT", "/F1 12 Tf"]
        y = 750
        for line in lines:
            drawn.append(f"1 0 0 1 72 {y} Tm ({escape(line)}) Tj")
            y -= 20
        drawn.append("ET")
        stream = "\n".join(drawn).encode("latin-1")

        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
            + str(content_id).encode()
            + b" 0 R /Resources << /Font << /F1 "
            + str(font_id).encode()
            + b" 0 R >> >> >>"
        )

    kids = b" ".join(f"{page_id} 0 R".encode() for page_id in page_ids)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += str(number).encode() + b" 0 obj\n" + objects[number] + b"\nendobj\n"

    start = len(out)
    count = max(objects) + 1
    out += b"xref\n0 " + str(count).encode() + b"\n0000000000 65535 f \n"
    for number in range(1, count):
        offset = offsets.get(number, 0)
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(count).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(start).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def pdf_form(pages: list[list[str]]) -> TextForm:
    return normalize_narrative(make_pdf(pages), "pdf")


def html_form(body: str) -> TextForm:
    return normalize_narrative(f"<!DOCTYPE html><html><body>{body}</body></html>".encode(), "html")


# -- el PDF de prueba es legible --------------------------------------------------


def test_the_handmade_pdf_is_readable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Si esto falla, el resto de los tests de PDF no prueban lo que creen probar."""
    form = pdf_form([["Primera linea.", "Segunda linea."]])
    assert form.source_format == "pdf"
    assert form.page_count == 1
    assert "Primera linea." in form.texts()


# -- normalizador de PDF ----------------------------------------------------------


def test_page_footers_are_dropped_even_though_the_number_changes() -> None:
    """Caso real de la FAQ de la AEAT: el pie es `<pagina> 4 de diciembre de 2025`, que
    es distinto en cada pagina y aun asi es la misma linea. Sin enmascarar las cifras,
    los 52 pies entraban como bloques y ademas pasaban por encabezados de apartado."""
    cuerpos = [
        "El periodo transitorio termina cuando lo diga la norma.",
        "Las arquitecturas admitidas son local y remota.",
        "La personalizacion de software no altera la responsabilidad.",
        "Los borradores de factura no son registros de facturacion.",
        "El hash encadena cada registro con el anterior.",
        "El QR se imprime en la factura en formato legible.",
        "La declaracion responsable la firma el productor.",
        "Los ficheros de anulacion siguen el mismo esquema.",
    ]
    pages = [[f"{n} 4 de diciembre de 2025", cuerpo] for n, cuerpo in enumerate(cuerpos, start=1)]
    form = pdf_form(pages)

    assert not [text for text in form.texts() if "diciembre" in text]
    assert any("descartadas" in warning for warning in form.warnings)
    # Un bloque por pagina: el cuerpo. Los ocho pies se van de una vez.
    assert form.texts() == cuerpos


def test_a_numbered_title_is_a_heading_and_labels_what_comes_after() -> None:
    form = pdf_form(
        [
            [
                "1. PERIODO TRANSITORIO.",
                "Los sistemas deberan adaptarse.",
                "2. ARQUITECTURAS DE LOS SIF.",
                "Un SIF puede ser local o remoto.",
            ]
        ]
    )

    kinds = {block.text: block.kind for block in form.blocks}
    assert kinds["1. PERIODO TRANSITORIO."] == "heading"
    assert kinds["Los sistemas deberan adaptarse."] == "paragraph"

    sections = {block.text: block.section for block in form.blocks}
    assert sections["Los sistemas deberan adaptarse."] == "1. PERIODO TRANSITORIO."
    assert sections["Un SIF puede ser local o remoto."] == "2. ARQUITECTURAS DE LOS SIF."


def test_page_numbers_alone_are_not_content() -> None:
    form = pdf_form([["Un parrafo con suficiente texto.", "12"], ["Otro parrafo distinto.", "13"]])
    assert "12" not in form.texts()
    assert "13" not in form.texts()


def test_an_unreadable_pdf_does_not_bring_down_the_pass() -> None:
    with pytest.raises(ValueError, match="PDF ilegible"):
        normalize_narrative(b"%PDF-1.4\nesto no es un PDF\n", "pdf")


def test_a_pdf_without_a_text_layer_is_partial_not_empty() -> None:
    """Un PDF escaneado son imagenes. Decir «no hay texto» sin mas haria que el detector
    lo leyera como que han borrado el documento entero."""
    form = pdf_form([[]])
    assert form.blocks == []
    assert form.is_partial
    assert any("OCR" in warning for warning in form.warnings)


# -- normalizador de HTML ---------------------------------------------------------


def test_html_blocks_carry_their_kind_and_section() -> None:
    form = html_form(
        "<h2>4.2 Plazo de remision</h2>"
        "<p>El plazo es de cuatro dias naturales.</p>"
        "<ul><li>Primer supuesto.</li><li>Segundo supuesto.</li></ul>"
        "<table><tr><td>Celda con texto.</td></tr></table>"
    )
    kinds = {block.text: block.kind for block in form.blocks}
    assert kinds["4.2 Plazo de remision"] == "heading"
    assert kinds["El plazo es de cuatro dias naturales."] == "paragraph"
    assert kinds["Primer supuesto."] == "list_item"
    assert kinds["Celda con texto."] == "table_cell"

    sections = {block.text: block.section for block in form.blocks}
    assert sections["El plazo es de cuatro dias naturales."] == "4.2 Plazo de remision"


def test_navigation_and_scripts_are_not_document_content() -> None:
    form = html_form(
        "<nav><p>Inicio Contacto</p></nav>"
        "<script>var x = 1;</script>"
        "<footer><p>Aviso legal</p></footer>"
        "<p>Esto si es el documento.</p>"
    )
    assert form.texts() == ["Esto si es el documento."]


def test_whitespace_is_collapsed_so_reflowing_is_not_a_change() -> None:
    assert html_form("<p>Un   parrafo\n\n  con  espacios raros.</p>").texts() == [
        "Un parrafo con espacios raros."
    ]


def test_the_form_survives_a_round_trip_through_json() -> None:
    original = html_form("<h1>Titulo</h1><p>Un parrafo cualquiera.</p>")
    assert TextForm.from_json(original.to_json()).to_json() == original.to_json()


# -- despachador ------------------------------------------------------------------


def test_the_dispatcher_produces_a_text_blocks_form_for_a_pdf() -> None:
    outcome = normalize_content(
        SourceKind.NARRATIVE, make_pdf([["Un documento narrativo cualquiera."]])
    )
    assert isinstance(outcome, Normalized)
    assert outcome.form_type == "TEXT_BLOCKS"
    assert outcome.parser_version == "narrative/1"
    assert outcome.text_form is not None
    assert outcome.typed_form is outcome.text_form


def test_a_zip_served_by_a_narrative_source_is_not_forced_through_the_parser() -> None:
    outcome = normalize_content(SourceKind.NARRATIVE, b"PK\x03\x04nada")
    assert isinstance(outcome, NotNormalizable)
    assert "zip" in outcome.reason


# -- detector ---------------------------------------------------------------------


def test_moving_a_paragraph_is_not_a_change() -> None:
    """El texto es la identidad del bloque: si aparece igual, no ha cambiado, este en la
    pagina que este."""
    before = html_form("<p>Parrafo uno completo.</p><p>Parrafo dos completo.</p>")
    after = html_form("<p>Parrafo dos completo.</p><p>Parrafo uno completo.</p>")
    assert compare_text(before, after).is_empty


def test_a_rewrite_that_keeps_every_figure_is_only_informative() -> None:
    before = html_form("<p>El plazo de remision sera de 4 dias naturales.</p>")
    after = html_form("<p>El plazo para la remision sera de 4 dias naturales.</p>")

    (item,) = compare_text(before, after).items
    assert item.change_type is ChangeType.TEXT_BLOCK_CHANGED
    assert item.severity is Severity.INFO
    assert item.detail["reason"] == "rewritten_without_changing_any_figure"


def test_a_figure_that_changes_is_what_the_analyst_has_to_see() -> None:
    """Es la heuristica del detector, y esta puesta a proposito: no sabemos que significa
    el parrafo, solo que sus cifras ya no son las mismas. En un documento normativo eso
    es una fecha de entrada en vigor, un plazo o un importe."""
    before = html_form("<p>El plazo de remision sera de 4 dias naturales.</p>")
    after = html_form("<p>El plazo de remision sera de 8 dias naturales.</p>")

    (item,) = compare_text(before, after).items
    assert item.change_type is ChangeType.TEXT_BLOCK_CHANGED
    assert item.severity is Severity.REQUIRES_CHANGE
    assert item.detail["reason"] == "numbers_changed"
    assert item.detail["numbers_before"] == ["4"]
    assert item.detail["numbers_after"] == ["8"]


def test_a_figure_written_out_in_words_is_a_known_blind_spot() -> None:
    """Limitacion conocida, escrita a proposito para que nadie la descubra en produccion.

    La heuristica mira digitos. Un plazo redactado en letra —«cuatro dias naturales»
    pasa a «ocho dias naturales»— es un cambio real que sale como `INFO`. No se persigue
    porque hacerlo bien exige entender el texto en castellano y en frances, y un
    detector que casi entiende es peor que uno que dice claramente hasta donde llega: el
    cambio **si** se emite y queda acotado al parrafo, solo que sin subir la severidad.
    """
    before = html_form("<p>El plazo de remision sera de cuatro dias naturales.</p>")
    after = html_form("<p>El plazo de remision sera de ocho dias naturales.</p>")

    (item,) = compare_text(before, after).items
    assert item.change_type is ChangeType.TEXT_BLOCK_CHANGED
    assert item.severity is Severity.INFO
    assert item.detail["reason"] == "rewritten_without_changing_any_figure"


def test_the_section_travels_with_the_change_so_the_ficha_can_cite_it() -> None:
    before = html_form("<h2>4.2 Plazo</h2><p>El plazo sera de 4 dias naturales.</p>")
    after = html_form("<h2>4.2 Plazo</h2><p>El plazo sera de 8 dias naturales.</p>")

    (item,) = compare_text(before, after).items
    assert item.detail["section"] == "4.2 Plazo"
    assert item.path.startswith("4.2 Plazo")


def test_a_new_paragraph_is_an_addition_not_a_rewrite() -> None:
    before = html_form("<p>Un parrafo que se queda igual.</p>")
    after = html_form(
        "<p>Un parrafo que se queda igual.</p><p>Algo completamente distinto y nuevo aqui.</p>"
    )

    (item,) = compare_text(before, after).items
    assert item.change_type is ChangeType.TEXT_BLOCK_ADDED
    assert item.severity is Severity.INFO


def test_a_removed_paragraph_is_reported() -> None:
    before = html_form("<p>Un parrafo que se queda.</p><p>Otro que van a quitar del todo.</p>")
    after = html_form("<p>Un parrafo que se queda.</p>")

    (item,) = compare_text(before, after).items
    assert item.change_type is ChangeType.TEXT_BLOCK_REMOVED


def test_two_unrelated_texts_are_not_paired_as_a_rewrite() -> None:
    """Emparejar por debajo del umbral produciria un «cambio» ilegible entre dos parrafos
    que no tienen nada que ver."""
    before = html_form("<p>El plazo de remision sera de cuatro dias.</p>")
    after = html_form("<p>Las arquitecturas admitidas incluyen sistemas remotos.</p>")

    types = {item.change_type for item in compare_text(before, after).items}
    assert types == {ChangeType.TEXT_BLOCK_ADDED, ChangeType.TEXT_BLOCK_REMOVED}


def test_an_empty_version_does_not_report_the_whole_document_as_removed() -> None:
    before = html_form("<p>Un documento con su contenido.</p><p>Y un segundo parrafo.</p>")
    after = normalize_narrative(make_pdf([[]]), "pdf")

    diff = compare_text(before, after)
    assert diff.items == []
    assert diff.is_partial
    assert any("no dio texto" in note for note in diff.notes)


def test_a_complete_reedition_is_truncated_and_says_so() -> None:
    """Miles de parrafos en un JSONB no los lee nadie, y el numero ya dice lo que pasa."""
    before = html_form(
        "".join(f"<p>Parrafo original numero {n} del documento.</p>" for n in range(400))
    )
    after = html_form(
        "".join(f"<p>Texto completamente rehecho, entrada {n} aqui.</p>" for n in range(400))
    )

    diff = compare_text(before, after)
    assert len(diff.items) == MAX_ITEMS
    assert diff.is_partial
    assert any("reedicion completa" in note for note in diff.notes)


def test_the_diff_carries_its_schema_version() -> None:
    before = html_form("<p>Un parrafo cualquiera del documento.</p>")
    after = html_form("<p>Un parrafo cualquiera del documento, ampliado.</p>")
    assert compare_text(before, after).to_json()["diff_schema_version"] == "text-diff/1"
