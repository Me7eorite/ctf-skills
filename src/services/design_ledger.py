"""Cross-batch design experience ledger.

Persists a compact digest of every completed design so later batches can plan
against earlier ones — the cross-batch half of anti-collapse (the in-prompt
"prior batch designs" handles within-batch). Append-only JSONL keeps it cheap
and human-inspectable; readers tail the last N lines.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core.paths import ProjectPaths


def _ledger_path(paths: ProjectPaths) -> Path:
    return paths.design_logs.parent / "ledger.jsonl"


def append_design(paths: ProjectPaths, entry: Mapping[str, Any]) -> None:
    """Append one design digest. Best-effort: never raise into the caller."""
    try:
        path = _ledger_path(paths)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(entry), ensure_ascii=False) + "\n")
    except OSError:
        # The ledger is an optimization, not a correctness dependency.
        return


def recent_entries(
    paths: ProjectPaths,
    *,
    category: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` most recent ledger entries (optionally filtered)."""
    path = _ledger_path(paths)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and (
            category is None or obj.get("category") == category
        ):
            rows.append(obj)
    return rows
