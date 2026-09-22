"""Detector de cambios entre dos formas `TEXT_BLOCKS`.

Un documento normativo se reescribe constantemente sin que cambie nada de lo que obliga:
se corrigen erratas, se reordenan apartados, se reexporta el PDF con otra plantilla. Si
cada una de esas cosas llegase como alarma, el operador dejaria de mirarlas, que es como
se pierde el cambio que si importaba.

## La regla de severidad, y por que es esta

El detector **no entiende** lo que lee. No sabe si un parrafo obliga a algo, y fingir que
si lo sabe seria el peor error posible en este producto. Lo unico que puede afirmar con
certeza es si el texto cambio y si **cambiaron las cifras**.

- **Bloque reescrito con las mismas cifras** -> `INFO`. Es una correccion de redaccion.
- **Bloque reescrito con cifras distintas** -> `REQUIRES_CHANGE`, con las cifras de antes
  y de despues en el detalle. En un documento normativo, un numero que cambia es una
  fecha de entrada en vigor, un plazo, un importe o una version: exactamente lo que el
  analista necesita ver. Es una heuristica, va marcada como tal en `detail.reason`, y
  prefiere el falso positivo al falso negativo **solo aqui**, porque el coste de no ver
  un plazo nuevo es mucho mayor que el de mirar un parrafo de mas.
- **Bloque nuevo o retirado** -> `INFO`. Anadir o quitar un parrafo es lo mas comun al
  actualizar una guia. Lo que importa es que quede acotado donde, no que suene una alarma.

Nada de esto decide la severidad de la ficha: el apartado 7.3 deja la ultima palabra al
editor humano, y aqui mas que en ningun otro detector.

**Limitacion conocida.** La regla mira digitos, asi que un plazo redactado en letra
—«cuatro dias naturales» pasa a «ocho dias naturales»— sale como `INFO`. El cambio se
emite igual y queda acotado al parrafo; lo que no sube es la severidad. No se persigue
porque hacerlo bien exige entender el texto en castellano y en frances, y un detector que
casi entiende es peor que uno que dice hasta donde llega. Hay un test que lo fija, para
que nadie lo descubra en produccion.

## Como se emparejan dos versiones

El texto normalizado es la identidad del bloque: lo que aparece igual en las dos
versiones no ha cambiado, este en la pagina que este. Mover un apartado de sitio no emite
nada. Lo que queda sin pareja exacta se alinea con `difflib.SequenceMatcher`, que es
determinista, y solo ahi se decide si un bloque fue reescrito o es un alta y una baja.
"""

from __future__ import annotations

import difflib
from typing import Final

from regwatch.core.enums import ChangeType, Severity
from regwatch.ingest.diff.base import DiffItem, StructuralDiff
from regwatch.ingest.normalizers.narrative import TextBlock, TextForm, numbers_in

#: Version del formato del JSONB `structural_diff` que sale de aqui.
DIFF_SCHEMA_VERSION: Final = "text-diff/1"

DEFAULT_SEVERITY: Final[dict[ChangeType, Severity]] = {
    ChangeType.TEXT_BLOCK_ADDED: Severity.INFO,
    ChangeType.TEXT_BLOCK_REMOVED: Severity.INFO,
    ChangeType.TEXT_BLOCK_CHANGED: Severity.INFO,
}

#: Parecido minimo para considerar que un bloque fue reescrito en vez de sustituido. Por
#: debajo son textos distintos, y emparejarlos produciria un "cambio" ilegible entre dos
#: parrafos que no tienen nada que ver.
SIMILARITY_THRESHOLD: Final = 0.6

#: Tope de cambios que se detallan. Una reedicion completa de una guia de 80 paginas
#: produce miles, y un JSONB de miles de parrafos no lo lee nadie ni cabe comodo.
MAX_ITEMS: Final = 300


