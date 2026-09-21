"""Normalizador XSD -> forma `XSD_ELEMENTS`.

Aplana un esquema en una lista de elementos y atributos identificados por una ruta
canonica. Esa ruta es la clave con la que el detector empareja versiones, asi que
tiene que ser **estable**: cualquier inestabilidad se convierte en un falso positivo,
y el apartado 1 del contexto declara el falso positivo inaceptable.

Reglas de la ruta canonica:

- Elementos: `/Facturae/FileHeader/SchemaVersion`.
- Atributos: `/Facturae/@version`, con arroba y siempre hoja.
- El compositor (`sequence`, `choice`, `all`) **no** aparece en la ruta. Se guarda en
  el campo `container`. Meterlo en la ruta haria que reordenar un `choice` sin cambiar
  nada semantico generase ruido.
- Los tipos anonimos se recorren en linea, sin nombre inventado.

Resolucion de dependencias: `include` e `import` se resuelven **solo** contra
artefactos ya descargados que se pasan en `dependencies`. El normalizador nunca sale a
la red; si lo hiciera, reprocesar el historico dejaria de ser reproducible. Una
dependencia que falta no aborta el analisis: marca la forma como parcial y sigue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from lxml import etree

#: Se sube a mano cuando cambia la semantica de la salida. Va en la clave unica de
#: `normalized_form`, de modo que el historico reprocesado convive con el anterior.
PARSER_VERSION: Final = "xsd/1"

XS: Final = "http://www.w3.org/2001/XMLSchema"

#: Tope de profundidad para tipos recursivos (una factura que contiene facturas).
#: Sin el, un esquema con recursion mutua cuelga el proceso.
MAX_DEPTH: Final = 40

#: Facetas de restriccion que nos interesa comparar. `enumeration` va aparte.
FACETS: Final = (
    "minLength",
    "maxLength",
    "length",
    "pattern",
    "minInclusive",
    "maxInclusive",
    "minExclusive",
    "maxExclusive",
    "totalDigits",
    "fractionDigits",
    "whiteSpace",
)

UNBOUNDED: Final = -1


@dataclass(frozen=True, slots=True)
class XsdElement:
    """Un elemento o atributo aplanado."""

    path: str
    name: str
    type_name: str | None
    min_occurs: int
    max_occurs: int
    is_attribute: bool
    container: str | None
    restrictions: dict[str, str]
    enumerations: tuple[str, ...]
    annotation: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "type": self.type_name,
            "min_occurs": self.min_occurs,
            "max_occurs": self.max_occurs,
            "is_attribute": self.is_attribute,
            "container": self.container,
            "restrictions": dict(self.restrictions),
            "enumerations": list(self.enumerations),
            "annotation": self.annotation,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> XsdElement:
        return cls(
            path=data["path"],
            name=data["name"],
            type_name=data.get("type"),
            min_occurs=int(data["min_occurs"]),
            max_occurs=int(data["max_occurs"]),
            is_attribute=bool(data.get("is_attribute", False)),
            container=data.get("container"),
            restrictions=dict(data.get("restrictions") or {}),
            enumerations=tuple(data.get("enumerations") or ()),
            annotation=data.get("annotation"),
        )


@dataclass(slots=True)
class XsdForm:
    """Resultado de normalizar un XSD."""

    elements: list[XsdElement] = field(default_factory=list)
    target_namespace: str | None = None
    declared_version: str | None = None
    is_partial: bool = False
    missing_dependencies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION

    def by_path(self) -> dict[str, XsdElement]:
        return {element.path: element for element in self.elements}

    def to_json(self) -> dict[str, Any]:
        return {
            "parser_version": self.parser_version,
            "target_namespace": self.target_namespace,
            "declared_version": self.declared_version,
            "is_partial": self.is_partial,
            "missing_dependencies": list(self.missing_dependencies),
            "warnings": list(self.warnings),
            "elements": [element.to_json() for element in self.elements],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> XsdForm:
        """Inversa de `to_json`. Es lo que permite comparar contra la forma guardada en
        `normalized_form.payload` sin volver a parsear el XSD."""
        return cls(
            elements=[XsdElement.from_json(item) for item in data.get("elements", [])],
            target_namespace=data.get("target_namespace"),
            declared_version=data.get("declared_version"),
            is_partial=bool(data.get("is_partial", False)),
            missing_dependencies=list(data.get("missing_dependencies") or []),
            warnings=list(data.get("warnings") or []),
            parser_version=str(data.get("parser_version", PARSER_VERSION)),
        )


def _local(tag: object) -> str:
    """Nombre local de una etiqueta, sin el namespace."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _strip_prefix(qname: str | None) -> str | None:
    """`fac:TextMax20Type` -> `TextMax20Type`.

    Se compara por nombre local a proposito: el prefijo depende de como se declaren
    los namespaces en el fichero, y eso cambia entre versiones sin cambiar nada real.
    """
    if qname is None:
        return None
    return qname.rsplit(":", 1)[-1]


