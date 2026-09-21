"""Proxima ejecucion de una fuente a partir de su expresion cron.

El cron no se puede evaluar en SQL, asi que `source.next_check_at` se materializa aqui
al terminar cada pasada y `collect run-due` solo tiene que comparar fechas.
"""

from __future__ import annotations

import datetime as dt

from croniter import croniter


class InvalidCronError(ValueError):
    """La expresion no es un cron valido. Se detecta al dar de alta la fuente."""


def validate_cron(expression: str) -> str:
    """Devuelve la expresion si es valida; si no, `InvalidCronError`."""
    if not croniter.is_valid(expression):
        raise InvalidCronError(f"expresion cron invalida: {expression!r}")
    return expression


def next_run(expression: str, after: dt.datetime) -> dt.datetime:
    """Primer instante posterior a `after` que cumple la expresion.

    `after` debe llevar zona horaria: las fuentes se comparan contra `now()` de
    Postgres, que es `timestamptz`, y mezclar fechas ingenuas con conscientes es la
    forma clasica de ejecutar un job a la hora equivocada.
    """
    if after.tzinfo is None:
        raise ValueError("`after` debe llevar zona horaria")
    validate_cron(expression)
    result: dt.datetime = croniter(expression, after).get_next(dt.datetime)
    return result
