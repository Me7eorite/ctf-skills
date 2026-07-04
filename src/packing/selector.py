"""Challenge selection for delivery packing."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from core.jsonio import read_json
from core.paths import ProjectPaths


def _selected_challenges(
    paths: ProjectPaths,
    include_ids: set[str] | set[UUID] | None = None,
) -> list[tuple[Path, dict[str, Any]]]:
    selected = []
    normalized_ids = {str(item) for item in include_ids} if include_ids else None
    for metadata_path in sorted(paths.challenges.glob("*/*/metadata.json")):
        metadata = read_json(metadata_path, {})
        if not isinstance(metadata, dict):
            continue
        # Publish gate: a challenge ships only when it both built AND its
        # reference solver actually passed validation. Gating on build_status
        # alone shipped challenges whose solver was pending/broken/hardcoded.
        if metadata.get("build_status") != "passed":
            continue
        if metadata.get("solve_status") != "passed":
            continue
        if normalized_ids is not None and str(metadata.get("id")) not in normalized_ids:
            continue
        selected.append((metadata_path.parent, metadata))
    return selected