class TextDiffDetector:
    """Compara dos documentos narrativos."""

    def compare(self, before: TextForm, after: TextForm) -> StructuralDiff:
        diff = StructuralDiff(diff_schema_version=DIFF_SCHEMA_VERSION)

        if before.is_partial or after.is_partial:
            diff.is_partial = True
            diff.notes.append(
                "una de las dos formas es parcial: no se extrajo todo el texto, asi que "
                "puede haber cambios no vistos"
            )
            if not before.blocks or not after.blocks:
                diff.notes.append(
                    "una de las dos versiones no dio texto: no se emiten altas ni bajas "
                    "porque serian el documento entero"
                )
                return diff

        removed, added = self._unmatched(before, after)
        pairs, removed, added = self._align(removed, added)

        for old, new in pairs:
            item = self._changed(old, new)
            if item is not None:
                diff.items.append(item)

        for block in added:
            diff.items.append(
                DiffItem(
                    change_type=ChangeType.TEXT_BLOCK_ADDED,
                    path=_path_of(block),
                    severity=DEFAULT_SEVERITY[ChangeType.TEXT_BLOCK_ADDED],
                    after=block.to_json(),
                    detail=_detail(block),
                )
            )

        for block in removed:
            diff.items.append(
                DiffItem(
                    change_type=ChangeType.TEXT_BLOCK_REMOVED,
                    path=_path_of(block),
                    severity=DEFAULT_SEVERITY[ChangeType.TEXT_BLOCK_REMOVED],
                    before=block.to_json(),
                    detail=_detail(block),
                )
            )

        diff.items.sort(key=lambda item: (-item.severity.rank(), item.path))

        if len(diff.items) > MAX_ITEMS:
            diff.notes.append(
                f"el documento cambio en {len(diff.items)} bloques y solo se detallan los "
                f"{MAX_ITEMS} mas severos: probablemente es una reedicion completa, no una "
                f"correccion"
            )
            diff.is_partial = True
            del diff.items[MAX_ITEMS:]

        return diff

    @staticmethod
    def _unmatched(before: TextForm, after: TextForm) -> tuple[list[TextBlock], list[TextBlock]]:
        """Lo que no aparece literalmente en la otra version.

        El texto es la identidad: lo que coincide exacto no ha cambiado, aunque se haya
        movido de pagina o de apartado.
        """
        old_texts = {block.text for block in before.blocks}
        new_texts = {block.text for block in after.blocks}

        removed = [block for block in before.by_text().values() if block.text not in new_texts]
        added = [block for block in after.by_text().values() if block.text not in old_texts]
        return removed, added

    @staticmethod
    def _align(
        removed: list[TextBlock], added: list[TextBlock]
    ) -> tuple[list[tuple[TextBlock, TextBlock]], list[TextBlock], list[TextBlock]]:
        """Empareja bajas con altas parecidas: son reescrituras, no sustituciones.

        Se recorre en orden de documento y cada bloque se empareja como mucho una vez,
        de modo que el resultado no depende del orden de iteracion de ningun conjunto.
        """
        pairs: list[tuple[TextBlock, TextBlock]] = []
        free = sorted(added, key=lambda block: block.position)
        taken: set[int] = set()

        for old in sorted(removed, key=lambda block: block.position):
            best: TextBlock | None = None
            best_ratio = SIMILARITY_THRESHOLD
            for candidate in free:
                if candidate.position in taken:
                    continue
                ratio = difflib.SequenceMatcher(None, old.text, candidate.text).ratio()
                if ratio > best_ratio:
                    best, best_ratio = candidate, ratio
            if best is not None:
                pairs.append((old, best))
                taken.add(best.position)

        matched_old = {old.position for old, _ in pairs}
        return (
            pairs,
            [block for block in removed if block.position not in matched_old],
            [block for block in free if block.position not in taken],
        )

    @staticmethod
    def _changed(old: TextBlock, new: TextBlock) -> DiffItem | None:
        if old.text == new.text:
            return None

        before_numbers = numbers_in(old.text)
        after_numbers = numbers_in(new.text)
        numbers_moved = before_numbers != after_numbers

        detail: dict[str, object] = {}
        if numbers_moved:
            # La heuristica, dicha en voz alta: no sabemos que significa el parrafo, solo
            # que sus cifras ya no son las mismas.
            detail["reason"] = "numbers_changed"
            detail["numbers_before"] = before_numbers
            detail["numbers_after"] = after_numbers
            severity = Severity.REQUIRES_CHANGE
        else:
            detail["reason"] = "rewritten_without_changing_any_figure"
            severity = DEFAULT_SEVERITY[ChangeType.TEXT_BLOCK_CHANGED]

        if new.section is not None:
            detail["section"] = new.section

        return DiffItem(
            change_type=ChangeType.TEXT_BLOCK_CHANGED,
            path=_path_of(new),
            severity=severity,
            before=old.to_json(),
            after=new.to_json(),
            detail=detail,
        )


def _path_of(block: TextBlock) -> str:
    """Identificador legible del bloque, para que la ficha pueda citarlo.

    Lleva el apartado porque es lo que un humano busca en el documento; y el orden, que
    desempata entre parrafos de la misma seccion. No es una clave de emparejamiento: eso
    lo hace el texto.
    """
    section = block.section or "(sin apartado)"
    return f"{section} #{block.position}"


def _detail(block: TextBlock) -> dict[str, object]:
    detail: dict[str, object] = {"kind": block.kind}
    if block.section is not None:
        detail["section"] = block.section
    return detail


def compare_text(before: TextForm, after: TextForm) -> StructuralDiff:
    return TextDiffDetector().compare(before, after)
