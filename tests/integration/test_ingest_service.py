"""El servicio de ingesta contra Postgres real y un almacen en disco.

La red se sustituye por un servidor simulado cuyo contenido se puede cambiar entre
pasadas: es la forma de reproducir en segundos lo que en produccion tarda meses en
pasar (un organismo republica un XSD). El reloj tambien es simulado, para poder afirmar
cosas sobre `next_check_at` sin depender de la hora a la que corran los tests.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from regwatch.alerts.models import PipelineIncident
from regwatch.catalog.models import Jurisdiction, RegulationFamily, Source
from regwatch.catalog.repository import (
    AmbiguousSourceError,
    UnknownSourceError,
    create_source,
    find_source,
)
from regwatch.changes.models import ChangeEvent
from regwatch.core.enums import ChangeStatus, ChangeType, FormType, IncidentKind, Severity
from regwatch.ingest.collectors.base import CollectorRegistry
from regwatch.ingest.collectors.http_file import HTTPFileCollector
from regwatch.ingest.collectors.http_html_index import HTTPHtmlIndexCollector
from regwatch.ingest.models import Artifact, NormalizedForm
from regwatch.ingest.normalizers.xsd import PARSER_VERSION
from regwatch.ingest.service import HASH_ONLY_DIFF_VERSION, IngestService, RunStatus
from regwatch.ingest.storage.artifact_store import LocalArtifactStore

pytestmark = pytest.mark.integration

USER_AGENT = "regwatch-tests/0.1 (+https://example.org/bot; tests@example.org)"
URL = "https://schemas.example.org/invoice/invoice.xsd"
XML_HEADERS = {"content-type": "application/xml"}


def schema(body: str, version: str = "1.0") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           targetNamespace="http://example.org/invoice" xmlns="http://example.org/invoice"
           version="{version}">
  <xs:element name="Invoice">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="Number" type="xs:string"/>
{body}
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>""".encode()


XSD_V1 = schema("")
XSD_V2_MANDATORY = schema('        <xs:element name="TaxId" type="xs:string"/>', version="2.0")
XSD_V2_OPTIONAL = schema('        <xs:element name="Note" type="xs:string" minOccurs="0"/>', "1.1")
PDF = b"%PDF-1.7\n% esto no es un esquema\n"


class FakeServer:
    """Respuestas por URL, editables entre pasadas. Entiende `If-None-Match`."""

    def __init__(self) -> None:
        self.responses: dict[str, tuple[int, bytes, dict[str, str]]] = {}
        self.robots = "User-agent: *\nAllow: /\n"
        self.requests: list[httpx.Request] = []

    def set(
        self, url: str, content: bytes, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self.responses[url] = (status, content, dict(headers or XML_HEADERS))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        entry = self.responses.get(str(request.url))
        if entry is None:
            return httpx.Response(404)
        status, content, headers = entry
        etag = headers.get("etag")
        if etag and request.headers.get("if-none-match") == etag:
            return httpx.Response(304)
        return httpx.Response(status, content=content, headers=headers)


class FakeClock:
    def __init__(self) -> None:
        self.now = dt.datetime(2026, 9, 8, 6, 0, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **delta: int) -> None:
        self.now += dt.timedelta(**delta)


class FailingStore(LocalArtifactStore):
    """Simula S3 caido en mitad de una pasada."""

    def put(self, content: bytes, *, content_type: str | None = None) -> str:
        raise OSError("bucket inaccesible")


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path) -> LocalArtifactStore:
    return LocalArtifactStore(tmp_path / "artifacts")


