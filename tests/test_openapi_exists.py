"""Smoke test: verify the OpenAPI spec file exists and is valid YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

OPENAPI_PATH = Path(__file__).resolve().parent.parent / "openapi" / "v1.yaml"


def test_openapi_file_exists() -> None:
    assert OPENAPI_PATH.is_file(), f"Expected OpenAPI spec at {OPENAPI_PATH}"


def test_openapi_is_valid_yaml() -> None:
    content = OPENAPI_PATH.read_text(encoding="utf-8")
    doc = yaml.safe_load(content)
    assert isinstance(doc, dict)
    assert doc.get("openapi", "").startswith("3.")
    assert "paths" in doc
    assert "info" in doc
