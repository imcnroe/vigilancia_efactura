"""Servicio de ingesta: une colector, almacen y base de datos.

Es la unica pieza que toca las tres cosas a la vez. El colector devuelve bytes; el
almacen guarda bytes; aqui se decide si esos bytes son un artefacto nuevo, se
normalizan, se comparan con el anterior y se deja constancia de todo ello.

Reglas que se hacen cumplir aqui y en ningun otro sitio:

- Hash igual -> no hay artefacto nuevo; solo se toca `last_checked_at` (apartado 7.1).
- El artefacto se guarda entero y antes que nada. Si el normalizador falla, el
  artefacto ya esta a salvo: es el activo, y lo demas se puede reprocesar.
- Ningun cambio de contenido pasa en silencio. Si no se puede comparar la estructura,
  se emite un `change_event` de solo-hash para que lo mire una persona.
- El diff se calcula contra el ultimo artefacto **normalizable**, no contra el
  inmediatamente anterior. Un portal que sirve una pagina de mantenimiento con 200
  genera un artefacto ilegible; el XSD que llega despues tiene que compararse con el
  XSD de antes, o el cambio estructural se pierde.
- Heartbeat: `heartbeat_threshold` fallos seguidos abren una `pipeline_incident`. El
  fallo silencioso de un colector es el unico riesgo existencial del producto.
- Cada fuente corre en su propia transaccion. Una fuente que falla, o que tiene un bug,
  no bloquea a las demas.
- El primer artefacto de una fuente no genera evento: no hay contra que comparar. Se
  ve en `source.last_success_at` y en la propia fila de `artifact`.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urljoin

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from regwatch.alerts.models import PipelineIncident
from regwatch.catalog.models import Source
from regwatch.catalog.repository import UnknownSourceError
from regwatch.changes.models import ChangeEvent
from regwatch.core.enums import ChangeStatus, FormType, IncidentKind, Priority, Severity
from regwatch.ingest.collectors.base import (
    CollectorError,
    CollectorRegistry,
    FetchResult,
    NotModified,
)
from regwatch.ingest.collectors.http_file import headers_to_config
from regwatch.ingest.diff.base import StructuralDiff
from regwatch.ingest.diff.index_diff import compare_index
from regwatch.ingest.diff.text_diff import compare_text
from regwatch.ingest.diff.xsd_diff import compare_xsd
from regwatch.ingest.models import Artifact, NormalizedForm
from regwatch.ingest.normalizers.dispatch import (
    NormalizationOutcome,
    Normalized,
    NotNormalizable,
    normalize_content,
    schema_locations,
)
from regwatch.ingest.normalizers.html_index import PARSER_VERSION as INDEX_PARSER_VERSION
from regwatch.ingest.normalizers.html_index import IndexForm
from regwatch.ingest.normalizers.narrative import PARSER_VERSION as TEXT_PARSER_VERSION
from regwatch.ingest.normalizers.narrative import TextForm
from regwatch.ingest.normalizers.xsd import PARSER_VERSION, XsdForm
from regwatch.ingest.schedule import InvalidCronError, next_run
from regwatch.ingest.storage.artifact_store import ArtifactStore

log = logging.getLogger(__name__)

#: Version del `structural_diff` que se emite cuando solo se sabe que el contenido
#: cambio. Distinta de la del detector para que nadie lo confunda con un diff real.
HASH_ONLY_DIFF_VERSION: Final = "content-hash/1"

#: Claves de `collector_config` que pertenecen a la ultima respuesta, no al operador.
VALIDATOR_KEYS: Final = ("etag", "last_modified")

#: Orden de ejecucion en `run_due`: las HOT primero.
PRIORITY_ORDER: Final[dict[str, int]] = {
    Priority.HOT.value: 0,
    Priority.WARM.value: 1,
    Priority.COLD.value: 2,
}

#: Si el cron de una fuente esta corrupto, se reintenta en un dia en vez de nunca.
FALLBACK_INTERVAL: Final = dt.timedelta(days=1)

#: Cuantos artefactos hacia atras se busca uno normalizable con el que comparar. Acota
#: el coste de una fuente que lleva meses sirviendo basura.
BASELINE_LOOKBACK: Final = 20

Clock = Callable[[], dt.datetime]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class RunStatus(StrEnum):
    NEW_ARTIFACT = "NEW_ARTIFACT"
    #: 200 con el mismo hash que el ultimo artefacto.
    UNCHANGED = "UNCHANGED"
    #: 304: el servidor confirma que no ha cambiado nada.
    NOT_MODIFIED = "NOT_MODIFIED"
    FAILED = "FAILED"
    #: Inactiva, borrada, no vencida o en curso en otro proceso.
    SKIPPED = "SKIPPED"


@dataclass(slots=True)
class RunReport:
    """Lo que paso al ejecutar una fuente. Es lo que imprime la CLI y lo que se loguea."""

    source_id: uuid.UUID
    source_name: str
    status: RunStatus
    artifact_ids: list[uuid.UUID] = field(default_factory=list)
    change_event_ids: list[uuid.UUID] = field(default_factory=list)
    max_severity: str | None = None
    consecutive_failures: int = 0
    incident_opened: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "source_id": str(self.source_id),
            "source_name": self.source_name,
            "status": self.status.value,
            "artifact_ids": [str(item) for item in self.artifact_ids],
            "change_event_ids": [str(item) for item in self.change_event_ids],
            "max_severity": self.max_severity,
            "consecutive_failures": self.consecutive_failures,
            "incident_opened": self.incident_opened,
            "error": self.error,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class PreviewReport:
    """Resultado de `source test`: que devuelve el colector, sin persistir nada."""

    source_id: uuid.UUID
    source_name: str
    fetched_url: str
    http_status: int | None
    mime_type: str | None
    size_bytes: int
    content_hash: str
    filename: str | None
    #: Nulo si la fuente no tiene todavia ningun artefacto.
    matches_latest_artifact: bool | None
    normalization: str
    element_count: int | None = None
    declared_version: str | None = None
    is_partial: bool = False
    missing_dependencies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReprocessReport:
    """Resultado de volver a normalizar y comparar el historico de una fuente."""

    source_id: uuid.UUID
    source_name: str
    artifacts: int = 0
    forms_created: int = 0
    events_created: int = 0
    events_updated: int = 0
    events_kept: int = 0
    warnings: list[str] = field(default_factory=list)


#: Formas tipadas que se saben comparar. Cada una tiene su detector; mezclarlas no se
#: intenta, porque una fuente que pasa de servir un XSD a servir un indice ha cambiado
#: de naturaleza y eso lo mira una persona.
TypedForm = XsdForm | IndexForm | TextForm


@dataclass(frozen=True, slots=True)
class _FormLookup:
    """Forma de un artefacto, calculada o leida, y por que no la hay si no la hay."""

    form: TypedForm | None
    reason: str | None
    created: bool


@dataclass(frozen=True, slots=True)
class _Baseline:
    """Contra que se compara un artefacto nuevo, y que quedo en medio sin comparar."""

    artifact: Artifact
    lookup: _FormLookup
    skipped: list[Artifact]


def absolute_location(base_url: str, location: str) -> str:
    """Resuelve un `schemaLocation` relativo contra la URL del documento que lo cita."""
    return urljoin(base_url, location)


class IngestService:
    """Orquesta una pasada completa sobre una fuente."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        store: ArtifactStore,
        collectors: CollectorRegistry,
        *,
        heartbeat_threshold: int = 2,
        clock: Clock = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._collectors = collectors
        self._heartbeat_threshold = heartbeat_threshold
        self._clock = clock

    # -- API publica -------------------------------------------------------------

    def run_source(self, source_id: uuid.UUID) -> RunReport:
        """Ejecuta una fuente ahora, este vencida o no. Es lo que usa `collect run`."""
        return self._run_guarded(source_id, skip_locked=False, only_if_due=False)

    def run_due(self, *, limit: int | None = None) -> list[RunReport]:
        """Ejecuta las fuentes vencidas, HOT primero. Es lo que llama el cron.

        Cada fuente va en su transaccion y se bloquea con `SKIP LOCKED`, de modo que dos
        pasadas solapadas no procesan la misma fuente ni se esperan la una a la otra.
        """
        now = self._clock()
        with self._session_factory() as session:
            rows = session.execute(
                select(Source.id, Source.priority)
                .where(
                    Source.is_active.is_(True),
                    Source.deleted_at.is_(None),
                    or_(Source.next_check_at.is_(None), Source.next_check_at <= now),
                )
                .order_by(Source.next_check_at.nulls_first(), Source.name)
            ).all()

        ordered = sorted(rows, key=lambda row: PRIORITY_ORDER.get(row.priority, 99))
        if limit is not None:
            ordered = ordered[:limit]

        log.info("run_due", extra={"due_sources": len(ordered)})
        return [self._run_guarded(row.id, skip_locked=True, only_if_due=True) for row in ordered]

    def preview(self, source_id: uuid.UUID) -> list[PreviewReport]:
        """Ejecuta el colector de una fuente y cuenta que devuelve, sin escribir nada.

        Se quitan los validadores (`etag`, `last_modified`) para forzar la descarga: el
        operador quiere ver el contenido, no un 304. Los errores del colector se
        propagan tal cual; aqui no hay heartbeat porque no hay pasada.
        """
        with self._session_factory() as session:
            source = self._get_source(session, source_id)
            latest = self._latest_artifact(session, source.id)
            collector = self._collectors.get(source.collector_type)
            config = {
                key: value
                for key, value in source.collector_config.items()
                if key not in VALIDATOR_KEYS
            }
            output = collector.fetch(source.url, config)

            if isinstance(output, NotModified):
                raise CollectorError(
                    f"{source.url} respondio 304 aunque no se enviaron validadores; el "
                    f"servidor o un proxy intermedio esta cacheando de mas"
                )

            reports: list[PreviewReport] = []
            for fetched in output:
                outcome = self._normalize(session, source, fetched.url, fetched.content)
                report = PreviewReport(
                    source_id=source.id,
                    source_name=source.name,
                    fetched_url=fetched.url,
                    http_status=fetched.http_status,
                    mime_type=fetched.mime_type,
                    size_bytes=fetched.size_bytes,
                    content_hash=fetched.content_hash,
                    filename=fetched.filename,
                    matches_latest_artifact=(
                        None if latest is None else latest.content_hash == fetched.content_hash
                    ),
                    normalization="",
                )
                if isinstance(outcome, Normalized):
                    report.normalization = outcome.form_type
                    # Un esquema cuenta elementos, un indice entradas y un documento
                    # narrativo bloques. Leer siempre `elements` daria «0» en todo lo que
                    # no fuera un XSD, que es peor que no decir nada: parece que el
                    # selector no ha casado.
                    report.element_count = len(
                        outcome.payload.get("elements")
                        or outcome.payload.get("entries")
                        or outcome.payload.get("blocks")
                        or []
                    )
                    report.declared_version = outcome.declared_version
                    report.is_partial = outcome.is_partial
                    report.missing_dependencies = list(outcome.missing_dependencies)
                    report.warnings = list(outcome.warnings)
                else:
                    report.normalization = f"sin normalizar: {outcome.reason}"
                reports.append(report)
            return reports

    def reprocess_source(self, source_id: uuid.UUID) -> ReprocessReport:
        """Vuelve a normalizar el historico de una fuente con el parser actual y
        recalcula el diff de cada artefacto contra su linea base.

        Es la reprocesabilidad del apartado 7: mejorar el parser no obliga a volver a
        descargar nada. Idempotente: las formas ya calculadas con este `parser_version`
        se reutilizan y cada artefacto tiene como mucho un evento. Solo se sobreescribe
        el diff de los eventos aun en `DETECTED`; lo que una persona ya reviso no se
        toca.
        """
        now = self._clock()
        with self._session_factory() as session, session.begin():
            source = self._get_source(session, source_id)
            report = ReprocessReport(source_id=source.id, source_name=source.name)

            artifacts = session.scalars(
                select(Artifact)
                .where(Artifact.source_id == source.id)
                .order_by(Artifact.captured_at.asc(), Artifact.id.asc())
            ).all()
            report.artifacts = len(artifacts)

            cache: dict[uuid.UUID, _FormLookup] = {}
            for index, artifact in enumerate(artifacts):
                lookup = self._form_of(session, source, artifact)
                cache[artifact.id] = lookup
                if lookup.created:
                    report.forms_created += 1
                if lookup.form is None and lookup.reason:
                    report.warnings.append(f"{artifact.id}: {lookup.reason}")
                if index == 0:
                    continue

                # Solo los candidatos que `_select_baseline` va a mirar: un historico de
                # años no tiene por que copiarse entero en cada iteracion.
                earlier = list(reversed(artifacts[max(0, index - BASELINE_LOOKBACK) : index]))
                baseline = self._select_baseline(session, source, lookup, earlier, cache)
                if baseline is not None:
                    self._reprocess_artifact(
                        session, source, baseline, artifact, lookup, now, report
                    )
            return report

    # -- una pasada --------------------------------------------------------------

    def _run_guarded(
        self, source_id: uuid.UUID, *, skip_locked: bool, only_if_due: bool
    ) -> RunReport:
        try:
            return self._run(source_id, skip_locked=skip_locked, only_if_due=only_if_due)
        except UnknownSourceError:
            raise
        except Exception as error:
            # no puede parar la cola ni pasar en silencio: cuenta como fallo para que el
            # heartbeat lo vea, y se loguea con traza para que alguien lo arregle.
            log.exception(
                "fallo inesperado en la ingesta",
                extra={"source_id": str(source_id), "error": str(error)},
            )
            return self._record_unexpected_failure(source_id, error)

    def _run(self, source_id: uuid.UUID, *, skip_locked: bool, only_if_due: bool) -> RunReport:
        now = self._clock()
        with self._session_factory() as session, session.begin():
            probe = self._get_source(session, source_id)
            source = self._lock_source(session, source_id, skip_locked)
            if source is None:
                return RunReport(
                    probe.id, probe.name, RunStatus.SKIPPED, error="en curso en otro proceso"
                )

            report = RunReport(source.id, source.name, RunStatus.UNCHANGED)
            if not source.is_active or source.deleted_at is not None:
                report.status = RunStatus.SKIPPED
                report.error = "fuente inactiva o borrada"
                return report
            if only_if_due and source.next_check_at is not None and source.next_check_at > now:
                report.status = RunStatus.SKIPPED
                report.error = "no vencida"
                return report

            try:
                collector = self._collectors.get(source.collector_type)
                output = collector.fetch(source.url, dict(source.collector_config))
            except CollectorError as error:
                return self._record_failure(session, source, report, error, now)

            if isinstance(output, NotModified):
                report.status = RunStatus.NOT_MODIFIED
                self._record_success(session, source, now)
                return report

            for fetched in output:
                self._ingest_fetch(session, source, fetched, report, now)

            if len(output) == 1:
                # Los validadores describen un recurso concreto. Con varios resultados
                # (un indice) no se sabe de cual serian, asi que no se guardan.
                self._remember_validators(source, output[0])

            self._record_success(session, source, now)
            log.info(
                "pasada terminada",
                extra={
                    "source_id": str(source.id),
                    "status": report.status.value,
                    "artifacts": len(report.artifact_ids),
                    "change_events": len(report.change_event_ids),
                    "max_severity": report.max_severity,
                },
            )
            return report

    def _ingest_fetch(
        self,
        session: Session,
        source: Source,
        fetched: FetchResult,
        report: RunReport,
        now: dt.datetime,
    ) -> None:
        content_hash = fetched.content_hash
        if self._artifact_exists(session, source.id, content_hash):
            log.info(
                "contenido sin cambios",
                extra={"source_id": str(source.id), "content_hash": content_hash},
            )
            return

        outcome = self._normalize(session, source, fetched.url, fetched.content)

        declared_version = fetched.declared_version
        if declared_version is None and isinstance(outcome, Normalized):
            declared_version = outcome.declared_version
        if declared_version is not None and len(declared_version) > 64:
            report.warnings.append(f"declared_version truncada a 64 caracteres: {declared_version}")
            declared_version = declared_version[:64]

        # El almacen primero. Es idempotente por construccion: si la transaccion falla
        # despues, la siguiente pasada encuentra el objeto y no lo reescribe.
        storage_key = self._store.put(fetched.content, content_type=fetched.mime_type)

        # Todos los campos antes del INSERT: un trigger prohibe el UPDATE de artifact.
        artifact = Artifact(
            source_id=source.id,
            captured_at=fetched.fetched_at,
            content_hash=content_hash,
            storage_key=storage_key,
            mime_type=fetched.mime_type,
            size_bytes=fetched.size_bytes,
            original_filename=fetched.filename,
            fetched_url=fetched.url,
            declared_version=declared_version,
            http_status=fetched.http_status,
            http_headers=dict(fetched.http_headers) or None,
        )
        session.add(artifact)
        session.flush()
        report.artifact_ids.append(artifact.id)
        report.status = RunStatus.NEW_ARTIFACT
        log.info(
            "nuevo artefacto",
            extra={
                "source_id": str(source.id),
                "artifact_id": str(artifact.id),
                "content_hash": content_hash,
                "size_bytes": fetched.size_bytes,
                "declared_version": declared_version,
            },
        )

        lookup = self._persist_form(session, artifact, outcome)
        if lookup.form is not None and lookup.form.is_partial:
            report.warnings.append(
                f"forma parcial: faltan {', '.join(lookup.form.missing_dependencies)}"
            )
        if lookup.form is None and lookup.reason:
            report.warnings.append(f"artefacto sin normalizar: {lookup.reason}")

        earlier = self._earlier_artifacts(session, source.id, artifact)
        baseline = self._select_baseline(session, source, lookup, earlier, {})
        if baseline is None:
            return  # primer artefacto de la fuente: no hay contra que comparar

        event = self._create_event(session, source, baseline, artifact, lookup, now)
        report.change_event_ids.append(event.id)
        report.max_severity = _max_severity(report.max_severity, event.severity_suggested)

    # -- normalizacion -----------------------------------------------------------

    def _normalize(
        self, session: Session, source: Source, base_url: str, content: bytes
    ) -> NormalizationOutcome:
        """Normaliza resolviendo `include`/`import` contra artefactos ya descargados.

        Dos pasadas: la primera descubre que dependencias faltan, se buscan en el
        historico, y la segunda las incorpora. El normalizador nunca sale a la red.
        """
        config = dict(source.collector_config)
        first = normalize_content(
            source.source_kind, content, None, base_url=base_url, config=config
        )
        if isinstance(first, NotNormalizable):
            return first

        locations = schema_locations(first.missing_dependencies)
        if not locations:
            return first

        dependencies = self._resolve_dependencies(session, base_url, locations)
        if not dependencies:
            return first
        return normalize_content(
            source.source_kind, content, dependencies, base_url=base_url, config=config
        )

    def _resolve_dependencies(
        self, session: Session, base_url: str, locations: list[str]
    ) -> dict[str, bytes]:
        """Busca cada `schemaLocation` entre los artefactos ya capturados.

        Se acepta tanto la URL descargada (`fetched_url`, tras redirecciones) como la
        URL de la fuente. Para que una dependencia se resuelva basta con darla de alta
        como fuente propia, normalmente `COLD`.
        """
        found: dict[str, bytes] = {}
        for location in locations:
            candidates = {location, absolute_location(base_url, location)}
            artifact = session.scalar(
                select(Artifact)
                .join(Source, Artifact.source_id == Source.id)
                .where(or_(Artifact.fetched_url.in_(candidates), Source.url.in_(candidates)))
                .order_by(Artifact.captured_at.desc(), Artifact.id.desc())
                .limit(1)
            )
            if artifact is None:
                continue
            found[location] = self._store.get(artifact.storage_key)
        return found

    def _persist_form(
        self, session: Session, artifact: Artifact, outcome: NormalizationOutcome
    ) -> _FormLookup:
        if isinstance(outcome, NotNormalizable):
            return _FormLookup(form=None, reason=outcome.reason, created=False)

        session.add(
            NormalizedForm(
                artifact_id=artifact.id,
                form_type=outcome.form_type,
                payload=outcome.payload,
                parser_version=outcome.parser_version,
                is_partial=outcome.is_partial,
                parse_warnings=outcome.parse_warnings(),
            )
        )
        session.flush()
        return _FormLookup(form=outcome.typed_form, reason=None, created=True)

    def _form_of(self, session: Session, source: Source, artifact: Artifact) -> _FormLookup:
        """Forma comparable de un artefacto con el parser actual.

        Si no existe se calcula ahora desde el almacen y se guarda: las dos formas que
        se comparan tienen que salir del mismo parser, o el diff mide el parser y no el
        documento.
        """
        for form_type, parser_version, load in (
            (FormType.XSD_ELEMENTS.value, PARSER_VERSION, XsdForm.from_json),
            (FormType.INDEX_ENTRIES.value, INDEX_PARSER_VERSION, IndexForm.from_json),
            (FormType.TEXT_BLOCKS.value, TEXT_PARSER_VERSION, TextForm.from_json),
        ):
            row = session.scalar(
                select(NormalizedForm).where(
                    NormalizedForm.artifact_id == artifact.id,
                    NormalizedForm.form_type == form_type,
                    NormalizedForm.parser_version == parser_version,
                )
            )
            if row is not None:
                return _FormLookup(form=load(row.payload), reason=None, created=False)

        content = self._store.get(artifact.storage_key)
        outcome = self._normalize(session, source, artifact.fetched_url, content)
        return self._persist_form(session, artifact, outcome)

    # -- deteccion de cambios ----------------------------------------------------

    def _select_baseline(
        self,
        session: Session,
        source: Source,
        current_lookup: _FormLookup,
        earlier: Sequence[Artifact],
        cache: dict[uuid.UUID, _FormLookup],
    ) -> _Baseline | None:
        """Elige contra que artefacto se compara el actual.

        `earlier` va del mas reciente al mas antiguo. Si el actual tiene forma, se busca
        hacia atras el primero que tambien la tenga; los que quedan en medio se anotan
        como saltados. Si el actual no tiene forma, o ninguno anterior la tiene, la
        comparacion es de solo-hash contra el inmediatamente anterior.
        """
        if not earlier:
            return None

        def lookup(artifact: Artifact) -> _FormLookup:
            if artifact.id not in cache:
                cache[artifact.id] = self._form_of(session, source, artifact)
            return cache[artifact.id]

        previous = earlier[0]
        if current_lookup.form is None:
            return _Baseline(previous, lookup(previous), [])

        skipped: list[Artifact] = []
        for candidate in earlier[:BASELINE_LOOKBACK]:
            candidate_lookup = lookup(candidate)
            if candidate_lookup.form is not None:
                return _Baseline(candidate, candidate_lookup, skipped)
            skipped.append(candidate)
        return _Baseline(previous, lookup(previous), [])

    def _build_diff(
        self,
        baseline: _Baseline,
        current: Artifact,
        current_lookup: _FormLookup,
    ) -> tuple[dict[str, Any], str, Severity]:
        """`(structural_diff, diff_schema_version, severidad sugerida)`."""
        comparison = _comparable(baseline.lookup.form, current_lookup.form)
        if comparison is not None:
            diff, form_type, parser_version = comparison
            structural = diff.to_json()
            structural["comparison"] = form_type
            structural["parser_version"] = parser_version
            if diff.is_empty:
                structural["notes"].append(
                    "el contenido cambio (hash distinto) pero la forma normalizada es "
                    "identica: probablemente comentarios, anotaciones o formato"
                )
            if baseline.skipped:
                structural["notes"].append(
                    f"comparado con el artefacto {baseline.artifact.id} de "
                    f"{baseline.artifact.captured_at.isoformat()}: los "
                    f"{len(baseline.skipped)} artefactos intermedios no eran normalizables"
                )
                structural["skipped_artifact_ids"] = [str(item.id) for item in baseline.skipped]
            return structural, diff.diff_schema_version, diff.max_severity()

        notes = ["el contenido cambio pero no se pudo comparar la estructura"]
        for label, lookup in (("anterior", baseline.lookup), ("nuevo", current_lookup)):
            if lookup.form is None:
                notes.append(f"artefacto {label}: {lookup.reason or 'sin forma normalizada'}")
        if baseline.lookup.form is not None and current_lookup.form is not None:
            # Las dos tienen forma pero de tipos distintos: la fuente servia un esquema y
            # ahora sirve un indice, o al reves. No hay diff posible, y el cambio de
            # naturaleza es en si mismo lo que hay que contarle a alguien.
            notes.append(
                f"la fuente cambio de tipo de documento: "
                f"{type(baseline.lookup.form).__name__} -> {type(current_lookup.form).__name__}"
            )

        structural = {
            "diff_schema_version": HASH_ONLY_DIFF_VERSION,
            "comparison": "CONTENT_HASH_ONLY",
            "is_partial": True,
            "max_severity": Severity.INFO.value,
            "notes": notes,
            "items": [],
            "before": _artifact_summary(baseline.artifact),
            "after": _artifact_summary(current),
        }
        return structural, HASH_ONLY_DIFF_VERSION, Severity.INFO

    def _create_event(
        self,
        session: Session,
        source: Source,
        baseline: _Baseline,
        current: Artifact,
        current_lookup: _FormLookup,
        now: dt.datetime,
    ) -> ChangeEvent:
        structural, schema_version, severity = self._build_diff(baseline, current, current_lookup)
        event = ChangeEvent(
            source_id=source.id,
            artifact_from_id=baseline.artifact.id,
            artifact_to_id=current.id,
            detected_at=now,
            structural_diff=structural,
            diff_schema_version=schema_version,
            severity_suggested=severity.value,
            status=ChangeStatus.DETECTED.value,
        )
        session.add(event)
        session.flush()
        log.info(
            "cambio detectado",
            extra={
                "source_id": str(source.id),
                "change_event_id": str(event.id),
                "severity": severity.value,
                "items": len(structural["items"]),
                "comparison": structural["comparison"],
            },
        )
        return event

    def _reprocess_artifact(
        self,
        session: Session,
        source: Source,
        baseline: _Baseline,
        current: Artifact,
        current_lookup: _FormLookup,
        now: dt.datetime,
        report: ReprocessReport,
    ) -> None:
        """Un artefacto tiene como mucho un evento: el que lo compara con su linea base.

        Se busca por `artifact_to_id` y no por el par completo porque un parser nuevo
        puede cambiar la linea base (lo que antes era ilegible ahora se normaliza).
        """
        existing = session.scalars(
            select(ChangeEvent)
            .where(ChangeEvent.artifact_to_id == current.id)
            .order_by(ChangeEvent.detected_at.asc())
        ).first()
        if existing is None:
            self._create_event(session, source, baseline, current, current_lookup, now)
            report.events_created += 1
            return

        if existing.status != ChangeStatus.DETECTED.value:
            report.events_kept += 1
            return

        structural, schema_version, severity = self._build_diff(baseline, current, current_lookup)
        existing.artifact_from_id = baseline.artifact.id
        existing.structural_diff = structural
        existing.diff_schema_version = schema_version
        existing.severity_suggested = severity.value
        report.events_updated += 1

    # -- estado de la fuente -----------------------------------------------------

    def _record_success(self, session: Session, source: Source, now: dt.datetime) -> None:
        source.last_checked_at = now
        source.last_success_at = now
        source.consecutive_failures = 0
        source.next_check_at = self._next_check(source, now)
        self._resolve_incidents(session, source, now)

    def _record_failure(
        self,
        session: Session,
        source: Source,
        report: RunReport,
        error: Exception,
        now: dt.datetime,
    ) -> RunReport:
        source.last_checked_at = now
        source.consecutive_failures += 1
        source.next_check_at = self._next_check(source, now)

        report.status = RunStatus.FAILED
        report.error = str(error)
        report.consecutive_failures = source.consecutive_failures
        log.warning(
            "fallo del colector",
            extra={
                "source_id": str(source.id),
                "url": source.url,
                "consecutive_failures": source.consecutive_failures,
                "error": str(error),
            },
        )

        if source.consecutive_failures >= self._heartbeat_threshold:
            report.incident_opened = self._open_or_update_incident(session, source, error, now)
        return report

    def _record_unexpected_failure(self, source_id: uuid.UUID, error: Exception) -> RunReport:
        """Anota un fallo cuya transaccion ya se deshizo. Si ni esto se puede, se loguea
        y se devuelve el informe igual: el operador tiene que ver el error."""
        now = self._clock()
        try:
            with self._session_factory() as session, session.begin():
                source = self._lock_source(session, source_id, skip_locked=False)
                if source is None:
                    raise UnknownSourceError(f"no hay fuente con id {source_id}")
                report = RunReport(source.id, source.name, RunStatus.FAILED)
                return self._record_failure(session, source, report, error, now)
        except UnknownSourceError:
            raise
        except Exception as second:
            log.exception("no se pudo registrar el fallo", extra={"source_id": str(source_id)})
            return RunReport(
                source_id, "", RunStatus.FAILED, error=f"{error} (y al registrarlo: {second})"
            )

    def _open_or_update_incident(
        self, session: Session, source: Source, error: Exception, now: dt.datetime
    ) -> bool:
        """Abre la incidencia del heartbeat, o actualiza la que ya esta abierta.

        Devuelve cierto solo si se abrio una nueva: es el momento en el que hay que
        avisar al operador. En la fase 1 el aviso es este registro y una linea de log
        a nivel ERROR; el email llega con el motor de notificaciones.
        """
        detail: dict[str, Any] = {
            "url": source.url,
            "collector_type": source.collector_type,
            "consecutive_failures": source.consecutive_failures,
            "last_error": str(error),
            "last_failure_at": now.isoformat(),
        }
        incident = session.scalar(
            select(PipelineIncident).where(
                PipelineIncident.source_id == source.id,
                PipelineIncident.kind == IncidentKind.COLLECTOR_FAILING.value,
                PipelineIncident.resolved_at.is_(None),
            )
        )
        if incident is not None:
            incident.detail = detail
            return False

        session.add(
            PipelineIncident(
                source_id=source.id,
                kind=IncidentKind.COLLECTOR_FAILING.value,
                opened_at=now,
                detail=detail,
            )
        )
        log.error(
            "heartbeat: colector en fallo, incidencia abierta",
            extra={
                "source_id": str(source.id),
                "source_name": source.name,
                "consecutive_failures": source.consecutive_failures,
                "error": str(error),
            },
        )
        return True

    def _resolve_incidents(self, session: Session, source: Source, now: dt.datetime) -> None:
        open_incidents = session.scalars(
            select(PipelineIncident).where(
                PipelineIncident.source_id == source.id,
                PipelineIncident.kind == IncidentKind.COLLECTOR_FAILING.value,
                PipelineIncident.resolved_at.is_(None),
            )
        ).all()
        for incident in open_incidents:
            incident.resolved_at = now
            log.info(
                "heartbeat: colector recuperado, incidencia resuelta",
                extra={"source_id": str(source.id), "incident_id": str(incident.id)},
            )

    def _next_check(self, source: Source, now: dt.datetime) -> dt.datetime:
        try:
            return next_run(source.check_frequency, now)
        except InvalidCronError as error:
            # Con `next_check_at` nulo la fuente se ejecutaria en cada pasada del cron;
            # mejor un dia de espera y un error en el log.
            log.error(
                "cron invalido en la fuente; se reintenta en un dia",
                extra={"source_id": str(source.id), "error": str(error)},
            )
            return now + FALLBACK_INTERVAL

    @staticmethod
    def _remember_validators(source: Source, fetched: FetchResult) -> None:
        """Guarda `etag`/`last_modified` de esta respuesta para la peticion siguiente.

        Los de la respuesta anterior se quitan siempre: un servidor que deja de enviar
        `ETag` no debe seguir recibiendo `If-None-Match` con un valor de hace meses.
        """
        headers = {key.lower(): value for key, value in fetched.http_headers.items()}
        kept = headers_to_config(headers)
        config = {
            key: value
            for key, value in source.collector_config.items()
            if key not in VALIDATOR_KEYS
        }
        config.update(kept)
        if config != source.collector_config:
            source.collector_config = config

    # -- consultas ---------------------------------------------------------------

    @staticmethod
    def _get_source(session: Session, source_id: uuid.UUID) -> Source:
        source = session.get(Source, source_id)
        if source is None:
            raise UnknownSourceError(f"no hay fuente con id {source_id}")
        return source

    @staticmethod
    def _lock_source(session: Session, source_id: uuid.UUID, skip_locked: bool) -> Source | None:
        return session.scalar(
            select(Source).where(Source.id == source_id).with_for_update(skip_locked=skip_locked)
        )

    @staticmethod
    def _artifact_exists(session: Session, source_id: uuid.UUID, content_hash: str) -> bool:
        return (
            session.scalar(
                select(Artifact.id).where(
                    Artifact.source_id == source_id, Artifact.content_hash == content_hash
                )
            )
            is not None
        )

    @staticmethod
    def _latest_artifact(session: Session, source_id: uuid.UUID) -> Artifact | None:
        return session.scalar(
            select(Artifact)
            .where(Artifact.source_id == source_id)
            .order_by(Artifact.captured_at.desc(), Artifact.id.desc())
            .limit(1)
        )

    @staticmethod
    def _earlier_artifacts(
        session: Session, source_id: uuid.UUID, current: Artifact
    ) -> Sequence[Artifact]:
        """Los artefactos anteriores al recien insertado, del mas reciente al mas antiguo."""
        return session.scalars(
            select(Artifact)
            .where(Artifact.source_id == source_id, Artifact.id != current.id)
            .order_by(Artifact.captured_at.desc(), Artifact.id.desc())
            .limit(BASELINE_LOOKBACK + 1)
        ).all()


