"""Unit tests for the cross-batch design experience ledger."""

from __future__ import annotations

from pathlib import Path

from core.paths import ProjectPaths
from services.design_ledger import append_design, recent_entries


def _paths(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths(root=tmp_path / "factory", repository=tmp_path)
    paths.initialize()
    return paths


def test_append_and_read_recent(tmp_path):
    paths = _paths(tmp_path)
    append_design(paths, {"category": "re", "semantic_fingerprint": "vm-bytecode"})
    append_design(paths, {"category": "web", "semantic_fingerprint": "ssrf-internal"})
    append_design(paths, {"category": "re", "semantic_fingerprint": "wasm-state"})

    all_rows = recent_entries(paths)
    assert len(all_rows) == 3
    re_rows = recent_entries(paths, category="re")
    assert [r["semantic_fingerprint"] for r in re_rows] == ["vm-bytecode", "wasm-state"]


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
