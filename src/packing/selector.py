"""Challenge selection for delivery packing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.jsonio import read_json
from core.paths import ProjectPaths


def _selected_challenges(paths: ProjectPaths) -> list[tuple[Path, dict[str, Any]]]:
    selected = []
    for metadata_path in sorted(paths.challenges.glob("*/*/metadata.json")):
        metadata = read_json(metadata_path, {})
        if not isinstance(metadata, dict) or metadata.get("build_status") != "passed":
            continue
        selected.append((metadata_path.parent, metadata))
    return selected
