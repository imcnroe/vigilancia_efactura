"""Interfaz de linea de comandos de la fase 1.

Cada etapa del pipeline es invocable por separado sobre datos ya almacenados. Eso no
es comodidad: es el requisito de reprocesabilidad del apartado 7. Si el normalizador
solo se puede ejecutar como parte de una descarga, mejorar el parser obliga a volver a
descargarlo todo, y eso significa volver a molestar a la AEAT.

La planificacion la hace el cron del sistema llamando a `collect run-due`. No hay
proceso residente en la fase 1.

Codigos de salida: 0 bien, 1 algo fallo en la ejecucion (fuente en fallo, cambio
bloqueante en `diff`), 2 error de uso o de configuracion.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from regwatch.catalog.repository import (
    AmbiguousSourceError,
    UnknownDocumentTypeError,
    UnknownFamilyError,
    UnknownSourceError,
    create_source,
    find_document_type,
    find_family,
    find_source,
    list_sources,
)
from regwatch.core.enums import FormType
from regwatch.core.logging import configure_logging
from regwatch.core.settings import Settings
from regwatch.ingest.bootstrap import ConfigurationError, build_service, build_session_factory
from regwatch.ingest.collectors.base import CollectorError
from regwatch.ingest.diff.xsd_diff import compare_xsd
from regwatch.ingest.normalizers.xsd import normalize_xsd
from regwatch.ingest.schedule import InvalidCronError
from regwatch.ingest.service import IngestService, RunReport, RunStatus
from regwatch.ingest.storage.factory import StoreUnavailableError

app = typer.Typer(help="Vigilancia normativa de facturacion electronica", no_args_is_help=True)

source_app = typer.Typer(help="Catalogo de fuentes", no_args_is_help=True)
collect_app = typer.Typer(help="Ejecucion de colectores", no_args_is_help=True)
app.add_typer(source_app, name="source")
app.add_typer(collect_app, name="collect")

#: Los UUIDv7 comparten prefijo, asi que `source list` muestra la cola y
#: `find_source` acepta el fragmento en cualquier posicion.
REF_HELP = "Identificador de la fuente, entero o la `ref` que muestra `source list`"

#: Errores que se muestran al operador en una linea y terminan con codigo 2.
_USAGE_ERRORS: tuple[type[Exception], ...] = (
    ConfigurationError,
    StoreUnavailableError,
    UnknownSourceError,
    AmbiguousSourceError,
    UnknownFamilyError,
    UnknownDocumentTypeError,
    InvalidCronError,
    CollectorError,
    ValueError,
)


# -- utilidades ------------------------------------------------------------------


def _settings() -> Settings:
    settings = Settings()
    configure_logging(settings.log_level)
    return settings


def _fail(message: str, code: int = 2) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


@contextmanager
def _handling_errors() -> Iterator[None]:
    """Convierte los errores previsibles en un mensaje y un codigo de salida."""
    try:
        yield
    except OperationalError as error:
        _fail(f"no se pudo conectar a la base de datos: {error.orig}")
    except _USAGE_ERRORS as error:
        _fail(str(error))


@contextmanager
def _session(settings: Settings) -> Iterator[Session]:
    factory = build_session_factory(settings)
    with factory() as session, session.begin():
        yield session


def _service(settings: Settings) -> IngestService:
    return build_service(settings)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Tabla de texto plano, sin dependencias. Las columnas se ajustan al contenido."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    fmt = "  ".join(f"{{:<{width}}}" for width in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * width for width in widths))]
    lines.extend(fmt.format(*row) for row in rows)
    return "\n".join(lines)


def _when(value: Any) -> str:
    return "-" if value is None else value.strftime("%Y-%m-%d %H:%M")


def _short(value: uuid.UUID) -> str:
    """Fragmento corto y **discriminante** de un identificador.

    Se muestra la cola, no el prefijo: en un UUIDv7 los primeros 12 caracteres son el
    instante de creacion, asi que dos fuentes del mismo dia comparten prefijo y el
    operador que copiase lo que ve se llevaria un "coincide con varias fuentes".
    `find_source` busca el fragmento en cualquier posicion, de modo que lo que sale por
    pantalla sirve para volver a teclearlo.
    """
    return str(value)[-8:]


def _print_report(report: RunReport) -> None:
    colour = {
        RunStatus.NEW_ARTIFACT: typer.colors.GREEN,
        RunStatus.UNCHANGED: typer.colors.WHITE,
        RunStatus.NOT_MODIFIED: typer.colors.WHITE,
        RunStatus.SKIPPED: typer.colors.BLUE,
        RunStatus.FAILED: typer.colors.RED,
    }[report.status]
    line = f"{_short(report.source_id)}  {report.status.value:<13} {report.source_name}"
    if report.artifact_ids:
        line += f"  artefactos={len(report.artifact_ids)}"
    if report.change_event_ids:
        line += f"  cambios={len(report.change_event_ids)} severidad={report.max_severity}"
    if report.error:
        line += f"  {report.error}"
    if report.status is RunStatus.FAILED:
        line += f"  fallos_seguidos={report.consecutive_failures}"
    if report.incident_opened:
        line += "  INCIDENCIA ABIERTA"
    typer.secho(line, fg=colour)
    for warning in report.warnings:
        typer.secho(f"          aviso: {warning}", fg=typer.colors.YELLOW)


# -- normalize / diff sobre ficheros (sin base de datos) -------------------------


@app.command()
def normalize(
    path: Path = typer.Argument(..., help="XSD en disco"),
    dependency: list[Path] = typer.Option(
        [], "--dependency", "-d", help="XSD del que depende, repetible"
    ),
    output: Path | None = typer.Option(None, "--output", "-o", help="JSON de salida"),
) -> None:
    """Normaliza un XSD a la forma XSD_ELEMENTS y la vuelca como JSON."""
    dependencies = {item.name: item.read_bytes() for item in dependency}
    form = normalize_xsd(path.read_bytes(), dependencies)

    if form.is_partial:
        typer.secho(
            f"forma parcial: faltan {', '.join(form.missing_dependencies)}",
            fg=typer.colors.YELLOW,
            err=True,
        )

    payload = json.dumps(form.to_json(), indent=2, ensure_ascii=False)
    if output:
        output.write_text(payload, encoding="utf-8")
        typer.echo(f"{len(form.elements)} elementos -> {output}")
    else:
        typer.echo(payload)


@app.command()
def diff(
    before: Path = typer.Argument(..., help="XSD anterior"),
    after: Path = typer.Argument(..., help="XSD nuevo"),
    output: Path | None = typer.Option(None, "--output", "-o"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Solo el resumen"),
) -> None:
    """Compara dos XSD y clasifica los cambios.

    Sale con codigo 1 si hay algun cambio bloqueante, para poder encadenarlo en un
    script sin parsear la salida.
    """
    result = compare_xsd(normalize_xsd(before.read_bytes()), normalize_xsd(after.read_bytes()))

    if result.is_empty:
        typer.secho("sin cambios estructurales", fg=typer.colors.GREEN)
        raise typer.Exit(0)

    counts: dict[str, int] = {}
    for item in result.items:
        counts[item.change_type.value] = counts.get(item.change_type.value, 0) + 1

    typer.echo(f"{len(result.items)} cambios, severidad maxima {result.max_severity().value}")
    for change_type, count in sorted(counts.items()):
        typer.echo(f"  {count:>3}  {change_type}")

    if not quiet:
        typer.echo("")
        for item in result.items:
            colour = {
                "BLOCKING": typer.colors.RED,
                "REQUIRES_CHANGE": typer.colors.YELLOW,
                "INFO": typer.colors.WHITE,
            }[item.severity.value]
            typer.secho(
                f"  [{item.severity.value:<15}] {item.change_type.value:<22} {item.path}",
                fg=colour,
            )

    if output:
        output.write_text(
            json.dumps(result.to_json(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        typer.echo(f"\ndiff completo -> {output}")

    raise typer.Exit(1 if result.max_severity().value == "BLOCKING" else 0)


# -- catalogo de fuentes ---------------------------------------------------------


@source_app.command("list")
def source_list(
    include_inactive: bool = typer.Option(False, "--all", "-a", help="Incluye las inactivas"),
) -> None:
    """Lista las fuentes del catalogo con su estado de salud."""
    settings = _settings()
    with _handling_errors(), _session(settings) as session:
        sources = list_sources(session, include_inactive=include_inactive)
        if not sources:
            typer.echo("no hay fuentes" + ("" if include_inactive else " activas"))
            return
        rows = [
            [
                _short(source.id),
                f"{source.regulation_family.jurisdiction.code}/{source.regulation_family.code}",
                source.name,
                source.collector_type,
                source.priority,
                "si" if source.is_active else "no",
                _when(source.last_success_at),
                str(source.consecutive_failures),
                _when(source.next_check_at),
            ]
            for source in sources
        ]
        typer.echo(
            _table(
                [
                    "ref",
                    "familia",
                    "nombre",
                    "colector",
                    "prio",
                    "activa",
                    "ultimo ok",
                    "fallos",
                    "proxima",
                ],
                rows,
            )
        )


@source_app.command("show")
def source_show(ref: str = typer.Argument(..., help=REF_HELP)) -> None:
    """Detalle de una fuente: configuracion, ultimos artefactos e incidencias abiertas."""
    from sqlalchemy import select

    from regwatch.alerts.models import PipelineIncident
    from regwatch.changes.models import ChangeEvent
    from regwatch.ingest.models import Artifact

    settings = _settings()
    with _handling_errors(), _session(settings) as session:
        source = find_source(session, ref)
        family = source.regulation_family
        fields = [
            ("id", str(source.id)),
            ("nombre", source.name),
            ("familia", f"{family.jurisdiction.code}/{family.code}"),
            ("tipo documento", source.document_type.code if source.document_type else "-"),
            ("url", source.url),
            ("clase", source.source_kind),
            ("colector", source.collector_type),
            ("cron", source.check_frequency),
            ("prioridad", source.priority),
            ("activa", "si" if source.is_active else "no"),
            ("ultima comprobacion", _when(source.last_checked_at)),
            ("ultimo exito", _when(source.last_success_at)),
            ("fallos seguidos", str(source.consecutive_failures)),
            ("proxima", _when(source.next_check_at)),
            ("config", json.dumps(source.collector_config, ensure_ascii=False)),
            ("notas", source.notes or "-"),
        ]
        for label, value in fields:
            typer.echo(f"{label:<20} {value}")

        artifacts = session.scalars(
            select(Artifact)
            .where(Artifact.source_id == source.id)
            .order_by(Artifact.captured_at.desc())
            .limit(10)
        ).all()
        typer.echo(f"\nartefactos ({len(artifacts)} mas recientes):")
        for artifact in artifacts:
            typer.echo(
                f"  {_short(artifact.id)}  {_when(artifact.captured_at)}  "
                f"{artifact.size_bytes:>9} B  v={artifact.declared_version or '-':<10} "
                f"sha256:{artifact.content_hash[:16]}"
            )

        events = session.scalars(
            select(ChangeEvent)
            .where(ChangeEvent.source_id == source.id)
            .order_by(ChangeEvent.detected_at.desc())
            .limit(10)
        ).all()
        typer.echo(f"\ncambios ({len(events)} mas recientes):")
        for event in events:
            items = len(event.structural_diff.get("items", []))
            comparison = event.structural_diff.get("comparison", "?")
            typer.echo(
                f"  {_short(event.id)}  {_when(event.detected_at)}  {event.status:<12} "
                f"{event.severity_suggested:<15} {items} items  {comparison}"
            )

        incidents = session.scalars(
            select(PipelineIncident).where(
                PipelineIncident.source_id == source.id, PipelineIncident.resolved_at.is_(None)
            )
        ).all()
        if incidents:
            typer.secho(f"\nincidencias abiertas: {len(incidents)}", fg=typer.colors.RED)
            for incident in incidents:
                typer.echo(
                    f"  {incident.kind} desde {_when(incident.opened_at)}: {incident.detail}"
                )


@source_app.command("add")
def source_add(
    family: str = typer.Option(..., "--family", "-f", help="JURISDICCION/FAMILIA, ej. ES/FACTURAE"),
    name: str = typer.Option(..., "--name", "-n"),
    url: str = typer.Option(..., "--url", "-u"),
    kind: str = typer.Option("SCHEMA", "--kind", "-k", help="SCHEMA | INDEX | NARRATIVE"),
    collector: str = typer.Option("HTTP_FILE", "--collector", "-c"),
    cron: str = typer.Option("0 6 * * *", "--cron", help="Frecuencia de comprobacion"),
    priority: str = typer.Option("WARM", "--priority", "-p", help="HOT | WARM | COLD"),
    document_type: str | None = typer.Option(None, "--document-type", "-d"),
    notes: str | None = typer.Option(None, "--notes", help="Por que existe y que mirar"),
    inactive: bool = typer.Option(
        False, "--inactive", help="Alta sin activar (fuentes de nivel B y C del catalogo)"
    ),
    config: str | None = typer.Option(None, "--config", help="collector_config como JSON"),
) -> None:
    """Da de alta una fuente en el catalogo."""
    settings = _settings()
    with _handling_errors(), _session(settings) as session:
        collector_config: dict[str, Any] | None = None
        if config:
            parsed = json.loads(config)
            if not isinstance(parsed, dict):
                raise ValueError("--config debe ser un objeto JSON")
            collector_config = parsed

        regulation_family = find_family(session, family)
        document = (
            find_document_type(session, regulation_family, document_type) if document_type else None
        )
        source = create_source(
            session,
            family=regulation_family,
            name=name,
            url=url,
            source_kind=kind,
            collector_type=collector,
            check_frequency=cron,
            priority=priority,
            document_type=document,
            notes=notes,
            is_active=not inactive,
            collector_config=collector_config,
        )
        typer.secho(f"fuente creada: {source.id}", fg=typer.colors.GREEN)
        if inactive:
            typer.echo(
                "inactiva: activala con `regwatch source activate` cuando `source test` responda"
            )


def _set_active(ref: str, active: bool) -> None:
    settings = _settings()
    with _handling_errors(), _session(settings) as session:
        source = find_source(session, ref)
        source.is_active = active
        if active:
            # Vencida ya: la proxima pasada de run-due la recoge.
            source.next_check_at = None
        typer.echo(f"{source.id} {'activada' if active else 'desactivada'}")


@source_app.command("activate")
def source_activate(ref: str = typer.Argument(..., help=REF_HELP)) -> None:
    """Activa una fuente. Queda vencida para que la siguiente pasada la ejecute."""
    _set_active(ref, True)


@source_app.command("deactivate")
def source_deactivate(ref: str = typer.Argument(..., help=REF_HELP)) -> None:
    """Desactiva una fuente sin borrarla. Su historico se conserva."""
    _set_active(ref, False)


@source_app.command("test")
def source_test(ref: str = typer.Argument(..., help=REF_HELP)) -> None:
    """Ejecuta el colector de una fuente sin persistir nada.

    Es lo que usa el operador para ver que devuelve una configuracion antes de darla
    por buena, y lo que hay que pasar a las fuentes de nivel B y C del catalogo semilla.
    """
    settings = _settings()
    with _handling_errors():
        service = _service(settings)
        with _session(settings) as session:
            source_id = find_source(session, ref).id
        for report in service.preview(source_id):
            typer.echo(f"url descargada     {report.fetched_url}")
            typer.echo(f"http               {report.http_status}  {report.mime_type or '-'}")
            typer.echo(
                f"tamano             {report.size_bytes} B  fichero={report.filename or '-'}"
            )
            typer.echo(f"sha256             {report.content_hash}")
            same = {
                None: "sin artefactos previos",
                True: "igual al ultimo",
                False: "DISTINTO al ultimo",
            }
            typer.echo(f"frente al ultimo   {same[report.matches_latest_artifact]}")
            typer.echo(f"normalizacion      {report.normalization}")
            if report.element_count is not None:
                unit = (
                    "entradas " if report.normalization == FormType.INDEX_ENTRIES else "elementos"
                )
                typer.echo(
                    f"{unit}          {report.element_count}  "
                    f"version declarada={report.declared_version or '-'}  "
                    f"parcial={'si' if report.is_partial else 'no'}"
                )
            for missing in report.missing_dependencies:
                typer.secho(f"  dependencia sin resolver: {missing}", fg=typer.colors.YELLOW)
            for warning in report.warnings:
                typer.secho(f"  aviso: {warning}", fg=typer.colors.YELLOW)


# -- ejecucion de colectores -----------------------------------------------------


def _run_and_print(run: Callable[[], list[RunReport]], as_json: bool) -> None:
    reports = run()
    if as_json:
        typer.echo(
            json.dumps([report.to_json() for report in reports], indent=2, ensure_ascii=False)
        )
    else:
        if not reports:
            typer.echo("ninguna fuente vencida")
        for report in reports:
            _print_report(report)
    if any(report.status is RunStatus.FAILED for report in reports):
        raise typer.Exit(1)


@collect_app.command("run")
def collect_run(
    ref: str = typer.Argument(..., help=REF_HELP),
    as_json: bool = typer.Option(False, "--json", help="Informe en JSON"),
) -> None:
    """Ejecuta una fuente ahora, este vencida o no."""
    settings = _settings()
    with _handling_errors():
        service = _service(settings)
        with _session(settings) as session:
            source_id = find_source(session, ref).id
        _run_and_print(lambda: [service.run_source(source_id)], as_json)


@collect_app.command("run-due")
def collect_run_due(
    limit: int | None = typer.Option(None, "--limit", "-l", help="Maximo de fuentes"),
    as_json: bool = typer.Option(False, "--json", help="Informe en JSON"),
) -> None:
    """Ejecuta las fuentes cuyo `next_check_at` ya ha pasado. Lo llama el cron.

    Sale con codigo 1 si alguna fuente fallo, para que el cron pueda avisar.
    """
    settings = _settings()
    with _handling_errors():
        service = _service(settings)
        _run_and_print(lambda: service.run_due(limit=limit), as_json)


@collect_app.command("reprocess")
def collect_reprocess(ref: str = typer.Argument(..., help=REF_HELP)) -> None:
    """Vuelve a normalizar el historico de una fuente con el parser actual y recalcula
    los diffs. No descarga nada."""
    settings = _settings()
    with _handling_errors():
        service = _service(settings)
        with _session(settings) as session:
            source_id = find_source(session, ref).id
        report = service.reprocess_source(source_id)
        typer.echo(
            f"{report.source_name}: {report.artifacts} artefactos, "
            f"{report.forms_created} formas nuevas, "
            f"{report.events_created} cambios nuevos, {report.events_updated} recalculados, "
            f"{report.events_kept} respetados (ya revisados)"
        )
        for warning in report.warnings:
            typer.secho(f"  aviso: {warning}", fg=typer.colors.YELLOW)


def main() -> None:
    sys.exit(app())


if __name__ == "__main__":
    main()
