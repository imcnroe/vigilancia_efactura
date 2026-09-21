"""Detector de cambios estructurales entre dos formas `XSD_ELEMENTS`.

Determinista y sin IA. No compara texto: compara estructura. Es la pieza que separa
este producto de un `diff` con un cron, asi que aqui no hay heuristicas blandas salvo
la de renombrado, que va marcada como tal.

El emparejamiento es por ruta canonica. Lo que queda sin pareja pasa por una segunda
vuelta que busca renombrados; lo que sobrevive a esa vuelta es alta o baja de verdad.

La severidad que sale de aqui es una **sugerencia**. El editor humano puede cambiarla,
y en la ficha manda la suya (apartado 7.3).
"""

from __future__ import annotations

from typing import Any, Final

from regwatch.core.enums import ChangeType, Severity
from regwatch.ingest.diff.base import DiffItem, StructuralDiff
from regwatch.ingest.normalizers.xsd import UNBOUNDED, XsdElement, XsdForm

#: Version del formato del JSONB `structural_diff`. Permite evolucionar la salida sin
#: romper los diffs ya guardados.
DIFF_SCHEMA_VERSION: Final = "xsd-diff/1"

#: Severidad por defecto de cada tipo de cambio. Tabla del apartado 7.3.
DEFAULT_SEVERITY: Final[dict[ChangeType, Severity]] = {
    ChangeType.FIELD_ADDED_OPTIONAL: Severity.INFO,
    ChangeType.FIELD_ADDED_MANDATORY: Severity.BLOCKING,
    ChangeType.FIELD_REMOVED: Severity.BLOCKING,
    ChangeType.CARDINALITY_TIGHTENED: Severity.BLOCKING,
    ChangeType.CARDINALITY_RELAXED: Severity.INFO,
    ChangeType.TYPE_CHANGED: Severity.REQUIRES_CHANGE,
    ChangeType.LENGTH_RESTRICTED: Severity.REQUIRES_CHANGE,
    ChangeType.PATTERN_CHANGED: Severity.REQUIRES_CHANGE,
    ChangeType.ENUM_VALUE_ADDED: Severity.INFO,
    ChangeType.ENUM_VALUE_REMOVED: Severity.BLOCKING,
    ChangeType.ELEMENT_RENAMED: Severity.BLOCKING,
}

#: Facetas que acotan la longitud. Estrecharlas rompe emisores; ensancharlas no.
LENGTH_FACETS: Final = ("maxLength", "length", "totalDigits", "fractionDigits")


def _parent_of(path: str) -> str:
    return path.rsplit("/", 1)[0]


def _is_mandatory(element: XsdElement) -> bool:
    return element.min_occurs >= 1


def _cardinality(element: XsdElement) -> tuple[int, int]:
    return element.min_occurs, element.max_occurs


def _width(value: int) -> float:
    """`unbounded` como infinito, para poder comparar cardinalidades sin casos raros."""
    return float("inf") if value == UNBOUNDED else float(value)


def _optional_new_ancestor(
    path: str,
    old_by_path: dict[str, XsdElement],
    new_by_path: dict[str, XsdElement],
) -> str | None:
    """Ancestro mas cercano que es nuevo en esta version y opcional, si lo hay.

    Se recorre hacia arriba desde el propio campo. Si en el camino aparece un elemento
    que no existia antes y que se puede omitir, el campo solo es exigible para quien
    decida usar esa rama.
    """
    parts = path.split("/")
    for depth in range(len(parts) - 1, 1, -1):
        ancestor = "/".join(parts[:depth])
        if ancestor in old_by_path:
            # A partir de aqui hacia arriba todo es preexistente: no hay rama nueva.
            return None
        candidate = new_by_path.get(ancestor)
        if candidate is not None and not _is_mandatory(candidate):
            return ancestor
    return None


