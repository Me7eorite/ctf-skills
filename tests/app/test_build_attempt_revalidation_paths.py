from __future__ import annotations

from pathlib import Path

from core.jsonio import write_json
from core.paths import ProjectPaths
from domain.resume import ChallengeResumePlan
from services.build_attempt_revalidation_service import _canonicalize_challenge_directory


def test_canonicalize_copies_execution_workspace_output(tmp_path: Path) -> None:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    workspace_challenge = (
        paths.executions
        / "attempt-1"
        / "current"
        / "output"
        / "challenges"
        / "re"
        / "re-0001-demo"
    )
    write_json(
        workspace_challenge / "metadata.json",
        {"id": "re-0001", "category": "re"},
    )
    (workspace_challenge / "validate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    plan = ChallengeResumePlan(
        challenge_id="re-0001",
        directory=workspace_challenge,
        lookup_status="workspace",
        first_pending_stage="validate",
    )

    canonical = _canonicalize_challenge_directory(paths, "re-0001", plan)

    assert canonical == paths.challenges / "re" / "re-0001-demo"
    assert (canonical / "metadata.json").is_file()
    assert workspace_challenge.is_dir()


def test_canonicalize_keeps_existing_canonical_directory(tmp_path: Path) -> None:
    paths = ProjectPaths(root=tmp_path, repository=tmp_path)
    paths.initialize()
    challenge = paths.challenges / "re" / "re-0001-demo"
    write_json(challenge / "metadata.json", {"id": "re-0001", "category": "re"})
    plan = ChallengeResumePlan(
        challenge_id="re-0001",
        directory=challenge,
        lookup_status="matched",
        first_pending_stage="validate",
    )

    assert _canonicalize_challenge_directory(paths, "re-0001", plan) == challenge