def _occurs(node: etree._Element, attribute: str, default: int) -> int:
    raw = node.get(attribute)
    if raw is None:
        return default
    if raw == "unbounded":
        return UNBOUNDED
    try:
        return int(raw)
    except ValueError:
        return default


def _annotation_of(node: etree._Element) -> str | None:
    """Primera `xs:documentation`, con los espacios colapsados.

    Se normaliza el espacio en blanco porque los XSD oficiales estan llenos de
    tabulaciones y saltos de linea decorativos que cambian entre publicaciones sin
    que cambie el texto.
    """
    for annotation in node.iterchildren(f"{{{XS}}}annotation"):
        for documentation in annotation.iterchildren(f"{{{XS}}}documentation"):
            text = " ".join((documentation.text or "").split())
            if text:
                return text
    return None


class _SchemaIndex:
    """Indice de definiciones globales de un esquema y de sus dependencias."""

    def __init__(self) -> None:
        self.types: dict[str, etree._Element] = {}
        self.elements: dict[str, etree._Element] = {}
        self.groups: dict[str, etree._Element] = {}
        self.attribute_groups: dict[str, etree._Element] = {}

    def absorb(self, schema: etree._Element) -> None:
        for child in schema:
            name = child.get("name")
            if name is None:
                continue
            tag = _local(child.tag)
            if tag in ("complexType", "simpleType"):
                self.types.setdefault(name, child)
            elif tag == "element":
                self.elements.setdefault(name, child)
            elif tag == "group":
                self.groups.setdefault(name, child)
            elif tag == "attributeGroup":
                self.attribute_groups.setdefault(name, child)


