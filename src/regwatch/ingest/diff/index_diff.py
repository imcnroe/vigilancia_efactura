"""Detector de cambios entre dos formas `INDEX_ENTRIES`.

Un indice no tiene estructura que romper: no hay campos obligatorios ni cardinalidades.
Lo que tiene es un catalogo de publicaciones, y los unicos sucesos que importan son que
aparezca una, que desaparezca, o que la misma URL cambie de version o de titulo.

Sobre la severidad. La tabla del apartado 7.3 clasifica cambios de esquema por su
impacto en el emisor de facturas, y ninguna de sus filas encaja aqui: una entrada nueva
en el indice no rompe a nadie **todavia**, porque nadie ha mirado aun que hay dentro.
Pero dejarla en `INFO` la entierra junto a los cambios cosmeticos, y una publicacion
nueva de la AEAT es justo el aviso por el que alguien paga este producto. Asi que:

- **Alta** y **baja** de entrada: `REQUIRES_CHANGE`. No afirma que haya que cambiar
  codigo; afirma que una persona tiene que mirarlo. Que es la verdad.
- **Cambio de version** sobre la misma URL: `REQUIRES_CHANGE`. Un organismo que
  resustituye el fichero de una URL estable sin cambiar la URL es el caso de Gipuzkoa
  del catalogo semilla, y es el que la vigilancia por numero de version no ve.
- **Cambio solo de titulo**: `INFO`. Retitular un enlace no publica nada.

Nada de esto descarga los ficheros enlazados. Descubrir no es capturar: la entrada
nueva se convierte en una `source` cuando un operador decide que merece vigilancia, y
esa decision no la toma un detector a las seis de la manana.
"""

from __future__ import annotations

from typing import Final

from regwatch.core.enums import ChangeType, Severity
from regwatch.ingest.diff.base import DiffItem, StructuralDiff
from regwatch.ingest.normalizers.html_index import IndexEntry, IndexForm

#: Version del formato del JSONB `structural_diff` que sale de aqui.
DIFF_SCHEMA_VERSION: Final = "index-diff/1"

DEFAULT_SEVERITY: Final[dict[ChangeType, Severity]] = {
    ChangeType.INDEX_ENTRY_ADDED: Severity.REQUIRES_CHANGE,
    ChangeType.INDEX_ENTRY_REMOVED: Severity.REQUIRES_CHANGE,
    ChangeType.INDEX_ENTRY_UPDATED: Severity.INFO,
}


class IndexDiffDetector:
    """Compara dos catalogos de publicaciones."""

    def compare(self, before: IndexForm, after: IndexForm) -> StructuralDiff:
        diff = StructuralDiff(diff_schema_version=DIFF_SCHEMA_VERSION)

        if before.is_partial or after.is_partial:
            diff.is_partial = True
            diff.notes.append(
                "una de las dos formas es parcial: el selector no caso con nada o se "
                "trunco la lista, asi que las altas y bajas pueden no ser reales"
            )
            # Con una forma vacia por un selector roto, todo el catalogo anterior
            # apareceria como retirado. Es un falso positivo con forma de catastrofe,
            # asi que no se emite: se cuenta y ya.
            if not before.entries or not after.entries:
                diff.notes.append(
                    "una de las dos listas esta vacia: no se emiten altas ni bajas "
                    "porque serian el catalogo entero"
                )
                return diff

        old = before.by_url()
        new = after.by_url()

        for url in sorted(new.keys() - old.keys()):
            entry = new[url]
            diff.items.append(
                DiffItem(
                    change_type=ChangeType.INDEX_ENTRY_ADDED,
                    path=url,
                    severity=DEFAULT_SEVERITY[ChangeType.INDEX_ENTRY_ADDED],
                    after=entry.to_json(),
                    detail=_detail(entry),
                )
            )

        for url in sorted(old.keys() - new.keys()):
            entry = old[url]
            diff.items.append(
                DiffItem(
                    change_type=ChangeType.INDEX_ENTRY_REMOVED,
                    path=url,
                    severity=DEFAULT_SEVERITY[ChangeType.INDEX_ENTRY_REMOVED],
                    before=entry.to_json(),
                    detail=_detail(entry),
                )
            )

        for url in sorted(old.keys() & new.keys()):
            item = _compare_entry(old[url], new[url])
            if item is not None:
                diff.items.append(item)

        diff.items.sort(key=lambda item: (-item.severity.rank(), item.path))
        return diff


def _compare_entry(before: IndexEntry, after: IndexEntry) -> DiffItem | None:
    """La misma URL en las dos pasadas. Solo hay cambio si mudo la version o el titulo."""
    changed: dict[str, list[str | None]] = {}
    if before.version != after.version:
        changed["version"] = [before.version, after.version]
    if before.text != after.text:
        changed["text"] = [before.text, after.text]
    if not changed:
        return None

    severity = (
        Severity.REQUIRES_CHANGE
        if "version" in changed
        else DEFAULT_SEVERITY[ChangeType.INDEX_ENTRY_UPDATED]
    )
    detail: dict[str, object] = {"changed": changed}
    if "version" in changed:
        detail["reason"] = "version_changed_on_stable_url"

    return DiffItem(
        change_type=ChangeType.INDEX_ENTRY_UPDATED,
        path=after.url,
        severity=severity,
        before=before.to_json(),
        after=after.to_json(),
        detail=detail,
    )


def _detail(entry: IndexEntry) -> dict[str, object]:
    detail: dict[str, object] = {}
    if entry.version is not None:
        detail["version"] = entry.version
    if entry.filename is not None:
        detail["filename"] = entry.filename
    return detail


def compare_index(before: IndexForm, after: IndexForm) -> StructuralDiff:
    return IndexDiffDetector().compare(before, after)