def make_service(
    session_factory: sessionmaker[Session],
    server: FakeServer,
    store: LocalArtifactStore,
    clock: FakeClock,
) -> IngestService:
    client = httpx.Client(
        transport=httpx.MockTransport(server.handler),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    registry = CollectorRegistry()
    for collector_class in (HTTPFileCollector, HTTPHtmlIndexCollector):
        registry.register(collector_class(user_agent=USER_AGENT, client=client, max_attempts=1))
    return IngestService(session_factory, store, registry, heartbeat_threshold=2, clock=clock)


@pytest.fixture
def service(
    session_factory: sessionmaker[Session],
    server: FakeServer,
    store: LocalArtifactStore,
    clock: FakeClock,
) -> IngestService:
    return make_service(session_factory, server, store, clock)


# -- ayudas de base ------------------------------------------------------------


def seed_source(
    session_factory: sessionmaker[Session],
    url: str = URL,
    *,
    kind: str = "SCHEMA",
    cron: str = "0 6 * * *",
    priority: str = "WARM",
    active: bool = True,
    collector: str = "HTTP_FILE",
    config: dict[str, Any] | None = None,
) -> uuid.UUID:
    tag = uuid.uuid4().hex[:6]
    with session_factory() as session, session.begin():
        jurisdiction = Jurisdiction(code=f"T{tag}", name_i18n={"es": "Prueba"}, country_code="ES")
        session.add(jurisdiction)
        session.flush()
        family = RegulationFamily(
            jurisdiction_id=jurisdiction.id, code=f"F{tag}", name_i18n={"es": "Familia"}
        )
        session.add(family)
        session.flush()
        source = create_source(
            session,
            family=family,
            name=f"Fuente {tag}",
            url=url,
            source_kind=kind,
            collector_type=collector,
            check_frequency=cron,
            priority=priority,
            is_active=active,
            collector_config=config,
        )
        return source.id


def get_source(session_factory: sessionmaker[Session], source_id: uuid.UUID) -> Source:
    with session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        return source


def artifacts_of(session_factory: sessionmaker[Session], source_id: uuid.UUID) -> list[Artifact]:
    # El `id` desempata: el reloj de los tests esta congelado y varias filas comparten
    # instante. Los UUIDv7 son ordenables por tiempo de creacion, asi que el orden
    # coincide con el de insercion.
    with session_factory() as session:
        return list(
            session.scalars(
                select(Artifact)
                .where(Artifact.source_id == source_id)
                .order_by(Artifact.captured_at.asc(), Artifact.id.asc())
            ).all()
        )


def forms_of(
    session_factory: sessionmaker[Session], artifact_id: uuid.UUID
) -> list[NormalizedForm]:
    with session_factory() as session:
        return list(
            session.scalars(select(NormalizedForm).where(NormalizedForm.artifact_id == artifact_id))
        )


def events_of(session_factory: sessionmaker[Session], source_id: uuid.UUID) -> list[ChangeEvent]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(ChangeEvent)
                .where(ChangeEvent.source_id == source_id)
                .order_by(ChangeEvent.detected_at.asc(), ChangeEvent.id.asc())
            ).all()
        )


def incidents_of(
    session_factory: sessionmaker[Session], source_id: uuid.UUID
) -> list[PipelineIncident]:
    with session_factory() as session:
        return list(
            session.scalars(select(PipelineIncident).where(PipelineIncident.source_id == source_id))
        )


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# -- primera captura -------------------------------------------------------------


def test_first_capture_stores_artifact_and_form_without_change_event(
    service: IngestService,
    session_factory: sessionmaker[Session],
    server: FakeServer,
    store: LocalArtifactStore,
    clock: FakeClock,
) -> None:
    server.set(URL, XSD_V1, headers={**XML_HEADERS, "etag": '"v1"'})
    source_id = seed_source(session_factory)

    report = service.run_source(source_id)

    assert report.status is RunStatus.NEW_ARTIFACT
    assert len(report.artifact_ids) == 1
    assert report.change_event_ids == []
    assert report.warnings == []

    (artifact,) = artifacts_of(session_factory, source_id)
    assert artifact.content_hash == sha256(XSD_V1)
    assert store.get(artifact.storage_key) == XSD_V1
    assert artifact.declared_version == "1.0"
    assert artifact.fetched_url == URL
    assert artifact.original_filename == "invoice.xsd"
    assert artifact.mime_type == "application/xml"
    assert artifact.http_status == 200
    assert artifact.size_bytes == len(XSD_V1)

    (form,) = forms_of(session_factory, artifact.id)
    assert form.form_type == FormType.XSD_ELEMENTS
    assert form.parser_version == PARSER_VERSION
    assert not form.is_partial
    assert form.parse_warnings is None
    assert {item["path"] for item in form.payload["elements"]} == {"/Invoice", "/Invoice/Number"}

    source = get_source(session_factory, source_id)
    assert source.last_checked_at == clock.now
    assert source.last_success_at == clock.now
    assert source.consecutive_failures == 0
    assert source.next_check_at == dt.datetime(2026, 9, 9, 6, 0, tzinfo=dt.UTC)
    assert source.collector_config == {"etag": '"v1"'}
    assert events_of(session_factory, source_id) == []


