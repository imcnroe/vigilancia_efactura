"""Configuracion, composicion del almacen y logs JSON."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from regwatch.core.logging import JsonFormatter, configure_logging
from regwatch.core.settings import Settings
from regwatch.ingest.bootstrap import ConfigurationError, build_collectors
from regwatch.ingest.service import PRIORITY_ORDER, absolute_location
from regwatch.ingest.storage.artifact_store import LocalArtifactStore
from regwatch.ingest.storage.factory import build_store


def settings_from(**values: str) -> Settings:
    # `_env_file=None`: el `.env` del repositorio no debe colarse en los tests.
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_defaults_point_at_the_compose_services() -> None:
    settings = settings_from()
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.artifact_store == "s3"
    assert settings.heartbeat_failure_threshold == 2


def test_user_agent_with_contact_details_is_accepted() -> None:
    """Basta con una de las dos formas de contacto: URL o correo."""
    for agent in (
        "regwatch/0.1 (+https://example.org/bot; vigilancia@example.org)",
        "regwatch/0.1 (+https://example.org/sobre-nuestro-bot)",
        "regwatch/0.1 (vigilancia@example.org)",
    ):
        assert settings_from(collector_user_agent=agent).user_agent_problem() is None, agent


def test_user_agent_placeholder_is_rejected() -> None:
    problem = settings_from().user_agent_problem()
    assert problem is not None
    assert "TODO_VERIFICAR" in problem


def test_anonymous_user_agent_is_rejected_even_without_the_placeholder() -> None:
    """`regwatch/0.1` no lleva marca de ejemplo y sigue sin identificar a nadie: quien lo
    vea en los logs de un organismo no tiene a quien escribir."""
    for agent in ("regwatch/0.1", "regwatch", "  ", "Mozilla/5.0 (compatible)"):
        problem = settings_from(collector_user_agent=agent).user_agent_problem()
        assert problem is not None, agent
        assert "COLLECTOR_USER_AGENT" in problem


def test_anonymous_is_allowed_only_behind_the_explicit_development_flag() -> None:
    """La bandera existe para descargas manuales supervisadas. No sirve para saltarse el
    placeholder: `TODO_VERIFICAR` significa "sin rellenar", no "decidido que no hace
    falta"."""
    anonymous = settings_from(collector_user_agent="regwatch/0.1", collector_allow_anonymous=True)
    assert anonymous.user_agent_problem() is None
    assert anonymous.user_agent_is_anonymous()

    still_placeholder = settings_from(collector_allow_anonymous=True)
    problem = still_placeholder.user_agent_problem()
    assert problem is not None
    assert "TODO_VERIFICAR" in problem


def test_a_contactable_user_agent_is_not_reported_as_anonymous() -> None:
    settings = settings_from(collector_user_agent="regwatch/0.1 (buzon@example.org)")
    assert not settings.user_agent_is_anonymous()


def test_the_anonymous_flag_leaves_a_warning_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Una bandera que relaja una norma de cortesia no puede activarse en produccion sin
    que se note."""
    with caplog.at_level(logging.WARNING):
        build_collectors(
            settings_from(collector_user_agent="regwatch/0.1", collector_allow_anonymous=True)
        )
    assert any("COLLECTOR_ALLOW_ANONYMOUS" in record.getMessage() for record in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        build_collectors(settings_from(collector_user_agent="regwatch/0.1 (buzon@example.org)"))
    assert not caplog.records


def test_the_collector_refuses_to_start_without_a_contactable_user_agent() -> None:
    """La comprobacion tiene que estar en el camino de arranque, no solo disponible: la
    primera descarga contra un organismo publico ocurre una vez y no se deshace."""
    with pytest.raises(ConfigurationError, match="no identifica a nadie"):
        build_collectors(settings_from(collector_user_agent="regwatch/0.1"))

    registry = build_collectors(
        settings_from(collector_user_agent="regwatch/0.1 (+https://example.org/bot; a@example.org)")
    )
    assert registry.known_types() == ("HTTP_FILE", "HTTP_HTML_INDEX")


def test_environment_variables_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTIFACT_STORE", "local")
    monkeypatch.setenv("COLLECTOR_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("HEARTBEAT_FAILURE_THRESHOLD", "3")
    settings = settings_from()
    assert settings.artifact_store == "local"
    assert settings.collector_timeout_seconds == 7.5
    assert settings.heartbeat_failure_threshold == 3


def test_invalid_store_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        settings_from(artifact_store="ftp")


def test_local_store_is_built_from_settings(tmp_path: Path) -> None:
    store = build_store(settings_from(artifact_store="local", artifact_local_root=str(tmp_path)))
    assert isinstance(store, LocalArtifactStore)
    key = store.put(b"x")
    assert (tmp_path / key).is_file()


# -- utilidades del servicio -----------------------------------------------------


def test_relative_schema_location_is_resolved_against_the_document_url() -> None:
    base = "https://www.facturae.gob.es/content/dam/facturae/formato/versiones/Facturaev3_2_2.xml"
    assert (
        absolute_location(base, "xmldsig-core-schema.xsd")
        == "https://www.facturae.gob.es/content/dam/facturae/formato/versiones/xmldsig-core-schema.xsd"
    )
    absolute = "http://www.w3.org/TR/xmldsig-core/xmldsig-core-schema.xsd"
    assert absolute_location(base, absolute) == absolute


def test_hot_sources_run_first() -> None:
    assert sorted(["COLD", "HOT", "WARM"], key=lambda p: PRIORITY_ORDER[p]) == [
        "HOT",
        "WARM",
        "COLD",
    ]


# -- logs ------------------------------------------------------------------------


def test_json_formatter_includes_extra_fields() -> None:
    record = logging.LogRecord(
        "regwatch.test", logging.INFO, __file__, 1, "hola %s", ("mundo",), None
    )
    record.source_id = "abc"  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "hola mundo"
    assert payload["level"] == "INFO"
    assert payload["source_id"] == "abc"
    assert payload["ts"].endswith("+00:00")
    assert "args" not in payload


def test_configure_logging_is_idempotent_and_writes_one_line_per_event() -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    configure_logging("INFO", stream=stream)
    logging.getLogger("regwatch.test").info("evento", extra={"n": 1})

    lines = [line for line in stream.getvalue().splitlines() if line]
    assert len(lines) == 1
    assert json.loads(lines[0])["n"] == 1
