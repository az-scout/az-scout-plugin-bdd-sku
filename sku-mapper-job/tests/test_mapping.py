"""Unit tests for the family→category mapping."""

import pytest

from sku_mapper_job.mapping import category_for_family


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("B", "burstable"),
        ("D", "general"),
        ("E", "memory"),
        ("F", "compute"),
        ("L", "storage"),
        ("M", "memory"),
        ("NC", "gpu"),
        ("ND", "gpu"),
        ("NV", "gpu"),
        ("NP", "gpu"),
        ("N", "gpu"),
        ("HB", "hpc"),
        ("HC", "hpc"),
        ("HX", "hpc"),
        ("H", "hpc"),
        ("DC", "general"),  # DC → D prefix → general
        ("EC", "memory"),  # EC → E prefix → memory
        ("X", "other"),
        ("Z", "other"),
        ("", "other"),
    ],
)
def test_category_for_family(family: str, expected: str) -> None:
    assert category_for_family(family) == expected
