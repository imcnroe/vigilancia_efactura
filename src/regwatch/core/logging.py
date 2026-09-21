"""Logs estructurados: una linea JSON por evento (apartado 3, observabilidad).

Los campos que se pasan en `extra=` van al JSON tal cual, de modo que
`log.info("nuevo artefacto", extra={"source_id": ...})` se puede filtrar sin expresiones
regulares sobre el texto.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from typing import IO, Any

#: Atributos que trae todo `LogRecord`. Lo que no este aqui viene de `extra=`.
_RESERVED: frozenset[str] = frozenset(
    set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}
)


class JsonFormatter(logging.Formatter):
    """Serializa el registro y sus campos extra como un objeto JSON en una linea."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", *, stream: IO[str] | None = None) -> None:
    """Deja un unico manejador JSON en el logger raiz.

    Idempotente a proposito: la CLI lo llama en cada comando y no debe duplicar salidas.
    """
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

    # Terceros ruidosos: solo interesan si fallan.
    for noisy in ("httpx", "httpcore", "botocore", "boto3", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