def test_same_content_creates_nothing_but_touches_last_checked_at(
    service: IngestService,
    session_factory: sessionmaker[Session],
    server: FakeServer,
    clock: FakeClock,
) -> None:
    server.set(URL, XSD_V1)  # sin ETag: el servidor no sabe decir 304
    source_id = seed_source(session_factory)
    service.run_source(source_id)

    clock.advance(days=1)
    report = service.run_source(source_id)

    assert report.status is RunStatus.UNCHANGED
    assert report.artifact_ids == []
    assert len(artifacts_of(session_factory, source_id)) == 1
    source = get_source(session_factory, source_id)
    assert source.last_checked_at == clock.now
    assert source.last_success_at == clock.now


def test_conditional_request_yields_not_modified(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    server.set(URL, XSD_V1, headers={**XML_HEADERS, "etag": '"v1"'})
    source_id = seed_source(session_factory)
    service.run_source(source_id)

    report = service.run_source(source_id)

    assert report.status is RunStatus.NOT_MODIFIED
    assert server.requests[-1].headers["if-none-match"] == '"v1"'
    assert len(artifacts_of(session_factory, source_id)) == 1


# -- deteccion de cambios --------------------------------------------------------


def test_changed_schema_creates_artifact_and_blocking_change_event(
    service: IngestService,
    session_factory: sessionmaker[Session],
    server: FakeServer,
    clock: FakeClock,
) -> None:
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory)
    service.run_source(source_id)

    server.set(URL, XSD_V2_MANDATORY)
    clock.advance(days=1)
    report = service.run_source(source_id)

    assert report.status is RunStatus.NEW_ARTIFACT
    assert len(report.change_event_ids) == 1
    assert report.max_severity == Severity.BLOCKING

    first, second = artifacts_of(session_factory, source_id)
    assert second.declared_version == "2.0"
    (event,) = events_of(session_factory, source_id)
    assert event.artifact_from_id == first.id
    assert event.artifact_to_id == second.id
    assert event.status == ChangeStatus.DETECTED
    assert event.severity_suggested == Severity.BLOCKING
    assert event.detected_at == clock.now
    assert event.structural_diff["comparison"] == FormType.XSD_ELEMENTS
    assert event.structural_diff["parser_version"] == PARSER_VERSION
    (item,) = event.structural_diff["items"]
    assert item["type"] == ChangeType.FIELD_ADDED_MANDATORY
    assert item["path"] == "/Invoice/TaxId"


def test_cosmetic_change_creates_event_with_empty_diff_and_a_note(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    """Hash distinto pero estructura identica: se registra para que alguien lo descarte,
    no se traga en silencio."""
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory)
    service.run_source(source_id)

    server.set(URL, XSD_V1.replace(b"</xs:schema>", b"<!-- republicado -->\n</xs:schema>"))
    report = service.run_source(source_id)

    assert report.status is RunStatus.NEW_ARTIFACT
    (event,) = events_of(session_factory, source_id)
    assert event.severity_suggested == Severity.INFO
    assert event.structural_diff["items"] == []
    assert any("identica" in note for note in event.structural_diff["notes"])


def test_non_xsd_content_is_stored_and_flagged_as_hash_only_change(
    service: IngestService,
    session_factory: sessionmaker[Session],
    server: FakeServer,
    store: LocalArtifactStore,
) -> None:
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory)
    service.run_source(source_id)

    server.set(URL, PDF, headers={"content-type": "application/pdf"})
    report = service.run_source(source_id)

    assert report.status is RunStatus.NEW_ARTIFACT
    assert any("sin normalizar" in warning for warning in report.warnings)

    _, pdf_artifact = artifacts_of(session_factory, source_id)
    assert store.get(pdf_artifact.storage_key) == PDF
    assert pdf_artifact.declared_version is None
    assert forms_of(session_factory, pdf_artifact.id) == []

    (event,) = events_of(session_factory, source_id)
    assert event.diff_schema_version == HASH_ONLY_DIFF_VERSION
    assert event.severity_suggested == Severity.INFO
    assert event.structural_diff["comparison"] == "CONTENT_HASH_ONLY"
    assert event.structural_diff["items"] == []
    assert event.structural_diff["after"]["content_hash"] == sha256(PDF)
    assert any("pdf" in note for note in event.structural_diff["notes"])