class XsdDiffDetector:
    """Compara dos formas normalizadas y clasifica lo que cambia."""

    def compare(self, before: XsdForm, after: XsdForm) -> StructuralDiff:
        diff = StructuralDiff(diff_schema_version=DIFF_SCHEMA_VERSION)

        if before.is_partial or after.is_partial:
            diff.is_partial = True
            diff.notes.append(
                "una de las dos formas es parcial: hay dependencias sin resolver, "
                "asi que puede haber cambios no vistos"
            )

        old_by_path = before.by_path()
        new_by_path = after.by_path()

        common = old_by_path.keys() & new_by_path.keys()
        removed_paths = old_by_path.keys() - new_by_path.keys()
        added_paths = new_by_path.keys() - old_by_path.keys()

        for path in sorted(common):
            diff.items.extend(self._compare_pair(old_by_path[path], new_by_path[path]))

        renamed, still_removed, still_added = self._match_renames(
            {path: old_by_path[path] for path in removed_paths},
            {path: new_by_path[path] for path in added_paths},
        )

        for old_element, new_element in renamed:
            diff.items.append(
                DiffItem(
                    change_type=ChangeType.ELEMENT_RENAMED,
                    path=old_element.path,
                    severity=DEFAULT_SEVERITY[ChangeType.ELEMENT_RENAMED],
                    before=old_element.name,
                    after=new_element.name,
                    detail={
                        "new_path": new_element.path,
                        "type": old_element.type_name,
                        "heuristic": "mismo padre, mismo tipo, unica pareja posible",
                    },
                )
            )
            # Un renombrado puede traer ademas cambios de cardinalidad o facetas.
            diff.items.extend(
                self._compare_pair(old_element, new_element, path_override=old_element.path)
            )

        for path in sorted(still_removed):
            element = old_by_path[path]
            diff.items.append(
                DiffItem(
                    change_type=ChangeType.FIELD_REMOVED,
                    path=path,
                    severity=DEFAULT_SEVERITY[ChangeType.FIELD_REMOVED],
                    before=element.type_name,
                    after=None,
                    detail={"was_mandatory": _is_mandatory(element)},
                )
            )

        for path in sorted(still_added):
            element = new_by_path[path]
            change_type = (
                ChangeType.FIELD_ADDED_MANDATORY
                if _is_mandatory(element)
                else ChangeType.FIELD_ADDED_OPTIONAL
            )
            severity = DEFAULT_SEVERITY[change_type]
            detail: dict[str, Any] = {
                "min_occurs": element.min_occurs,
                "max_occurs": element.max_occurs,
                "is_attribute": element.is_attribute,
            }

            # Un campo obligatorio **dentro de una rama nueva y opcional** no rompe a
            # nadie: quien no adopte la rama no lo emite nunca. Facturae 3.2.2 añadió
            # asi el bloque de factoring y el de pago en especie, con hijos obligatorios
            # colgando de padres `minOccurs="0"`, y marcarlos bloqueantes serian seis
            # falsos positivos en un solo cambio. El tipo sigue describiendo la
            # estructura real; lo que baja es la severidad, que es lo que mide el
            # impacto sobre quien ya emite.
            if change_type is ChangeType.FIELD_ADDED_MANDATORY:
                ancestor = _optional_new_ancestor(path, old_by_path, new_by_path)
                if ancestor is not None:
                    severity = Severity.INFO
                    detail["mandatory_within_new_optional_branch"] = ancestor

            diff.items.append(
                DiffItem(
                    change_type=change_type,
                    path=path,
                    severity=severity,
                    before=None,
                    after=element.type_name,
                    detail=detail,
                )
            )

        diff.items.sort(key=lambda item: (-item.severity.rank(), item.path, item.change_type.value))
        return diff

    # -- comparacion de un par emparejado ---------------------------------------

    def _compare_pair(
        self,
        old: XsdElement,
        new: XsdElement,
        path_override: str | None = None,
    ) -> list[DiffItem]:
        path = path_override or new.path
        items: list[DiffItem] = []

        items.extend(self._compare_cardinality(old, new, path))
        items.extend(self._compare_type(old, new, path))
        items.extend(self._compare_restrictions(old, new, path))
        items.extend(self._compare_enumerations(old, new, path))

        return items

    def _compare_cardinality(self, old: XsdElement, new: XsdElement, path: str) -> list[DiffItem]:
        if _cardinality(old) == _cardinality(new):
            return []

        tightened = new.min_occurs > old.min_occurs or _width(new.max_occurs) < _width(
            old.max_occurs
        )
        relaxed = new.min_occurs < old.min_occurs or _width(new.max_occurs) > _width(old.max_occurs)

        # Si se estrecha por un lado y se ensancha por otro, manda el estrechamiento:
        # es lo que rompe al emisor.
        change_type = (
            ChangeType.CARDINALITY_TIGHTENED if tightened else ChangeType.CARDINALITY_RELAXED
        )
        if not tightened and not relaxed:
            return []

        return [
            DiffItem(
                change_type=change_type,
                path=path,
                severity=DEFAULT_SEVERITY[change_type],
                before=self._format_cardinality(old),
                after=self._format_cardinality(new),
                detail={"became_mandatory": not _is_mandatory(old) and _is_mandatory(new)},
            )
        ]

    @staticmethod
    def _format_cardinality(element: XsdElement) -> str:
        maximum = "unbounded" if element.max_occurs == UNBOUNDED else str(element.max_occurs)
        return f"{element.min_occurs}..{maximum}"

    def _compare_type(self, old: XsdElement, new: XsdElement, path: str) -> list[DiffItem]:
        if old.type_name == new.type_name:
            return []
        return [
            DiffItem(
                change_type=ChangeType.TYPE_CHANGED,
                path=path,
                severity=DEFAULT_SEVERITY[ChangeType.TYPE_CHANGED],
                before=old.type_name,
                after=new.type_name,
            )
        ]

    def _compare_restrictions(self, old: XsdElement, new: XsdElement, path: str) -> list[DiffItem]:
        items: list[DiffItem] = []

        old_pattern = old.restrictions.get("pattern")
        new_pattern = new.restrictions.get("pattern")
        if old_pattern != new_pattern:
            items.append(
                DiffItem(
                    change_type=ChangeType.PATTERN_CHANGED,
                    path=path,
                    severity=DEFAULT_SEVERITY[ChangeType.PATTERN_CHANGED],
                    before=old_pattern,
                    after=new_pattern,
                )
            )

        for facet in LENGTH_FACETS:
            old_raw = old.restrictions.get(facet)
            new_raw = new.restrictions.get(facet)
            if old_raw == new_raw:
                continue

            narrowed = self._is_narrower(old_raw, new_raw)
            if narrowed is None:
                # No podemos afirmar la direccion (por ejemplo aparece una faceta que
                # antes no existia sobre un tipo sin acotar). Se trata como
                # estrechamiento: es la lectura conservadora.
                narrowed = new_raw is not None and old_raw is None

            if not narrowed:
                # Ensanchar no rompe a nadie. La tabla del 7.3 no contempla este caso;
                # se emite como LENGTH_RESTRICTED con severidad INFO y direccion
                # explicita en el detalle, para no perder el cambio ni alarmar.
                items.append(
                    DiffItem(
                        change_type=ChangeType.LENGTH_RESTRICTED,
                        path=path,
                        severity=Severity.INFO,
                        before=old_raw,
                        after=new_raw,
                        detail={"facet": facet, "direction": "relaxed"},
                    )
                )
                continue

            items.append(
                DiffItem(
                    change_type=ChangeType.LENGTH_RESTRICTED,
                    path=path,
                    severity=DEFAULT_SEVERITY[ChangeType.LENGTH_RESTRICTED],
                    before=old_raw,
                    after=new_raw,
                    detail={"facet": facet, "direction": "tightened"},
                )
            )

        return items

    @staticmethod
    def _is_narrower(old_raw: str | None, new_raw: str | None) -> bool | None:
        if old_raw is None or new_raw is None:
            return None
        try:
            return int(new_raw) < int(old_raw)
        except ValueError:
            return None

    def _compare_enumerations(self, old: XsdElement, new: XsdElement, path: str) -> list[DiffItem]:
        old_values = set(old.enumerations)
        new_values = set(new.enumerations)
        if old_values == new_values:
            return []

        items: list[DiffItem] = []

        added = sorted(new_values - old_values)
        if added:
            items.append(
                DiffItem(
                    change_type=ChangeType.ENUM_VALUE_ADDED,
                    path=path,
                    severity=DEFAULT_SEVERITY[ChangeType.ENUM_VALUE_ADDED],
                    before=None,
                    after=added,
                    detail={"count": len(added)},
                )
            )

        removed = sorted(old_values - new_values)
        if removed:
            items.append(
                DiffItem(
                    change_type=ChangeType.ENUM_VALUE_REMOVED,
                    path=path,
                    severity=DEFAULT_SEVERITY[ChangeType.ENUM_VALUE_REMOVED],
                    before=removed,
                    after=None,
                    detail={"count": len(removed)},
                )
            )

        return items

    # -- heuristica de renombrado -----------------------------------------------

    def _match_renames(
        self,
        removed: dict[str, XsdElement],
        added: dict[str, XsdElement],
    ) -> tuple[list[tuple[XsdElement, XsdElement]], set[str], set[str]]:
        """Empareja bajas con altas que parezcan el mismo elemento con otro nombre.

        Criterio deliberadamente estrecho: mismo padre, mismo tipo declarado, y una
        unica pareja posible dentro de ese padre. Si hay dos candidatos, no se decide:
        se dejan como alta y baja, que es el resultado seguro.

        El contexto sugeria usar tambien la posicion en el documento. No se usa: la
        forma normalizada esta ordenada por ruta y no conserva el orden original, y
        anadirlo haria que insertar un campo desplazase a todos los siguientes.
        """
        buckets: dict[tuple[str, str | None], tuple[list[XsdElement], list[XsdElement]]] = {}

        for element in removed.values():
            key = (_parent_of(element.path), element.type_name)
            buckets.setdefault(key, ([], []))[0].append(element)

        for element in added.values():
            key = (_parent_of(element.path), element.type_name)
            buckets.setdefault(key, ([], []))[1].append(element)

        renamed: list[tuple[XsdElement, XsdElement]] = []
        matched_removed: set[str] = set()
        matched_added: set[str] = set()

        for (_, type_name), (olds, news) in buckets.items():
            if type_name is None:
                # Sin tipo declarado no hay evidencia suficiente para afirmar nada.
                continue
            if len(olds) != 1 or len(news) != 1:
                continue
            renamed.append((olds[0], news[0]))
            matched_removed.add(olds[0].path)
            matched_added.add(news[0].path)

        return renamed, removed.keys() - matched_removed, added.keys() - matched_added


def compare_xsd(before: XsdForm, after: XsdForm) -> StructuralDiff:
    """Atajo funcional sobre `XsdDiffDetector`."""
    return XsdDiffDetector().compare(before, after)
