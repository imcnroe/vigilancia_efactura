"""Tests del normalizador.

El foco esta en la estabilidad de la ruta canonica y en los casos que hacen fallar a
un parser ingenuo: `choice`, tipos anonimos, herencia por extension, recursion y
dependencias sin resolver.
"""

from __future__ import annotations

import pytest

from regwatch.ingest.normalizers.xsd import (
    PARSER_VERSION,
    UNBOUNDED,
    normalize_xsd,
)

HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'targetNamespace="http://example.org/i" xmlns="http://example.org/i" version="2.5">'
)


def wrap(body: str) -> bytes:
    return f"{HEADER}\n{body}\n</xs:schema>".encode()


def test_reads_declared_version_and_namespace() -> None:
    form = normalize_xsd(wrap('<xs:element name="Root" type="xs:string"/>'))
    assert form.declared_version == "2.5"
    assert form.target_namespace == "http://example.org/i"
    assert form.parser_version == PARSER_VERSION


def test_flattens_nested_elements_into_canonical_paths() -> None:
    form = normalize_xsd(
        wrap("""
  <xs:element name="Invoice">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="Header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="Number" type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>""")
    )
    paths = [element.path for element in form.elements]
    assert paths == ["/Invoice", "/Invoice/Header", "/Invoice/Header/Number"]


def test_attributes_use_the_at_sign_and_required_becomes_min_occurs_one() -> None:
    form = normalize_xsd(
        wrap("""
  <xs:element name="Invoice">
    <xs:complexType>
      <xs:attribute name="currency" type="xs:string" use="required"/>
      <xs:attribute name="note" type="xs:string"/>
    </xs:complexType>
  </xs:element>""")
    )
    by_path = form.by_path()
    assert by_path["/Invoice/@currency"].min_occurs == 1
    assert by_path["/Invoice/@currency"].is_attribute is True
    assert by_path["/Invoice/@note"].min_occurs == 0


def test_choice_is_recorded_in_container_not_in_the_path() -> None:
    """Reordenar un choice no puede generar ruido: el compositor va fuera de la ruta."""
    form = normalize_xsd(
        wrap("""
  <xs:element name="Party">
    <xs:complexType>
      <xs:choice>
        <xs:element name="LegalEntity" type="xs:string"/>
        <xs:element name="Individual" type="xs:string"/>
      </xs:choice>
    </xs:complexType>
  </xs:element>""")
    )
    by_path = form.by_path()
    assert by_path["/Party/LegalEntity"].container == "choice"
    assert by_path["/Party/Individual"].container == "choice"


def test_unbounded_is_normalised_to_a_sentinel() -> None:
    form = normalize_xsd(
        wrap("""
  <xs:element name="Invoices">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="Invoice" type="xs:string" maxOccurs="unbounded"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>""")
    )
    assert form.by_path()["/Invoices/Invoice"].max_occurs == UNBOUNDED


def test_facets_and_enumerations_travel_up_the_base_chain() -> None:
    form = normalize_xsd(
        wrap("""
  <xs:element name="Unit" type="UnitType"/>
  <xs:simpleType name="BaseText">
    <xs:restriction base="xs:string">
      <xs:maxLength value="30"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="UnitType">
    <xs:restriction base="BaseText">
      <xs:enumeration value="EA"/>
      <xs:enumeration value="KG"/>
    </xs:restriction>
  </xs:simpleType>""")
    )
    element = form.by_path()["/Unit"]
    assert element.enumerations == ("EA", "KG")
    assert element.restrictions["maxLength"] == "30"


def test_extension_pulls_in_the_base_type_members() -> None:
    form = normalize_xsd(
        wrap("""
  <xs:element name="Line" type="DetailedLine"/>
  <xs:complexType name="BaseLine">
    <xs:sequence>
      <xs:element name="Description" type="xs:string"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="DetailedLine">
    <xs:complexContent>
      <xs:extension base="BaseLine">
        <xs:sequence>
          <xs:element name="Quantity" type="xs:double"/>
        </xs:sequence>
      </xs:extension>
    </xs:complexContent>
  </xs:complexType>""")
    )
    paths = set(form.by_path())
    assert "/Line/Description" in paths
    assert "/Line/Quantity" in paths


def test_recursive_types_terminate() -> None:
    """Un tipo que se contiene a si mismo no puede colgar el proceso."""
    form = normalize_xsd(
        wrap("""
  <xs:element name="Node" type="NodeType"/>
  <xs:complexType name="NodeType">
    <xs:sequence>
      <xs:element name="Label" type="xs:string"/>
      <xs:element name="Child" type="NodeType" minOccurs="0"/>
    </xs:sequence>
  </xs:complexType>""")
    )
    paths = set(form.by_path())
    assert "/Node/Label" in paths
    assert "/Node/Child" in paths
    assert len(form.elements) < 10


def test_missing_import_marks_the_form_partial_instead_of_failing() -> None:
    form = normalize_xsd(
        wrap("""
  <xs:import namespace="http://other" schemaLocation="http://other/other.xsd"/>
  <xs:element name="Invoice" type="xs:string"/>""")
    )
    assert form.is_partial is True
    assert "http://other/other.xsd" in form.missing_dependencies
    assert form.by_path()["/Invoice"] is not None


def test_import_is_resolved_from_the_supplied_dependencies_only() -> None:
    other = (
        b'<?xml version="1.0"?>'
        b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        b'  <xs:complexType name="SignatureType">'
        b'    <xs:sequence><xs:element name="Value" type="xs:string"/></xs:sequence>'
        b"  </xs:complexType>"
        b"</xs:schema>"
    )

    form = normalize_xsd(
        wrap("""
  <xs:include schemaLocation="signature.xsd"/>
  <xs:element name="Signature" type="SignatureType"/>"""),
        dependencies={"signature.xsd": other},
    )
    assert form.is_partial is False
    assert "/Signature/Value" in form.by_path()


def test_import_without_location_is_not_a_broken_dependency() -> None:
    form = normalize_xsd(
        wrap("""
  <xs:import namespace="http://other"/>
  <xs:element name="Invoice" type="xs:string"/>""")
    )
    assert form.missing_dependencies == []


def test_annotation_whitespace_is_collapsed() -> None:
    """Los XSD oficiales cambian tabulaciones entre publicaciones sin cambiar el texto."""
    form = normalize_xsd(
        wrap("""
  <xs:element name="Number" type="xs:string">
    <xs:annotation>
      <xs:documentation xml:lang="es">
          Numero    de
          factura.
      </xs:documentation>
    </xs:annotation>
  </xs:element>""")
    )
    assert form.by_path()["/Number"].annotation == "Numero de factura."


def test_output_is_deterministic() -> None:
    body = """
  <xs:element name="Invoice">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="Zeta" type="xs:string"/>
        <xs:element name="Alpha" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>"""
    first = normalize_xsd(wrap(body)).to_json()
    second = normalize_xsd(wrap(body)).to_json()
    assert first == second
    assert [item["path"] for item in first["elements"]] == [
        "/Invoice",
        "/Invoice/Alpha",
        "/Invoice/Zeta",
    ]


def test_rejects_something_that_is_not_a_schema() -> None:
    with pytest.raises(ValueError, match="xs:schema"):
        normalize_xsd(b"<html><body>404</body></html>")


def test_rejects_unparseable_content() -> None:
    with pytest.raises(ValueError, match="ilegible"):
        normalize_xsd(b"<xs:schema")