def _comparable(
    before: TypedForm | None, after: TypedForm | None
) -> tuple[StructuralDiff, str, str] | None:
    """Elige detector segun el tipo de las dos formas. `(diff, form_type, parser)`.

    Devuelve nulo si falta alguna de las dos o si no son del mismo tipo. Comparar un
    esquema con un indice no es un diff pobre: es un sinsentido, y sale mejor por la via
    de solo-hash, que al menos dice la verdad.
    """
    if isinstance(before, XsdForm) and isinstance(after, XsdForm):
        return compare_xsd(before, after), FormType.XSD_ELEMENTS.value, PARSER_VERSION
    if isinstance(before, IndexForm) and isinstance(after, IndexForm):
        return compare_index(before, after), FormType.INDEX_ENTRIES.value, INDEX_PARSER_VERSION
    if isinstance(before, TextForm) and isinstance(after, TextForm):
        return compare_text(before, after), FormType.TEXT_BLOCKS.value, TEXT_PARSER_VERSION
    return None


def _artifact_summary(artifact: Artifact) -> dict[str, Any]:
    return {
        "artifact_id": str(artifact.id),
        "content_hash": artifact.content_hash,
        "size_bytes": artifact.size_bytes,
        "mime_type": artifact.mime_type,
        "declared_version": artifact.declared_version,
        "captured_at": artifact.captured_at.isoformat(),
    }


def _max_severity(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    return max(current, candidate, key=lambda value: Severity(value).rank())
