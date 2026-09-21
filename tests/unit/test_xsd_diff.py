"""Los once tipos de cambio del apartado 7.3, uno por test.

Cada test construye un par de esquemas que difieren en **una sola cosa**, para que un
fallo senale exactamente que clasificador se ha roto. Los casos donde interactuan
varios cambios van al final.
"""

from __future__ import annotations

import pytest

from regwatch.core.enums import ChangeType, Severity
from regwatch.ingest.diff.xsd_diff import compare_xsd
from regwatch.ingest.normalizers.xsd import normalize_xsd

NS = "http://example.org/invoice"


def schema(body: str, types: str = "", version: str = "1.0") -> bytes:
    """Envuelve un cuerpo de esquema en un xs:schema minimo pero valido."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           targetNamespace="{NS}" xmlns="{NS}" version="{version}">
  <xs:element name="Invoice">
    <xs:complexType>
      <xs:sequence>
{body}
      </xs:sequence>
    </xs:complexType>
  </xs:element>
{types}
</xs:schema>""".encode()


def diff_of(before_body: str, after_body: str, before_types: str = "", after_types: str = ""):
    return compare_xsd(
        normalize_xsd(schema(before_body, before_types)),
        normalize_xsd(schema(after_body, after_types)),
    )


def only_item(diff, change_type: ChangeType):
    """Comprueba que hay exactamente un cambio y es del tipo esperado."""
    assert len(diff.items) == 1, [item.to_json() for item in diff.items]
    item = diff.items[0]
    assert item.change_type is change_type
    return item


NUMBER = '        <xs:element name="Number" type="xs:string"/>'


# -- 1 a 3: altas y bajas ------------------------------------------------------


def test_field_added_optional() -> None:
    diff = diff_of(
        NUMBER,
        NUMBER + '\n        <xs:element name="Note" type="xs:string" minOccurs="0"/>',
    )
    item = only_item(diff, ChangeType.FIELD_ADDED_OPTIONAL)
    assert item.path == "/Invoice/Note"
    assert item.severity is Severity.INFO


def test_field_added_mandatory() -> None:
    diff = diff_of(
        NUMBER,
        NUMBER + '\n        <xs:element name="TaxId" type="xs:string"/>',
    )
    item = only_item(diff, ChangeType.FIELD_ADDED_MANDATORY)
    assert item.path == "/Invoice/TaxId"
    assert item.severity is Severity.BLOCKING


def test_field_removed() -> None:
    diff = diff_of(
        NUMBER + '\n        <xs:element name="Legacy" type="xs:date"/>',
        NUMBER,
    )
    item = only_item(diff, ChangeType.FIELD_REMOVED)
    assert item.path == "/Invoice/Legacy"
    assert item.severity is Severity.BLOCKING
    assert item.detail["was_mandatory"] is True


# -- 4 y 5: cardinalidad -------------------------------------------------------


def test_cardinality_tightened() -> None:
    diff = diff_of(
        NUMBER + '\n        <xs:element name="Note" type="xs:string" minOccurs="0"/>',
        NUMBER + '\n        <xs:element name="Note" type="xs:string"/>',
    )
    item = only_item(diff, ChangeType.CARDINALITY_TIGHTENED)
    assert (item.before, item.after) == ("0..1", "1..1")
    assert item.severity is Severity.BLOCKING
    assert item.detail["became_mandatory"] is True


def test_cardinality_relaxed() -> None:
    diff = diff_of(
        NUMBER + '\n        <xs:element name="Note" type="xs:string"/>',
        NUMBER + '\n        <xs:element name="Note" type="xs:string" maxOccurs="unbounded"/>',
    )
    item = only_item(diff, ChangeType.CARDINALITY_RELAXED)
    assert item.after == "1..unbounded"
    assert item.severity is Severity.INFO


# -- 6: tipo -------------------------------------------------------------------


def test_type_changed() -> None:
    diff = diff_of(
        NUMBER + '\n        <xs:element name="Issued" type="xs:string"/>',
        NUMBER + '\n        <xs:element name="Issued" type="xs:date"/>',
    )
    item = only_item(diff, ChangeType.TYPE_CHANGED)
    assert (item.before, item.after) == ("string", "date")
    assert item.severity is Severity.REQUIRES_CHANGE


# -- 7: longitud ---------------------------------------------------------------


def _text_type(max_length: int) -> str:
    return f"""  <xs:simpleType name="TextType">
    <xs:restriction base="xs:string">
      <xs:maxLength value="{max_length}"/>
    </xs:restriction>
  </xs:simpleType>"""


