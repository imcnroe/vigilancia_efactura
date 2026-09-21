"""El detector contra los XSD reales de Facturae, contrastado con el listado que
publica el propio organismo.

Esto no es un test sintetico donde escribimos la pregunta y la respuesta: Facturae
publica 3.2, 3.2.1 y 3.2.2 simultaneamente y ademas publica **que cambio** entre 3.2.1
y 3.2.2. Eso convierte esa lista en un oraculo externo, y es la unica prueba del
proyecto que puede decir que el detector acierta sobre un caso verdadero.

Los ficheros no estan en el repositorio (pesan 180 KB cada uno y son artefactos
descargados). Se bajan con:

    python scripts/fetch_fixtures.py

Si no estan, el modulo se salta. No van al repositorio, pero su SHA-256 si, en
`manifest.json`: estos tests verifican el hash antes de mirar el contenido, porque un
fichero distinto al que se congelo invalidaria cualquier conclusion.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from regwatch.core.enums import ChangeType, Severity
from regwatch.ingest.diff.xsd_diff import StructuralDiff, compare_xsd
from regwatch.ingest.normalizers.xsd import XsdForm, normalize_xsd

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "xsd"
MANIFEST = FIXTURES / "manifest.json"

V_321 = "facturae_3_2_1.xsd"
V_322 = "facturae_3_2_2.xsd"

#: La firma XML-DSig se importa de la W3C y no se descarga: el normalizador marca la
#: forma como parcial y sigue, que es lo que el apartado 7.2 exige.
XMLDSIG = "http://www.w3.org/TR/xmldsig-core/xmldsig-core-schema.xsd"


def _load(name: str) -> XsdForm:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"falta {name}: ejecuta `python scripts/fetch_fixtures.py`")

    expected = json.loads(MANIFEST.read_text(encoding="utf-8")).get(name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == expected, (
        f"{name} no coincide con el manifiesto. O el organismo lo ha resustituido —que es "
        f"el suceso que este producto existe para detectar— o el fichero local esta "
        f"alterado. Revisa con `python scripts/fetch_fixtures.py`."
    )
    return normalize_xsd(path.read_bytes())


@pytest.fixture(scope="module")
def diff() -> StructuralDiff:
    return compare_xsd(_load(V_321), _load(V_322))


# -- normalizador sobre esquemas de verdad ---------------------------------------


@pytest.mark.parametrize(
    ("name", "version", "namespace"),
    [
        (V_321, "3.2.1", "http://www.facturae.es/Facturae/2014/v3.2.1/Facturae"),
        (V_322, "3.2.2", "http://www.facturae.gob.es/formato/Versiones/Facturaev3_2_2.xml"),
    ],
)
def test_real_schema_is_normalized(name: str, version: str, namespace: str) -> None:
    form = _load(name)
    assert form.declared_version == version
    assert form.target_namespace == namespace
    # Un esquema de factura completo: si esto baja de golpe, el parser se ha roto.
    assert len(form.elements) > 500

    # Parcial solo por la firma. Cualquier otra dependencia sin resolver es un problema.
    assert form.is_partial
    assert [d for d in form.missing_dependencies if not d.startswith("element:")] == [XMLDSIG]


def test_canonical_paths_are_stable_across_versions() -> None:
    """La ruta canonica es la clave con la que se emparejan versiones. Si fuera
    inestable, cada publicacion generaria cientos de falsos positivos."""
    common = _load(V_321).by_path().keys() & _load(V_322).by_path().keys()
    # 3.2.1 tiene 610 elementos y 3.2.2 626: casi todo tiene que emparejar.
    assert len(common) >= 600
    assert "/Facturae/FileHeader/SchemaVersion" in common
    assert "/Facturae/Invoices/Invoice/InvoiceTotals/TotalGrossAmount" in common


# -- contraste con el listado oficial de cambios 3.2.1 -> 3.2.2 ------------------


def test_the_diff_is_small_and_has_no_removals(diff: StructuralDiff) -> None:
    """Una version menor no quita campos. Si aparecen bajas o renombrados, o el detector
    se ha equivocado o el organismo ha hecho algo muy gordo."""
    assert diff.of_type(ChangeType.FIELD_REMOVED) == []
    assert diff.of_type(ChangeType.ELEMENT_RENAMED) == []
    assert diff.of_type(ChangeType.CARDINALITY_TIGHTENED) == []
    assert diff.of_type(ChangeType.TYPE_CHANGED) == []
    # Un cambio de version menor con veintitantas diferencias es lo esperable; cientos
    # significaria que el emparejamiento por ruta se ha roto.
    assert 10 <= len(diff.items) <= 40, Counter(i.change_type.value for i in diff.items)


def test_factoring_block_is_detected(diff: StructuralDiff) -> None:
    """«Campos nuevos de cesión de factoring», segun el organismo."""
    paths = {item.path for item in diff.items}
    assert "/Facturae/FileHeader/FactoringAssignmentData/FactoringAssignmentDocument" in paths
    assert len({p for p in paths if "FactoringAssignmentDocument" in p}) >= 6, (
        "el bloque de factoring trae varios campos"
    )


def test_corrective_invoice_issue_date_is_detected(diff: StructuralDiff) -> None:
    """«InvoiceIssueDate para rectificativas»."""
    assert any(
        item.path == "/Facturae/Invoices/Invoice/InvoiceHeader/Corrective/InvoiceIssueDate"
        for item in diff.items
    )


def test_invoice_description_is_detected(diff: StructuralDiff) -> None:
    """«InvoiceDescription»."""
    assert any(
        item.path == "/Facturae/Invoices/Invoice/InvoiceIssueData/InvoiceDescription"
        for item in diff.items
    )


def test_payment_in_kind_block_is_detected(diff: StructuralDiff) -> None:
    """«El bloque de pago en especie»."""
    paths = {item.path for item in diff.items}
    assert "/Facturae/Invoices/Invoice/InvoiceTotals/PaymentInKind" in paths
    assert "/Facturae/Invoices/Invoice/InvoiceTotals/PaymentInKind/PaymentInKindAmount" in paths


def test_the_two_new_enumeration_values_are_detected(diff: StructuralDiff) -> None:
    """«Dos altas en enumerados: KWh como unidad de medida, HTML como formato de adjunto».

    KWh entra con el codigo `36`: el organismo describe el cambio con el nombre de la
    unidad, pero el esquema solo lleva el codigo. Es justo la clase de traduccion que
    hace falta al redactar la ficha, y el motivo de que el analista redacte y no
    descubra.
    """
    added = {
        item.path: item.after
        for item in diff.items
        if item.change_type is ChangeType.ENUM_VALUE_ADDED
    }
    assert added[
        "/Facturae/Invoices/Invoice/AdditionalData/RelatedDocuments/Attachment/AttachmentFormat"
    ] == ["html"]
    assert added["/Facturae/Invoices/Invoice/Items/InvoiceLine/UnitOfMeasure"] == ["36"]


# -- el hallazgo que solo aparece con esquemas reales ----------------------------


def test_no_false_blocking_from_mandatory_fields_in_new_optional_blocks(
    diff: StructuralDiff,
) -> None:
    """El bloque de factoring y el de pago en especie entraron como opcionales, con hijos
    obligatorios dentro. Sin la regla de la rama opcional, esta version menor —que no
    obliga a nadie a nada— saldria con seis cambios bloqueantes. El apartado 1 declara
    el falso positivo inaceptable, asi que este es el test que lo guarda.
    """
    mandatory = diff.of_type(ChangeType.FIELD_ADDED_MANDATORY)
    assert mandatory, "3.2.2 si trae campos obligatorios, dentro de ramas opcionales"

    for item in mandatory:
        assert item.severity is Severity.INFO, item.path
        assert "mandatory_within_new_optional_branch" in item.detail, item.path


def test_only_the_schema_version_enum_is_blocking(diff: StructuralDiff) -> None:
    """Lo unico bloqueante que queda es `SchemaVersion`, que deja de admitir `3.2.1` y
    pasa a admitir `3.2.2`. Es real —un fichero que declare la version antigua no valida
    contra el esquema nuevo— pero es inherente al cambio de version, no una obligacion
    nueva de negocio. Se deja visible para que el editor humano decida como contarlo.
    """
    blocking = [item for item in diff.items if item.severity is Severity.BLOCKING]
    assert {item.path for item in blocking} == {"/Facturae/FileHeader/SchemaVersion"}
    assert {item.change_type for item in blocking} == {ChangeType.ENUM_VALUE_REMOVED}
