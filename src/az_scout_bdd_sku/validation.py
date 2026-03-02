"""Input validation helpers for the v1 API.

Centralises limit parsing, ISO datetime parsing, and enum validation
so that route handlers stay thin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

DEFAULT_LIMIT = 1000
MIN_LIMIT = 1
MAX_LIMIT = 5000


class Bucket(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"


class Agg(StrEnum):
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class Sample(StrEnum):
    RAW = "raw"
    HOURLY = "hourly"
    DAILY = "daily"


class ValidationError(ValueError):
    """Raised when user input does not pass validation."""


def parse_limit(value: int | None, *, default: int = DEFAULT_LIMIT) -> int:
    """Return a validated limit or *default*.

    Raises ``ValidationError`` for out-of-range values.
    """
    if value is None:
        return default
    if value < MIN_LIMIT or value > MAX_LIMIT:
        raise ValidationError(f"limit must be between {MIN_LIMIT} and {MAX_LIMIT}, got {value}")
    return value


def parse_iso_dt(value: str | None, *, param_name: str = "datetime") -> datetime | None:
    """Parse an ISO 8601 string to a timezone-aware ``datetime``.

    Returns ``None`` when *value* is ``None`` or empty.
    Raises ``ValidationError`` on malformed input.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"Invalid ISO datetime for '{param_name}': {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def validate_bucket(value: str) -> str:
    """Validate and return a bucket value (hour/day/week).

    Raises ``ValidationError`` on invalid input.
    """
    try:
        return Bucket(value.lower()).value
    except ValueError as exc:
        raise ValidationError(f"Invalid bucket '{value}', must be one of: hour, day, week") from exc


def validate_agg(value: str) -> str:
    """Validate and return an aggregation function name.

    Raises ``ValidationError`` on invalid input.
    """
    try:
        return Agg(value.lower()).value
    except ValueError as exc:
        raise ValidationError(f"Invalid agg '{value}', must be one of: avg, min, max") from exc


def validate_sample(value: str) -> str:
    """Validate and return a sample mode.

    Raises ``ValidationError`` on invalid input.
    """
    try:
        return Sample(value.lower()).value
    except ValueError as exc:
        raise ValidationError(
            f"Invalid sample '{value}', must be one of: raw, hourly, daily"
        ) from exc