def test_length_restricted() -> None:
    body = NUMBER + '\n        <xs:element name="Series" type="TextType"/>'
    diff = diff_of(body, body, _text_type(20), _text_type(10))
    item = only_item(diff, ChangeType.LENGTH_RESTRICTED)
    assert (item.before, item.after) == ("20", "10")
    assert item.detail["direction"] == "tightened"
    assert item.severity is Severity.REQUIRES_CHANGE


def test_length_relaxed_is_informative() -> None:
    """Ensanchar no rompe a ningun emisor: se registra, pero no alarma.

    Es el caso real de TicketBAI 1.2, donde el codigo postal paso a 20 caracteres.
    """
    body = NUMBER + '\n        <xs:element name="PostCode" type="TextType"/>'
    diff = diff_of(body, body, _text_type(5), _text_type(20))
    item = only_item(diff, ChangeType.LENGTH_RESTRICTED)
    assert item.detail["direction"] == "relaxed"
    assert item.severity is Severity.INFO


# -- 8: patron -----------------------------------------------------------------


def _pattern_type(pattern: str) -> str:
    return f"""  <xs:simpleType name="CodeType">
    <xs:restriction base="xs:string">
      <xs:pattern value="{pattern}"/>
    </xs:restriction>
  </xs:simpleType>"""


def test_pattern_changed() -> None:
    body = NUMBER + '\n        <xs:element name="Code" type="CodeType"/>'
    diff = diff_of(body, body, _pattern_type("[0-9]{4}"), _pattern_type("[0-9]{6}"))
    item = only_item(diff, ChangeType.PATTERN_CHANGED)
    assert (item.before, item.after) == ("[0-9]{4}", "[0-9]{6}")
    assert item.severity is Severity.REQUIRES_CHANGE


# -- 9 y 10: enumerados --------------------------------------------------------


def _enum_type(*values: str) -> str:
    entries = "\n".join(f'      <xs:enumeration value="{value}"/>' for value in values)
    return f"""  <xs:simpleType name="UnitType">
    <xs:restriction base="xs:string">
{entries}
    </xs:restriction>
  </xs:simpleType>"""


def test_enum_value_added() -> None:
    body = NUMBER + '\n        <xs:element name="Unit" type="UnitType"/>'
    diff = diff_of(body, body, _enum_type("EA", "KG"), _enum_type("EA", "KG", "KWH"))
    item = only_item(diff, ChangeType.ENUM_VALUE_ADDED)
    assert item.after == ["KWH"]
    assert item.severity is Severity.INFO


def test_enum_value_removed() -> None:
    body = NUMBER + '\n        <xs:element name="Unit" type="UnitType"/>'
    diff = diff_of(body, body, _enum_type("EA", "KG", "OLD"), _enum_type("EA", "KG"))
    item = only_item(diff, ChangeType.ENUM_VALUE_REMOVED)
    assert item.before == ["OLD"]
    assert item.severity is Severity.BLOCKING


# -- 11: renombrado ------------------------------------------------------------


def test_element_renamed() -> None:
    diff = diff_of(
        NUMBER + '\n        <xs:element name="Client" type="xs:string"/>',
        NUMBER + '\n        <xs:element name="Customer" type="xs:string"/>',
    )
    item = only_item(diff, ChangeType.ELEMENT_RENAMED)
    assert (item.before, item.after) == ("Client", "Customer")
    assert item.detail["new_path"] == "/Invoice/Customer"
    assert item.severity is Severity.BLOCKING


def test_ambiguous_rename_falls_back_to_add_and_remove() -> None:
    """Con dos candidatos no se adivina: alta y baja, que es el resultado seguro."""
    diff = diff_of(
        NUMBER
        + """
        <xs:element name="Client" type="xs:string"/>
        <xs:element name="Vendor" type="xs:string"/>""",
        NUMBER
        + """
        <xs:element name="Customer" type="xs:string"/>
        <xs:element name="Supplier" type="xs:string"/>""",
    )
    assert not diff.of_type(ChangeType.ELEMENT_RENAMED)
    assert len(diff.of_type(ChangeType.FIELD_REMOVED)) == 2
    assert len(diff.of_type(ChangeType.FIELD_ADDED_MANDATORY)) == 2


# -- comportamiento general ----------------------------------------------------