def test_dependency_is_resolved_from_another_captured_source(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    """Basta con dar de alta la dependencia como fuente propia para que se resuelva."""
    dependency_url = "https://schemas.example.org/invoice/xmldsig-core-schema.xsd"
    dependency = (
        b'<?xml version="1.0"?><xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        b'targetNamespace="http://www.w3.org/2000/09/xmldsig#">'
        b'<xs:element name="Signature" type="xs:string"/></xs:schema>'
    )
    importing = b"""<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
           targetNamespace="http://example.org/i" xmlns="http://example.org/i" version="1">
  <xs:import namespace="http://www.w3.org/2000/09/xmldsig#" schemaLocation="xmldsig-core-schema.xsd"/>
  <xs:element name="Invoice" type="xs:string"/>
</xs:schema>"""
    server.set(URL, importing)
    server.set(dependency_url, dependency)
    main_id = seed_source(session_factory)
    dependency_id = seed_source(session_factory, dependency_url, priority="COLD")

    # Sin la dependencia capturada, la forma sale parcial.
    service.run_source(main_id)
    (first,) = artifacts_of(session_factory, main_id)
    (partial,) = forms_of(session_factory, first.id)
    assert partial.is_partial
    assert partial.parse_warnings == {
        "warnings": [],
        "missing_dependencies": ["xmldsig-core-schema.xsd"],
    }

    # Con ella en el historico, la siguiente captura se resuelve entera.
    service.run_source(dependency_id)
    server.set(URL, importing.replace(b'version="1"', b'version="2"'))
    service.run_source(main_id)

    _, second = artifacts_of(session_factory, main_id)
    (complete,) = forms_of(session_factory, second.id)
    assert not complete.is_partial
    assert complete.parse_warnings is None


# -- heartbeat -------------------------------------------------------------------


def test_repeated_failures_open_one_incident_and_success_resolves_it(
    service: IngestService,
    session_factory: sessionmaker[Session],
    server: FakeServer,
    clock: FakeClock,
) -> None:
    server.set(URL, b"caido", status=500)
    source_id = seed_source(session_factory)

    first = service.run_source(source_id)
    assert first.status is RunStatus.FAILED
    assert first.consecutive_failures == 1
    assert not first.incident_opened
    assert "500" in (first.error or "")
    assert incidents_of(session_factory, source_id) == []
    source = get_source(session_factory, source_id)
    assert source.last_checked_at == clock.now
    assert source.last_success_at is None
    assert source.next_check_at == dt.datetime(2026, 9, 9, 6, 0, tzinfo=dt.UTC)

    clock.advance(days=1)
    second = service.run_source(source_id)
    assert second.consecutive_failures == 2
    assert second.incident_opened
    (incident,) = incidents_of(session_factory, source_id)
    assert incident.kind == IncidentKind.COLLECTOR_FAILING
    assert incident.resolved_at is None
    assert incident.detail["consecutive_failures"] == 2

    clock.advance(days=1)
    third = service.run_source(source_id)
    assert third.consecutive_failures == 3
    assert not third.incident_opened  # se actualiza la abierta, no se abre otra
    (incident,) = incidents_of(session_factory, source_id)
    assert incident.detail["consecutive_failures"] == 3

    server.set(URL, XSD_V1)
    clock.advance(days=1)
    recovered = service.run_source(source_id)
    assert recovered.status is RunStatus.NEW_ARTIFACT
    assert get_source(session_factory, source_id).consecutive_failures == 0
    (incident,) = incidents_of(session_factory, source_id)
    assert incident.resolved_at == clock.now


def test_robots_disallow_counts_as_failure(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    server.robots = "User-agent: *\nDisallow: /\n"
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory)

    report = service.run_source(source_id)

    assert report.status is RunStatus.FAILED
    assert "robots.txt" in (report.error or "")
    assert artifacts_of(session_factory, source_id) == []


def test_infrastructure_error_is_recorded_as_failure_and_rolls_back(
    session_factory: sessionmaker[Session],
    server: FakeServer,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory)
    service = make_service(session_factory, server, FailingStore(tmp_path / "broken"), clock)

    report = service.run_source(source_id)

    assert report.status is RunStatus.FAILED
    assert "bucket inaccesible" in (report.error or "")
    assert artifacts_of(session_factory, source_id) == []
    source = get_source(session_factory, source_id)
    assert source.consecutive_failures == 1
    assert source.last_checked_at == clock.now


# -- planificacion ---------------------------------------------------------------


def _deactivate_all_but(session_factory: sessionmaker[Session], keep: set[uuid.UUID]) -> None:
    """La base es compartida por toda la sesion de tests; `run_due` solo debe ver lo
    que este test dio de alta."""
    with session_factory() as session, session.begin():
        session.execute(
            text("UPDATE source SET is_active = false WHERE NOT (id = ANY(:keep))"),
            {"keep": list(keep)},
        )


def test_run_due_runs_due_sources_hot_first_and_isolates_failures(
    service: IngestService,
    session_factory: sessionmaker[Session],
    server: FakeServer,
    clock: FakeClock,
) -> None:
    hot_url = "https://a.example.org/hot.xsd"
    warm_url = "https://b.example.org/warm.xsd"
    cold_url = "https://c.example.org/cold.xsd"
    server.set(hot_url, b"caido", status=500)
    server.set(warm_url, XSD_V1)
    server.set(cold_url, XSD_V1)

    hot = seed_source(session_factory, hot_url, priority="HOT")
    warm = seed_source(session_factory, warm_url, priority="WARM")
    cold = seed_source(session_factory, cold_url, priority="COLD")
    _deactivate_all_but(session_factory, {hot, warm, cold})
    with session_factory() as session, session.begin():
        session.execute(
            text("UPDATE source SET next_check_at = :later WHERE id = :id"),
            {"later": clock.now + dt.timedelta(hours=1), "id": cold},
        )

    reports = service.run_due()

    assert [(report.source_id, report.status) for report in reports] == [
        (hot, RunStatus.FAILED),
        (warm, RunStatus.NEW_ARTIFACT),
    ]
    assert get_source(session_factory, cold).last_checked_at is None

    # Ya no hay nada vencido: las dos ejecutadas tienen next_check_at manana.
    assert service.run_due() == []

    clock.advance(hours=1)
    assert [report.source_id for report in service.run_due()] == [cold]

    clock.advance(days=1)
    assert [report.source_id for report in service.run_due(limit=1)] == [hot]


def test_inactive_source_is_skipped_and_unknown_source_raises(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory, active=False)

    report = service.run_source(source_id)
    assert report.status is RunStatus.SKIPPED
    assert get_source(session_factory, source_id).last_checked_at is None

    with pytest.raises(UnknownSourceError):
        service.run_source(uuid.uuid4())


# -- previsualizacion y reproceso ------------------------------------------------


def test_preview_reports_without_writing_and_without_validators(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    server.set(URL, XSD_V1, headers={**XML_HEADERS, "etag": '"v1"'})
    source_id = seed_source(session_factory)

    (before,) = service.preview(source_id)
    assert before.matches_latest_artifact is None
    assert before.normalization == FormType.XSD_ELEMENTS
    assert before.element_count == 2
    assert before.declared_version == "1.0"
    assert artifacts_of(session_factory, source_id) == []
    assert get_source(session_factory, source_id).last_checked_at is None

    service.run_source(source_id)
    server.set(URL, XSD_V2_OPTIONAL, headers={**XML_HEADERS, "etag": '"v2"'})
    (after,) = service.preview(source_id)
    assert after.matches_latest_artifact is False
    assert "if-none-match" not in server.requests[-1].headers
    assert len(artifacts_of(session_factory, source_id)) == 1


def test_reprocess_backfills_forms_and_recalculates_unreviewed_events_only(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory)
    service.run_source(source_id)
    server.set(URL, XSD_V2_OPTIONAL)
    service.run_source(source_id)

    # Simula un historico normalizado con un parser antiguo.
    with session_factory() as session, session.begin():
        session.execute(
            text(
                "UPDATE normalized_form SET parser_version = 'xsd/0' "
                "WHERE artifact_id IN (SELECT id FROM artifact WHERE source_id = :sid)"
            ),
            {"sid": source_id},
        )

    report = service.reprocess_source(source_id)
    assert (report.artifacts, report.forms_created) == (2, 2)
    assert (report.events_created, report.events_updated, report.events_kept) == (0, 1, 0)
    with session_factory() as session:
        forms = session.scalar(
            select(func.count())
            .select_from(NormalizedForm)
            .join(Artifact, NormalizedForm.artifact_id == Artifact.id)
            .where(Artifact.source_id == source_id)
        )
    assert forms == 4  # dos parsers por artefacto: el historico antiguo se conserva

    (event,) = events_of(session_factory, source_id)
    assert event.severity_suggested == Severity.INFO
    assert event.structural_diff["items"][0]["type"] == ChangeType.FIELD_ADDED_OPTIONAL

    # Una vez revisado por una persona, el reproceso no lo toca.
    with session_factory() as session, session.begin():
        session.execute(
            text("UPDATE change_event SET status = 'UNDER_REVIEW' WHERE id = :id"),
            {"id": event.id},
        )
    again = service.reprocess_source(source_id)
    assert (again.forms_created, again.events_created, again.events_updated) == (0, 0, 0)
    assert again.events_kept == 1
    assert len(events_of(session_factory, source_id)) == 1


def test_reprocess_creates_missing_events_between_consecutive_artifacts(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory)
    service.run_source(source_id)
    server.set(URL, XSD_V2_MANDATORY)
    service.run_source(source_id)

    with session_factory() as session, session.begin():
        session.execute(text("DELETE FROM change_event WHERE source_id = :sid"), {"sid": source_id})

    report = service.reprocess_source(source_id)
    assert report.events_created == 1
    (event,) = events_of(session_factory, source_id)
    assert event.severity_suggested == Severity.BLOCKING


# -- linea base de comparacion ---------------------------------------------------


def test_baseline_skips_unparseable_artifacts_in_the_middle(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    """Un portal que sirve una pagina de mantenimiento con 200 no debe hacer que el
    siguiente XSD se compare contra basura: el cambio estructural se perderia."""
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory)
    service.run_source(source_id)

    server.set(
        URL,
        b"<html><body>Portal en mantenimiento</body></html>",
        headers={"content-type": "text/html"},
    )
    middle = service.run_source(source_id)
    assert middle.status is RunStatus.NEW_ARTIFACT
    assert any("sin normalizar" in warning for warning in middle.warnings)

    server.set(URL, XSD_V2_MANDATORY)
    report = service.run_source(source_id)

    assert report.max_severity == Severity.BLOCKING
    first, html, third = artifacts_of(session_factory, source_id)
    events = events_of(session_factory, source_id)
    assert len(events) == 2

    # El HTML se comparo con el XSD anterior: solo hash, sin estructura.
    assert events[0].artifact_to_id == html.id
    assert events[0].diff_schema_version == HASH_ONLY_DIFF_VERSION

    # El XSD nuevo se comparo con el XSD viejo, saltandose el HTML de en medio.
    latest = events[1]
    assert latest.artifact_to_id == third.id
    assert latest.artifact_from_id == first.id
    assert latest.structural_diff["comparison"] == FormType.XSD_ELEMENTS
    assert latest.structural_diff["skipped_artifact_ids"] == [str(html.id)]
    assert any(
        "intermedios no eran normalizables" in note for note in latest.structural_diff["notes"]
    )
    (item,) = latest.structural_diff["items"]
    assert item["type"] == ChangeType.FIELD_ADDED_MANDATORY
    assert item["path"] == "/Invoice/TaxId"


def test_reprocess_keeps_one_event_per_artifact_and_the_right_baseline(
    service: IngestService, session_factory: sessionmaker[Session], server: FakeServer
) -> None:
    """Reprocesar un historico con un artefacto ilegible en medio no duplica eventos ni
    mueve la linea base: cada artefacto conserva su unico evento, y el del XSD nuevo
    sigue apuntando al XSD viejo.

    El evento se busca por `artifact_to_id` justamente para esto: si un parser mejorado
    convirtiese el artefacto de en medio en normalizable, la linea base cambiaria y
    buscar por el par completo crearia un evento nuevo en vez de corregir el que hay.
    """
    server.set(URL, XSD_V1)
    source_id = seed_source(session_factory)
    service.run_source(source_id)
    server.set(URL, PDF, headers={"content-type": "application/pdf"})
    service.run_source(source_id)
    server.set(URL, XSD_V2_MANDATORY)
    service.run_source(source_id)

    first, _pdf, third = artifacts_of(session_factory, source_id)
    events = events_of(session_factory, source_id)
    assert events[1].artifact_from_id == first.id

    report = service.reprocess_source(source_id)
    assert report.artifacts == 3
    assert report.events_updated == 2
    assert report.events_created == 0
    assert len(events_of(session_factory, source_id)) == 2

    latest = events_of(session_factory, source_id)[1]
    assert latest.artifact_to_id == third.id
    assert latest.artifact_from_id == first.id
    assert latest.severity_suggested == Severity.BLOCKING


# -- referencias de fuentes ------------------------------------------------------


def test_sources_are_found_by_the_random_tail_not_the_shared_prefix(
    session_factory: sessionmaker[Session],
) -> None:
    """Dos fuentes creadas seguidas comparten casi todo el prefijo del UUIDv7, porque
    esos primeros 12 caracteres hexadecimales son el instante de creacion. Buscar por
    prefijo corto no sirve; por la cola aleatoria si."""
    first = seed_source(session_factory, "https://a.example.org/1.xsd")
    second = seed_source(session_factory, "https://b.example.org/2.xsd")

    assert str(first)[:8] == str(second)[:8], "el prefijo deberia colisionar: son UUIDv7"

    with session_factory() as session:
        assert find_source(session, str(first)[-8:]).id == first
        assert find_source(session, str(second)[-8:]).id == second
        assert find_source(session, first).id == first
        assert find_source(session, str(first)).id == first

        with pytest.raises(AmbiguousSourceError, match="varias fuentes"):
            find_source(session, str(first)[:8])
        with pytest.raises(UnknownSourceError, match="demasiado corto"):
            find_source(session, "ab")
        with pytest.raises(UnknownSourceError, match="ninguna fuente"):
            find_source(session, "zzzzzzzz")


# -- indices de publicaciones ----------------------------------------------------


INDEX_URL = "https://portal.example.org/formato/ultima-version"
INDEX_CONFIG = {"url_pattern": r"/versiones/.*\.xml$", "version_pattern": r"v(\d+_\d+)"}
HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}


def index_page(links: str, banner: str = "Aviso de cookies") -> bytes:
    return f"""<!DOCTYPE html>
<html><body>
  <div id="cookies">{banner}</div>
  <nav><a href="/">Inicio</a></nav>
  <ul>{links}</ul>
</body></html>""".encode()


ESQUEMA_31 = '<li><a href="/versiones/Esquemav3_1.xml">Version 3.1</a></li>'
ESQUEMA_32 = '<li><a href="/versiones/Esquemav3_2.xml">Version 3.2</a></li>'


def test_a_new_link_in_an_index_becomes_a_change_event(
    service: IngestService,
    session_factory: sessionmaker[Session],
    server: FakeServer,
    clock: FakeClock,
) -> None:
    """La fuente de indice guarda la pagina; lo que se compara es su lista de entradas.

    Una publicacion nueva sale nombrada con su URL, que es lo que el operador necesita
    para decidir si le da de alta una `source` propia.
    """
    server.set(INDEX_URL, index_page(ESQUEMA_31), headers=HTML_HEADERS)
    source_id = seed_source(
        session_factory,
        INDEX_URL,
        kind="INDEX",
        collector="HTTP_HTML_INDEX",
        config=INDEX_CONFIG,
    )
    service.run_source(source_id)

    server.set(INDEX_URL, index_page(ESQUEMA_31 + ESQUEMA_32), headers=HTML_HEADERS)
    clock.advance(days=1)
    report = service.run_source(source_id)

    assert report.status is RunStatus.NEW_ARTIFACT
    assert report.max_severity == Severity.REQUIRES_CHANGE

    # Un solo artefacto por pasada: la pagina. Lo que enlaza no se descarga.
    assert len(artifacts_of(session_factory, source_id)) == 2
    _, page = artifacts_of(session_factory, source_id)
    (form,) = forms_of(session_factory, page.id)
    assert form.form_type == FormType.INDEX_ENTRIES
    assert len(form.payload["entries"]) == 2

    (event,) = events_of(session_factory, source_id)
    assert event.structural_diff["comparison"] == FormType.INDEX_ENTRIES
    (item,) = event.structural_diff["items"]
    assert item["type"] == ChangeType.INDEX_ENTRY_ADDED
    assert item["path"] == "https://portal.example.org/versiones/Esquemav3_2.xml"
    assert item["detail"]["version"] == "3_2"


def test_a_cosmetic_edit_of_the_index_is_not_a_publication(
    service: IngestService,
    session_factory: sessionmaker[Session],
    server: FakeServer,
    clock: FakeClock,
) -> None:
    """El hash cambia todos los dias por el banner de turno. Si eso llegase como alarma,
    el operador dejaria de mirarlas, que es como se pierde un cambio de verdad."""
    server.set(INDEX_URL, index_page(ESQUEMA_31), headers=HTML_HEADERS)
    source_id = seed_source(
        session_factory,
        INDEX_URL,
        kind="INDEX",
        collector="HTTP_HTML_INDEX",
        config=INDEX_CONFIG,
    )
    service.run_source(source_id)

    server.set(
        INDEX_URL,
        index_page(ESQUEMA_31, banner="Usamos cookies propias y de terceros"),
        headers=HTML_HEADERS,
    )
    clock.advance(days=1)
    report = service.run_source(source_id)

    # El artefacto si se guarda: el contenido cambio y nunca se tira nada.
    assert len(artifacts_of(session_factory, source_id)) == 2
    (event,) = events_of(session_factory, source_id)
    assert event.severity_suggested == Severity.INFO
    assert event.structural_diff["items"] == []
    assert any("identica" in note for note in event.structural_diff["notes"])
    assert report.max_severity == Severity.INFO


# -- la fase 1 de punta a punta, con los bytes reales del organismo ---------------


FACTURAE_URL = "https://www.facturae.gob.es/content/dam/facturae/formato/versiones/Facturae.xml"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "xsd"


def real_xsd(name: str) -> bytes:
    """Un fixture descargado, verificado contra el manifiesto antes de usarlo.

    Si el hash no coincide, el fichero local no es el que se congelo y cualquier
    conclusion que saquemos de el no vale nada.
    """
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"falta {name}: ejecuta `python scripts/fetch_fixtures.py`")
    content = path.read_bytes()
    expected = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8")).get(name)
    assert sha256(content) == expected, f"{name} no coincide con el manifiesto"
    return content


