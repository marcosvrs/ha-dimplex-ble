from __future__ import annotations

import pytest

from custom_components.dimplex_ble.discovery import (
    advertisement_label,
    is_dimplex_advertisement,
)


def synthetic_local_name(prefix: tuple[int, int], suffix: str) -> str:
    """Build a fake local name without embedding a heater identifier."""
    return "".join(map(chr, prefix)) + suffix


@pytest.mark.parametrize(
    "name",
    [
        "heater<Dimplex>",
        "QRAD100E<Dimplex>",
        "<DIMPLEX>",
        synthetic_local_name((65, 76), "1234"),
        synthetic_local_name((65, 77), "9876"),
    ],
)
def test_recognizes_suffix_and_six_character_local_names(name: str) -> None:
    assert is_dimplex_advertisement(name) is True


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "heater",
        synthetic_local_name((65, 76), "123"),
        synthetic_local_name((65, 76), "12345"),
        synthetic_local_name((88, 88), "1234"),
        synthetic_local_name((65, 76), "12A4"),
        synthetic_local_name((65, 76), "A234"),
        "QRAD100E<Dimplex>extra",
    ],
)
def test_rejects_nonmatching_advertisements(name: str | None) -> None:
    assert is_dimplex_advertisement(name) is False


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("sample-heater-1<Dimplex>", "sample-heater-1"),
        (" QRAD100E <DIMPLEX> ", "QRAD100E"),
        ("sample-heater-2", "sample-heater-2"),
        ("<Dimplex>", None),
        (None, None),
        ("", None),
    ],
)
def test_advertisement_label_removes_only_the_dimplex_marker(
    name: str | None, expected: str | None
) -> None:
    assert advertisement_label(name) == expected
