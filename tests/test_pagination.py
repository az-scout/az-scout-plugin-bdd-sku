"""Unit tests for the keyset pagination module."""

from __future__ import annotations

import pytest

from az_scout_bdd_sku.pagination import (
    InvalidCursorError,
    build_page,
    decode_cursor,
    encode_cursor,
    keyset_clause,
)


class TestEncodeDecode:
    """Round-trip encode → decode tests."""

    def test_round_trip_simple(self) -> None:
        payload = {"name": "eastus"}
        cursor = encode_cursor(payload)
        assert isinstance(cursor, str)
        assert decode_cursor(cursor) == payload

    def test_round_trip_multi_key(self) -> None:
        payload = {"a": 1, "b": "hello", "c": True}
        assert decode_cursor(encode_cursor(payload)) == payload

    def test_round_trip_empty(self) -> None:
        assert decode_cursor(encode_cursor({})) == {}

    def test_cursor_is_url_safe(self) -> None:
        payload = {"key": "value/with+special=chars"}
        cursor = encode_cursor(payload)
        assert "+" not in cursor
        assert "/" not in cursor


class TestDecodeCursorInvalid:
    """Invalid cursor strings must raise ``InvalidCursorError``."""

    def test_garbage_string(self) -> None:
        with pytest.raises(InvalidCursorError):
            decode_cursor("not-base64-at-all!!!")

    def test_valid_base64_but_not_json(self) -> None:
        import base64

        raw = base64.urlsafe_b64encode(b"not json").decode()
        with pytest.raises(InvalidCursorError):
            decode_cursor(raw)

    def test_json_array_rejected(self) -> None:
        import base64

        raw = base64.urlsafe_b64encode(b"[1,2,3]").decode()
        with pytest.raises(InvalidCursorError):
            decode_cursor(raw)


class TestKeysetClause:
    """SQL keyset clause builder."""

    def test_single_column(self) -> None:
        sql, params = keyset_clause(["name"], {"name": "eastus"})
        assert sql == "(name) > (%s)"
        assert params == ["eastus"]

    def test_multi_column(self) -> None:
        sql, params = keyset_clause(
            ["a", "b", "c"],
            {"a": 1, "b": 2, "c": 3},
        )
        assert sql == "(a, b, c) > (%s, %s, %s)"
        assert params == [1, 2, 3]

    def test_missing_field_raises(self) -> None:
        with pytest.raises(InvalidCursorError, match="missing fields"):
            keyset_clause(["a", "b"], {"a": 1})

    def test_extra_fields_ignored(self) -> None:
        sql, params = keyset_clause(["a"], {"a": 1, "extra": 99})
        assert params == [1]


class TestBuildPage:
    """Page trimming and envelope construction."""

    def test_no_items(self) -> None:
        trimmed, page = build_page([], 10)
        assert trimmed == []
        assert page["hasMore"] is False
        assert page["cursor"] is None
        assert page["limit"] == 10

    def test_fewer_than_limit(self) -> None:
        items = [{"n": i} for i in range(5)]
        trimmed, page = build_page(items, 10)
        assert len(trimmed) == 5
        assert page["hasMore"] is False

    def test_exactly_limit(self) -> None:
        items = [{"n": i} for i in range(10)]
        trimmed, page = build_page(items, 10)
        assert len(trimmed) == 10
        assert page["hasMore"] is False

    def test_has_more_trims(self) -> None:
        # limit+1 items → hasMore=True, trimmed to limit
        items = [{"n": i} for i in range(11)]
        trimmed, page = build_page(items, 10)
        assert len(trimmed) == 10
        assert page["hasMore"] is True

    def test_cursor_from_builder(self) -> None:
        items = [{"n": i} for i in range(11)]
        trimmed, page = build_page(
            items,
            10,
            cursor_builder=lambda it: {"n": it["n"]},
        )
        assert page["cursor"] is not None
        decoded = decode_cursor(page["cursor"])
        assert decoded == {"n": 9}  # last item after trim

    def test_cursor_none_without_builder(self) -> None:
        items = [{"n": i} for i in range(11)]
        _, page = build_page(items, 10)
        assert page["cursor"] is None
