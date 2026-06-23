"""Unit tests for the design technique taxonomy foundation."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from domain.design.technique_taxonomy import (
    CATEGORY_TECHNIQUE_FAMILIES,
    FAMILY_DISPLAY_NAMES,
    resolve_family,
    resolve_sub_technique,
)


def _finding(label: str, technique_family: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(label=label, technique_family=technique_family)


def test_resolve_family_preserves_valid_stored_value():
    assert resolve_family(_finding("JWT confusion", "auth"), category="web") == "auth"


def test_resolve_family_coerces_unknown_stored_value(caplog):
    with caplog.at_level("WARNING"):
        family = resolve_family(_finding("JWT confusion", "made-up"), category="web")

    assert family == "other"
    assert "unknown technique_family" in caplog.text


def test_resolve_family_derives_from_label_when_stored_value_missing():
    assert resolve_family(_finding("blind SQLi"), category="web") == "injection"
    assert resolve_family(_finding("second-order SQLi"), category="web") == "injection"
    assert resolve_family(_finding("SQLi login bypass"), category="web") == "injection"


def test_resolve_family_handles_layered_encoding_stably():
    assert resolve_family(_finding("layered-encoding transform"), category="re") == "crackme"


def test_resolve_sub_technique_collapses_xor_surface_variants():
    keys = {
        resolve_sub_technique(_finding(label))
        for label in ("xor", "XOR", "xor-decrypt", "xor decrypt")
    }
    assert keys == {"xor"}


def test_resolve_sub_technique_keeps_sqli_variants_distinct():
    assert resolve_sub_technique(_finding("blind SQLi")) != resolve_sub_technique(
        _finding("second-order SQLi")
    )
    assert resolve_sub_technique(_finding("blind SQLi")) != resolve_sub_technique(
        _finding("SQLi login bypass")
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("base64", "base32"),
        ("xor", "rc4"),
        ("sqli", "ssti"),
        ("ret2win", "ret2libc"),
        ("tcache poisoning", "UAF"),
    ],
)
def test_alias_map_conservatism_guard(left: str, right: str):
    assert resolve_sub_technique(_finding(left)) != resolve_sub_technique(_finding(right))


def test_category_tactics_lane_list_matches_taxonomy_constants():
    doc = _category_tactics_text()
    expected_by_heading = {
        "Web": _display_lanes("web"),
        "Pwn": _display_lanes("pwn"),
        "Reverse": _display_lanes("re"),
    }
    for heading, expected_lanes in expected_by_heading.items():
        assert _doc_lanes(doc, heading) == expected_lanes


def _display_lanes(category: str) -> list[str]:
    display_names = FAMILY_DISPLAY_NAMES[category]
    return [display_names[family] for family in CATEGORY_TECHNIQUE_FAMILIES[category]]


def _category_tactics_text() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "design-challenges"
        / "references"
        / "category-tactics.md"
    ).read_text(encoding="utf-8")


def _doc_lanes(doc: str, heading: str) -> list[str]:
    match = re.search(rf"^## {re.escape(heading)}\n(?P<section>.*?)(?=^## |\Z)", doc, re.M | re.S)
    assert match is not None
    section = match.group("section")
    lanes: list[str] = []
    in_table = False
    for line in section.splitlines():
        if line.startswith("| Lane |"):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("| --- |"):
            continue
        if not line.startswith("|"):
            if lanes:
                break
            continue
        lanes.append(line.split("|")[1].strip())
    return lanes
