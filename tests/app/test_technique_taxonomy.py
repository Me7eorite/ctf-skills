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


def test_resolve_sub_technique_keeps_sqli_variants_folded():
    assert resolve_sub_technique(_finding("blind SQLi")) == resolve_sub_technique(
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
        ("ret2csu", "ret2libc"),
        ("ret2dlresolve", "ret2libc"),
        ("stack pivot", "ret2libc"),
    ],
)
def test_alias_map_conservatism_guard(left: str, right: str):
    assert resolve_sub_technique(_finding(left)) != resolve_sub_technique(_finding(right))


@pytest.mark.parametrize(
    "label",
    [
        "glibc heap",
        "heap exploitation",
        "tcache poisoning",
        "fastbin dup",
        "unsorted bin attack",
        "use after free",
        "UAF",
    ],
)
def test_pwn_heap_aliases_match_governed_profile_key(label: str):
    assert resolve_sub_technique(_finding(label)) == "heap_uaf_tcache"


@pytest.mark.parametrize("label", ["ROP", "ROP chain", "return oriented programming"])
def test_pwn_rop_aliases_match_governed_profile_key(label: str):
    assert resolve_sub_technique(_finding(label)) == "ret2libc"


def test_category_tactics_lane_list_matches_taxonomy_constants():
    doc = _category_tactics_text()
    expected_by_heading = {
        "Web": _display_lanes("web"),
        "Pwn": _display_lanes("pwn"),
        "Reverse": _display_lanes("re"),
    }
    for heading, expected_lanes in expected_by_heading.items():
        assert _doc_lanes(doc, heading) == expected_lanes


def test_category_tactics_decouples_steps_from_difficulty():
    doc = _category_tactics_text()

    assert "考点 (distinct technique) ≠ 解题步骤 (mechanical step)" in doc
    assert "strings→base64→flag" in doc
    assert "IDA→xor→base64→flag" in doc
    assert "single-step solve" not in doc
    assert "Phase 2 will add" not in doc


def test_difficulty_rubric_uses_upper_bound_path_steps():
    doc = _difficulty_rubric_text()

    assert "| medium | **2 or 3** | ≤ 5 |" in doc
    assert "| hard   | **3 or 4** | ≤ 7 |" in doc
    assert "there is no per-tier minimum" in doc
    assert "strings→base64→flag" in doc
    assert "IDA→xor→base64→flag" in doc
    assert "2–5" not in doc
    assert "3–7" not in doc


def test_design_planner_prompt_decouples_steps_from_difficulty():
    doc = _design_planner_prompt_text()

    assert "Difficulty is driven by the count of distinct 考点 + novelty" in doc
    assert "NOT by the\nnumber of solve steps" in doc
    assert "linear decode/unwrap chain is ONE technique" in doc


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


def _difficulty_rubric_text() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "design-challenges"
        / "references"
        / "difficulty-rubric.md"
    ).read_text(encoding="utf-8")


def _design_planner_prompt_text() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2]
        / "prompts"
        / "design_planner_prompt.md"
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
