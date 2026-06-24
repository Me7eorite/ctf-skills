"""Unit tests for the cross-batch design experience ledger + rotation offset."""

from __future__ import annotations

from pathlib import Path

from core.paths import ProjectPaths
from services import design_task_planning_service as planning_module
from services.design_ledger import append_design, recent_entries


def _paths(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths(root=tmp_path / "factory", repository=tmp_path)
    paths.initialize()
    return paths


def test_append_and_read_recent(tmp_path):
    paths = _paths(tmp_path)
    append_design(paths, {"category": "re", "core_mechanism": "xor_keystream"})
    append_design(paths, {"category": "web", "core_mechanism": "ssrf_internal"})
    append_design(paths, {"category": "re", "core_mechanism": "aes"})

    all_rows = recent_entries(paths)
    assert len(all_rows) == 3
    re_rows = recent_entries(paths, category="re")
    assert [r["core_mechanism"] for r in re_rows] == ["xor_keystream", "aes"]


def test_recent_entries_missing_file_is_empty(tmp_path):
    paths = _paths(tmp_path)
    assert recent_entries(paths) == []


def test_append_is_best_effort_on_bad_path(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    # Force the ledger path to be unwritable; append must not raise.
    monkeypatch.setattr(
        "services.design_ledger._ledger_path",
        lambda p: Path("/this/does/not/exist/ledger.jsonl"),
    )
    append_design(paths, {"category": "re"})  # no exception


def test_allocate_core_mechanisms_start_offset_continues_rotation():
    catalog = planning_module._DEFAULT_MECHANISMS["re"]
    first = planning_module._allocate_core_mechanisms("re", 3, start_offset=0)
    second = planning_module._allocate_core_mechanisms("re", 3, start_offset=3)
    assert first == list(catalog[0:3])
    assert second == list(catalog[3:6])
    # The two batches together do not repeat a mechanism.
    assert set(first).isdisjoint(set(second))
