"""Forma comun de todos los diffs, sea cual sea lo que se compare.

Empezo viviendo dentro del detector de XSD, que era el unico que habia. Al aparecer el
de indices hubo que sacarlo: lo que se guarda en `change_event.structural_diff` tiene
que tener la misma forma venga de donde venga, porque quien lo lee —la bandeja de
revision, el analista de la fase 2, el cliente— es el mismo y no deberia saber que
normalizador produjo el dato.

`diff_schema_version` dice que detector lo genero (`xsd-diff/1`, `index-diff/1`,
`content-hash/1`). Es lo que permite evolucionar una salida sin invalidar los diffs ya
guardados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from regwatch.core.enums import ChangeType, Severity


@dataclass(frozen=True, slots=True)
class DiffItem:
    """Un cambio concreto sobre una ruta.

    En un esquema la `path` es la ruta canonica del elemento; en un indice, la URL de
    la entrada. En los dos casos es la clave estable con la que se emparejan las dos
    versiones, que es lo unico que el resto del sistema necesita saber.
    """

    change_type: ChangeType
    path: str
    severity: Severity
    before: Any = None
    after: Any = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.change_type.value,
            "path": self.path,
            "severity": self.severity.value,
            "before": self.before,
            "after": self.after,
            "detail": dict(self.detail),
        }


@dataclass(slots=True)
class StructuralDiff:
    """Resultado completo. Es la fuente de verdad de la que bebe todo lo demas."""

    items: list[DiffItem] = field(default_factory=list)
    diff_schema_version: str = "diff/1"
    is_partial: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.items

    def max_severity(self) -> Severity:
        if not self.items:
            return Severity.INFO
        return max((item.severity for item in self.items), key=lambda s: s.rank())

    def of_type(self, change_type: ChangeType) -> list[DiffItem]:
        return [item for item in self.items if item.change_type is change_type]

    def affected_paths(self) -> list[str]:
        return sorted({item.path for item in self.items})

    def to_json(self) -> dict[str, Any]:
        return {
            "diff_schema_version": self.diff_schema_version,
            "is_partial": self.is_partial,
            "notes": list(self.notes),
            "max_severity": self.max_severity().value,
            "items": [item.to_json() for item in self.items],
        }