def test_identical_schemas_produce_no_diff() -> None:
    body = NUMBER + '\n        <xs:element name="Note" type="xs:string" minOccurs="0"/>'
    diff = diff_of(body, body)
    assert diff.is_empty
    assert diff.max_severity() is Severity.INFO


def test_max_severity_is_the_worst_of_the_lot() -> None:
    diff = diff_of(
        NUMBER,
        NUMBER
        + """
        <xs:element name="Note" type="xs:string" minOccurs="0"/>
        <xs:element name="TaxId" type="xs:string"/>""",
    )
    assert diff.max_severity() is Severity.BLOCKING
    assert diff.affected_paths() == ["/Invoice/Note", "/Invoice/TaxId"]


def test_blocking_items_come_first() -> None:
    diff = diff_of(
        NUMBER,
        NUMBER
        + """
        <xs:element name="Note" type="xs:string" minOccurs="0"/>
        <xs:element name="TaxId" type="xs:string"/>""",
    )
    assert diff.items[0].severity is Severity.BLOCKING


def test_diff_carries_its_schema_version() -> None:
    diff = diff_of(NUMBER, NUMBER)
    assert diff.to_json()["diff_schema_version"] == "xsd-diff/1"


@pytest.mark.parametrize("change_type", list(ChangeType))
def test_every_change_type_has_a_default_severity(change_type: ChangeType) -> None:
    """Cada detector trae su tabla, pero entre las dos tienen que cubrir el enumerado
    entero: un tipo de cambio sin severidad por defecto es un cambio que se emite sin
    clasificar y que nadie prioriza."""
    from regwatch.ingest.diff.index_diff import DEFAULT_SEVERITY as INDEX_SEVERITY
    from regwatch.ingest.diff.xsd_diff import DEFAULT_SEVERITY

    assert change_type in DEFAULT_SEVERITY | INDEX_SEVERITY


# -- campos obligatorios dentro de una rama nueva y opcional --------------------


def test_mandatory_field_inside_a_new_optional_branch_is_not_blocking() -> None:
    """Quien no adopte la rama nueva no emite ese campo jamas, asi que no le rompe nada.

    Es el caso real de Facturae 3.2.2: el bloque de factoring y el de pago en especie
    entraron como opcionales, con hijos obligatorios dentro. Clasificarlos bloqueantes
    eran seis falsos positivos en un solo cambio, y el apartado 1 los declara
    inaceptables.
    """
    diff = diff_of(
        NUMBER,
        NUMBER
        + """
        <xs:element name="Factoring" minOccurs="0">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="Assignee" type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>""",
    )

    by_path = {item.path: item for item in diff.items}
    assert by_path["/Invoice/Factoring"].change_type is ChangeType.FIELD_ADDED_OPTIONAL

    hijo = by_path["/Invoice/Factoring/Assignee"]
    assert hijo.change_type is ChangeType.FIELD_ADDED_MANDATORY, "el tipo describe la estructura"
    assert hijo.severity is Severity.INFO, "la severidad mide el impacto, y aqui no lo hay"
    assert hijo.detail["mandatory_within_new_optional_branch"] == "/Invoice/Factoring"
    assert diff.max_severity() is Severity.INFO


def test_mandatory_field_inside_a_new_mandatory_branch_still_blocks() -> None:
    """Si la rama nueva es obligatoria, el campo se exige a todo el mundo."""
    diff = diff_of(
        NUMBER,
        NUMBER
        + """
        <xs:element name="Block">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="Inner" type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>""",
    )

    hijo = {item.path: item for item in diff.items}["/Invoice/Block/Inner"]
    assert hijo.change_type is ChangeType.FIELD_ADDED_MANDATORY
    assert hijo.severity is Severity.BLOCKING
    assert "mandatory_within_new_optional_branch" not in hijo.detail


def test_mandatory_field_added_to_an_existing_branch_still_blocks() -> None:
    """El caso que el criterio de aceptacion 2 exige detectar: un campo obligatorio
    nuevo en una rama que ya existia rompe a todos los emisores."""
    antes = (
        NUMBER
        + """
        <xs:element name="Party" minOccurs="0">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="Name" type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>"""
    )
    despues = (
        NUMBER
        + """
        <xs:element name="Party" minOccurs="0">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="Name" type="xs:string"/>
              <xs:element name="TaxId" type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>"""
    )

    item = only_item(diff_of(antes, despues), ChangeType.FIELD_ADDED_MANDATORY)
    assert item.path == "/Invoice/Party/TaxId"
    assert item.severity is Severity.BLOCKING
