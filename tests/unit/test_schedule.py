"""Calculo de `next_check_at` a partir del cron de la fuente."""

from __future__ import annotations

import datetime as dt

import pytest

from regwatch.ingest.schedule import InvalidCronError, next_run, validate_cron


def test_next_run_is_strictly_after_the_reference_instant() -> None:
    at_six = dt.datetime(2026, 9, 8, 6, 0, tzinfo=dt.UTC)
    assert next_run("0 6 * * *", at_six) == dt.datetime(2026, 9, 9, 6, 0, tzinfo=dt.UTC)


def test_next_run_keeps_the_timezone() -> None:
    madrid = dt.timezone(dt.timedelta(hours=2))
    result = next_run("*/15 * * * *", dt.datetime(2026, 9, 8, 10, 7, tzinfo=madrid))
    assert result == dt.datetime(2026, 9, 8, 10, 15, tzinfo=madrid)
    assert result.tzinfo is not None


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="zona horaria"):
        next_run("0 6 * * *", dt.datetime(2026, 9, 8, 6, 0))


@pytest.mark.parametrize("expression", ["", "cada dia", "0 6 * *", "99 6 * * *"])
def test_invalid_expressions_are_rejected(expression: str) -> None:
    with pytest.raises(InvalidCronError):
        validate_cron(expression)


def test_valid_expression_is_returned_unchanged() -> None:
    assert validate_cron("0 */6 * * 1-5") == "0 */6 * * 1-5"
