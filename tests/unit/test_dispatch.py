"""Eleccion de normalizador por contenido y viaje de ida y vuelta de la forma XSD."""

from __future__ import annotations

import pytest

from regwatch.core.enums import FormType, SourceKind
from regwatch.ingest.diff.xsd_diff import compare_xsd
from regwatch.ingest.normalizers.dispatch import (
    Normalized,
    NotNormalizable,
    normalize_content,
    schema_locations,
    sniff,
)
from regwatch.ingest.normalizers.xsd import XsdForm, normalize_xsd

XSD = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           targetNamespace="http://example.org/i" xmlns="http://example.org/i" version="3.2.1">
  <xs:element name="Invoice">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="Number" type="xs:string"/>
        <xs:element name="Currency" minOccurs="0">
          <xs:simpleType>
            <xs:restriction base="xs:string">
              <xs:enumeration value="EUR"/>
              <xs:enumeration value="USD"/>
              <xs:maxLength value="3"/>
            </xs:restriction>
          </xs:simpleType>
        </xs:element>
        <xs:element name="Lines" maxOccurs="unbounded" type="xs:string"/>
      </xs:sequence>
      <xs:attribute name="version" type="xs:string" use="required"/>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

XSD_WITH_IMPORT = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
           targetNamespace="http://example.org/i" xmlns="http://example.org/i">
  <xs:import namespace="http://www.w3.org/2000/09/xmldsig#" schemaLocation="xmldsig-core-schema.xsd"/>
  <xs:element name="Invoice" type="xs:string"/>
</xs:schema>"""


# -- sniff -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"PK\x03\x04" + b"\x00" * 20, "zip"),
        (b"%PDF-1.7\n%...", "pdf"),
        (b"<!DOCTYPE html><html><body>x</body></html>", "html"),
        (b"  \n<html lang='es'>", "html"),
        (b'<?xml version="1.0"?><xs:schema/>', "xml"),
        (b"\xef\xbb\xbf<?xml version='1.0'?><a/>", "xml"),
        (b"hola, esto es texto plano", "text"),
        (b"\x89PNG\r\n\x1a\n" + b"\xff" * 10, "binary"),
    ],
)
def test_sniff_by_leading_bytes(content: bytes, expected: str) -> None:
    assert sniff(content) == expected


# -- normalize_content -----------------------------------------------------------


def test_schema_source_with_xsd_is_normalized() -> None:
    outcome = normalize_content(SourceKind.SCHEMA, XSD)
    assert isinstance(outcome, Normalized)
    assert outcome.form_type == FormType.XSD_ELEMENTS
    assert outcome.declared_version == "3.2.1"
    assert not outcome.is_partial
    assert outcome.xsd_form is not None
    assert len(outcome.payload["elements"]) == len(outcome.xsd_form.elements) == 5
    assert outcome.parse_warnings() is None


def test_schema_source_with_pdf_is_not_normalizable_but_explains_why() -> None:
    outcome = normalize_content(SourceKind.SCHEMA, b"%PDF-1.4 ...")
    assert isinstance(outcome, NotNormalizable)
    assert "pdf" in outcome.reason


def test_schema_source_with_zip_names_the_pending_normalizer() -> None:
    outcome = normalize_content(SourceKind.SCHEMA, b"PK\x03\x04rest")
    assert isinstance(outcome, NotNormalizable)
    assert "ZIP" in outcome.reason


def test_xml_that_is_not_a_schema_is_not_normalizable() -> None:
    outcome = normalize_content(SourceKind.SCHEMA, b"<?xml version='1.0'?><Factura/>")
    assert isinstance(outcome, NotNormalizable)
    assert "Factura" in outcome.reason


def test_broken_xml_does_not_raise() -> None:
    outcome = normalize_content(SourceKind.SCHEMA, b"<xs:schema xmlns:xs='x'><unclosed>")
    assert isinstance(outcome, NotNormalizable)


@pytest.mark.parametrize("kind", ["SANDBOX"])
def test_other_source_kinds_have_no_normalizer_yet(kind: str) -> None:
    outcome = normalize_content(kind, XSD)
    assert isinstance(outcome, NotNormalizable)


def test_an_index_source_that_serves_a_schema_is_not_normalized_as_an_index() -> None:
    """El `source_kind` dice que se espera, no que ha llegado. Un XSD por una fuente de
    indice no se fuerza: se guarda el artefacto y el aviso lo pone delante de alguien."""
    outcome = normalize_content(SourceKind.INDEX, XSD)
    assert isinstance(outcome, Normalized)
    assert outcome.form_type == "INDEX_ENTRIES"
    # Un XSD no tiene enlaces, asi que la lista sale vacia y la forma, parcial.
    assert outcome.payload["entries"] == []
    assert outcome.is_partial


def test_missing_import_is_reported_and_form_is_partial() -> None:
    outcome = normalize_content(SourceKind.SCHEMA, XSD_WITH_IMPORT)
    assert isinstance(outcome, Normalized)
    assert outcome.is_partial
    assert outcome.missing_dependencies == ["xmldsig-core-schema.xsd"]
    assert outcome.parse_warnings() == {
        "warnings": [],
        "missing_dependencies": ["xmldsig-core-schema.xsd"],
    }


def test_dependency_resolves_the_import() -> None:
    dependency = (
        b'<?xml version="1.0"?><xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        b'targetNamespace="http://www.w3.org/2000/09/xmldsig#"/>'
    )
    outcome = normalize_content(
        SourceKind.SCHEMA, XSD_WITH_IMPORT, {"xmldsig-core-schema.xsd": dependency}
    )
    assert isinstance(outcome, Normalized)
    assert not outcome.is_partial


def test_schema_locations_drops_unresolved_element_references() -> None:
    assert schema_locations(["element:Signature", "xmldsig-core-schema.xsd", "element:X"]) == [
        "xmldsig-core-schema.xsd"
    ]


# -- ida y vuelta de la forma ----------------------------------------------------


def test_form_survives_json_round_trip() -> None:
    """Lo que se guarda en `normalized_form.payload` tiene que valer para comparar."""
    original = normalize_xsd(XSD)
    restored = XsdForm.from_json(original.to_json())

    assert restored.elements == original.elements
    assert restored.declared_version == original.declared_version
    assert restored.target_namespace == original.target_namespace
    assert restored.parser_version == original.parser_version
    assert restored.is_partial == original.is_partial
    assert compare_xsd(original, restored).is_empty


def test_round_trip_preserves_unbounded_and_enumerations() -> None:
    restored = XsdForm.from_json(normalize_xsd(XSD).to_json()).by_path()
    assert restored["/Invoice/Lines"].max_occurs == -1
    assert restored["/Invoice/Currency"].enumerations == ("EUR", "USD")
    assert restored["/Invoice/Currency"].restrictions == {"maxLength": "3"}
    assert restored["/Invoice/@version"].is_attribute
    assert restored["/Invoice/@version"].min_occurs == 1