class XsdNormalizer:
    """Convierte un XSD en una `XsdForm`.

    `dependencies` mapea el `schemaLocation` tal y como aparece en el fichero al
    contenido de esa dependencia. Lo rellena la capa de ingesta con artefactos ya
    almacenados; el normalizador no descarga nada.
    """

    def __init__(self, dependencies: dict[str, bytes] | None = None) -> None:
        self._dependencies = dependencies or {}
        self._form = XsdForm()
        self._index = _SchemaIndex()

    def normalize(self, content: bytes) -> XsdForm:
        self._form = XsdForm()
        self._index = _SchemaIndex()

        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
        try:
            root = etree.fromstring(content, parser=parser)
        except etree.XMLSyntaxError as error:
            raise ValueError(f"XSD ilegible: {error}") from error

        if _local(root.tag) != "schema":
            raise ValueError(f"la raiz no es xs:schema sino {_local(root.tag)!r}")

        self._form.target_namespace = root.get("targetNamespace")
        self._form.declared_version = root.get("version")

        self._absorb_recursively(root, seen=set())

        for name, node in sorted(self._index.elements.items()):
            self._walk_element(node, parent_path="", container=None, type_stack=(), depth=0)
            del name

        self._form.elements.sort(key=lambda element: element.path)
        return self._form

    # -- dependencias -----------------------------------------------------------

    def _absorb_recursively(self, schema: etree._Element, seen: set[str]) -> None:
        self._index.absorb(schema)

        for child in schema:
            tag = _local(child.tag)
            if tag not in ("include", "import", "redefine"):
                continue

            location = child.get("schemaLocation")
            if location is None:
                # Un `import` sin schemaLocation es legitimo: declara que se usa un
                # namespace externo sin decir donde vive. No es una dependencia rota.
                continue
            if location in seen:
                continue
            seen.add(location)

            payload = self._dependencies.get(location)
            if payload is None:
                self._form.is_partial = True
                self._form.missing_dependencies.append(location)
                continue

            try:
                nested = etree.fromstring(
                    payload,
                    parser=etree.XMLParser(resolve_entities=False, no_network=True),
                )
            except etree.XMLSyntaxError as error:
                self._form.is_partial = True
                self._form.warnings.append(f"dependencia ilegible {location}: {error}")
                continue

            self._absorb_recursively(nested, seen)

    # -- recorrido --------------------------------------------------------------

    def _walk_element(
        self,
        node: etree._Element,
        parent_path: str,
        container: str | None,
        type_stack: tuple[str, ...],
        depth: int,
    ) -> None:
        if depth > MAX_DEPTH:
            self._form.warnings.append(f"profundidad maxima alcanzada en {parent_path}")
            return

        ref = _strip_prefix(node.get("ref"))
        if ref is not None:
            target = self._index.elements.get(ref)
            if target is None:
                # Referencia a otro namespace que no tenemos (el caso tipico es
                # ds:Signature). Se registra como hoja para que su aparicion o
                # desaparicion se detecte, aunque no podamos expandirla.
                self._emit(
                    path=f"{parent_path}/{ref}",
                    name=ref,
                    node=node,
                    type_name=ref,
                    container=container,
                    restrictions={},
                    enumerations=(),
                )
                if ref not in self._form.missing_dependencies:
                    self._form.is_partial = True
                    self._form.missing_dependencies.append(f"element:{ref}")
                return
            node = target

        name = node.get("name")
        if name is None:
            return

        path = f"{parent_path}/{name}"
        type_name = _strip_prefix(node.get("type"))

        definition = self._resolve_type(node, type_name)
        restrictions, enumerations = self._facets_of(definition)

        self._emit(
            path=path,
            name=name,
            node=node,
            type_name=type_name,
            container=container,
            restrictions=restrictions,
            enumerations=enumerations,
        )

        if definition is None:
            return

        # Corte de recursion: si ya estamos dentro de este tipo con nombre, paramos.
        # Solo aplica a tipos nombrados; los anonimos no pueden recursar por si mismos.
        definition_name = definition.get("name")
        if definition_name is not None:
            if definition_name in type_stack:
                return
            type_stack = (*type_stack, definition_name)

        self._walk_type_children(definition, path, type_stack, depth + 1)

    def _resolve_type(self, node: etree._Element, type_name: str | None) -> etree._Element | None:
        """Devuelve la definicion del tipo: inline si la hay, global si no."""
        for child in node:
            if _local(child.tag) in ("complexType", "simpleType"):
                return child
        if type_name is None:
            return None
        return self._index.types.get(type_name)

    def _walk_type_children(
        self,
        definition: etree._Element,
        path: str,
        type_stack: tuple[str, ...],
        depth: int,
    ) -> None:
        for child in definition:
            tag = _local(child.tag)

            if tag in ("sequence", "all"):
                self._walk_particles(child, path, tag, type_stack, depth)
            elif tag == "choice":
                self._walk_particles(child, path, "choice", type_stack, depth)
            elif tag == "group":
                group = self._index.groups.get(_strip_prefix(child.get("ref")) or "")
                if group is not None:
                    self._walk_type_children(group, path, type_stack, depth)
            elif tag == "attribute":
                self._emit_attribute(child, path)
            elif tag == "attributeGroup":
                group = self._index.attribute_groups.get(_strip_prefix(child.get("ref")) or "")
                if group is not None:
                    for attribute in group.iterchildren(f"{{{XS}}}attribute"):
                        self._emit_attribute(attribute, path)
            elif tag in ("complexContent", "simpleContent"):
                for grandchild in child:
                    if _local(grandchild.tag) in ("extension", "restriction"):
                        base = self._index.types.get(_strip_prefix(grandchild.get("base")) or "")
                        if base is not None and base.get("name") not in type_stack:
                            self._walk_type_children(base, path, type_stack, depth)
                        self._walk_type_children(grandchild, path, type_stack, depth)

    def _walk_particles(
        self,
        particle: etree._Element,
        path: str,
        container: str,
        type_stack: tuple[str, ...],
        depth: int,
    ) -> None:
        for child in particle:
            tag = _local(child.tag)
            if tag == "element":
                self._walk_element(child, path, container, type_stack, depth)
            elif tag in ("sequence", "choice", "all"):
                # Compositor anidado: el hijo directo manda para `container`.
                self._walk_particles(child, path, tag, type_stack, depth)
            elif tag == "group":
                group = self._index.groups.get(_strip_prefix(child.get("ref")) or "")
                if group is not None:
                    self._walk_type_children(group, path, type_stack, depth)

    def _emit_attribute(self, node: etree._Element, path: str) -> None:
        name = node.get("name") or _strip_prefix(node.get("ref"))
        if name is None:
            return
        definition = self._resolve_type(node, _strip_prefix(node.get("type")))
        restrictions, enumerations = self._facets_of(definition)
        required = node.get("use") == "required"

        self._form.elements.append(
            XsdElement(
                path=f"{path}/@{name}",
                name=name,
                type_name=_strip_prefix(node.get("type")),
                min_occurs=1 if required else 0,
                max_occurs=1,
                is_attribute=True,
                container=None,
                restrictions=restrictions,
                enumerations=enumerations,
                annotation=_annotation_of(node),
            )
        )

    def _emit(
        self,
        path: str,
        name: str,
        node: etree._Element,
        type_name: str | None,
        container: str | None,
        restrictions: dict[str, str],
        enumerations: tuple[str, ...],
    ) -> None:
        self._form.elements.append(
            XsdElement(
                path=path,
                name=name,
                type_name=type_name,
                min_occurs=_occurs(node, "minOccurs", 1),
                max_occurs=_occurs(node, "maxOccurs", 1),
                is_attribute=False,
                container=container,
                restrictions=restrictions,
                enumerations=enumerations,
                annotation=_annotation_of(node),
            )
        )

    def _facets_of(
        self, definition: etree._Element | None
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        """Extrae facetas y enumerados de un simpleType, siguiendo la cadena de bases."""
        if definition is None:
            return {}, ()

        restrictions: dict[str, str] = {}
        enumerations: list[str] = []
        seen_bases: set[str] = set()

        current: etree._Element | None = definition
        while current is not None:
            restriction = self._restriction_of(current)
            if restriction is None:
                break

            for facet in restriction:
                tag = _local(facet.tag)
                value = facet.get("value")
                if value is None:
                    continue
                if tag == "enumeration":
                    enumerations.append(value)
                elif tag in FACETS:
                    # El primero gana: la restriccion mas cercana es la mas estricta.
                    restrictions.setdefault(tag, value)

            base = _strip_prefix(restriction.get("base"))
            if base is None or base in seen_bases:
                break
            seen_bases.add(base)
            current = self._index.types.get(base)

        return restrictions, tuple(enumerations)

    @staticmethod
    def _restriction_of(definition: etree._Element) -> etree._Element | None:
        for child in definition:
            if _local(child.tag) == "restriction":
                return child
            if _local(child.tag) == "simpleContent":
                for grandchild in child:
                    if _local(grandchild.tag) == "restriction":
                        return grandchild
        return None


def normalize_xsd(content: bytes, dependencies: dict[str, bytes] | None = None) -> XsdForm:
    """Atajo funcional sobre `XsdNormalizer`."""
    return XsdNormalizer(dependencies).normalize(content)