def test_real_facturae_republication_produces_the_change_event_end_to_end(
    service: IngestService,
    session_factory: sessionmaker[Session],
    server: FakeServer,
    store: LocalArtifactStore,
    clock: FakeClock,
) -> None:
    """El criterio de la fase 1, con contenido verdadero y toda la maquinaria puesta.

    Los tests de arriba usan esquemas de juguete de diez lineas; `test_facturae_real.py`
    contrasta el detector contra el listado oficial pero sin base de datos ni almacen.
    Esto es lo unico que ejerce las cinco etapas del apartado 7 —descarga, almacen,
    normalizacion, diff, evento— sobre los 190 KB que publica el organismo.

    El suceso simulado es el que el producto existe para ver: una fuente que servia
    3.2.1 pasa a servir 3.2.2. En produccion tarda meses; aqui, dos pasadas.
    """
    v321, v322 = real_xsd("facturae_3_2_1.xsd"), real_xsd("facturae_3_2_2.xsd")

    server.set(FACTURAE_URL, v321)
    source_id = seed_source(session_factory, FACTURAE_URL)
    service.run_source(source_id)

    server.set(FACTURAE_URL, v322)
    clock.advance(days=1)
    report = service.run_source(source_id)

    assert report.status is RunStatus.NEW_ARTIFACT

    # El artefacto se guarda entero y byte a byte: es el activo del producto.
    first, second = artifacts_of(session_factory, source_id)
    assert store.get(second.storage_key) == v322
    assert (first.declared_version, second.declared_version) == ("3.2.1", "3.2.2")

    # La forma normalizada es parcial por la firma XML-DSig de la W3C, que no se
    # descarga. Es el comportamiento correcto, no un fallo, y el aviso lo dice.
    (form,) = forms_of(session_factory, second.id)
    assert form.form_type == FormType.XSD_ELEMENTS
    assert len(form.payload["elements"]) > 600
    assert form.is_partial
    assert any("forma parcial" in warning for warning in report.warnings)

    (event,) = events_of(session_factory, source_id)
    assert event.artifact_from_id == first.id
    assert event.artifact_to_id == second.id
    assert event.status == ChangeStatus.DETECTED
    assert event.structural_diff["comparison"] == FormType.XSD_ELEMENTS

    # Los cinco cambios que publica el organismo, tal y como llegan a la base.
    paths = {item["path"] for item in event.structural_diff["items"]}
    assert "/Facturae/Invoices/Invoice/InvoiceHeader/Corrective/InvoiceIssueDate" in paths
    assert "/Facturae/Invoices/Invoice/InvoiceIssueData/InvoiceDescription" in paths
    assert "/Facturae/Invoices/Invoice/InvoiceTotals/PaymentInKind" in paths
    assert any("FactoringAssignmentDocument" in path for path in paths)
    assert "/Facturae/Invoices/Invoice/Items/InvoiceLine/UnitOfMeasure" in paths

    # Y la clasificacion sobrevive al viaje por la base: lo unico bloqueante es
    # `SchemaVersion`, que deja de admitir la version vieja. Los campos obligatorios de
    # las ramas nuevas y opcionales no obligan a nadie, y salen como INFO.
    blocking = [
        item for item in event.structural_diff["items"] if item["severity"] == Severity.BLOCKING
    ]
    assert [item["path"] for item in blocking] == ["/Facturae/FileHeader/SchemaVersion"]
    assert report.max_severity == Severity.BLOCKING
