"""Keyset (cursor-based) pagination utilities.

Provides encode/decode for opaque cursors and SQL clause builders
for efficient keyset pagination over large PostgreSQL tables.
"""

from __future__ import annotations

import base64
import json
from typing import Any


class InvalidCursorError(ValueError):
    """Raised when a cursor string cannot be decoded or is malformed."""


def encode_cursor(payload: dict[str, Any]) -> str:
    """Encode a keyset payload as a URL-safe base64 opaque cursor string."""
    raw = json.dumps(payload, separators=(",", ":"), default=str)
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode an opaque cursor string back to a keyset payload dict.

    Raises ``InvalidCursorError`` if the string is not valid.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        data: dict[str, Any] = json.loads(raw)
        if not isinstance(data, dict):
            raise InvalidCursorError("Cursor payload must be a JSON object")
        return data
    except (json.JSONDecodeError, UnicodeDecodeError, Exception) as exc:
        raise InvalidCursorError(f"Invalid cursor: {exc}") from exc


def keyset_clause(
    columns: list[str],
    payload: dict[str, Any],
) -> tuple[str, list[Any]]:
    """Build a keyset ``WHERE`` clause for ascending sort.

    For columns ``(a, b, c)`` and payload ``{"a": 1, "b": 2, "c": 3}``
    produces ``(a, b, c) > (%s, %s, %s)`` with params ``[1, 2, 3]``.

    Returns a tuple of (sql_fragment, params).

    Raises ``InvalidCursorError`` if any expected column is missing.
    """
    missing = [c for c in columns if c not in payload]
    if missing:
        raise InvalidCursorError(f"Cursor missing fields: {', '.join(missing)}")

    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join(columns)
    sql = f"({col_list}) > ({placeholders})"
    params = [payload[c] for c in columns]
    return sql, params


def build_page(
    items: list[dict[str, Any]],
    limit: int,
    cursor_builder: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Trim ``limit+1`` rows to ``limit`` and build the page envelope.

    *cursor_builder* is a callable that takes the last item and returns
    the cursor payload dict.  If ``None``, ``cursor`` in page will be ``None``.

    Returns ``(trimmed_items, page_dict)``.
    """
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    cursor: str | None = None
    if items and cursor_builder is not None:
        cursor = encode_cursor(cursor_builder(items[-1]))

    page = {
        "limit": limit,
        "cursor": cursor,
        "hasMore": has_more,
    }
    return items, page
