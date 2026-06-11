"""Host-side resume planning.

Computes a structured resume plan for a claimed shard before the runner
writes its own queued event. The plan combines:

- the previous claim window's challenge events (from StateStore)
- deterministic file/image/SHA-256 evidence on disk

The runner consumes the plan to carry forward verified stage prefixes and
to choose the first pending stage per challenge.

This module MUST NOT import ``subprocess`` directly. The single Docker
inspection call is delegated to ``core.docker.image_exists``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.docker import image_exists as default_image_exists
from core.paths import ProjectPaths
from core.state import StateStore

STAGE_ORDER: tuple[str, ...] = (
    "design",
    "implement",
    "build",
    "validate",
    "document",
)

_DOCUMENT_HEADING_PREFIX = "## "
_DOCUMENT_MIN_BYTES = 500
_DOCUMENT_MIN_HEADINGS = 2

_DOC_EXTENSIONS = {".md", ".rst", ".txt"}
_BUILD_FILENAMES = {
    "Makefile",
    "makefile",
    "GNUmakefile",
    "CMakeLists.txt",
    "build.gradle",
    "build.gradle.kts",
    "build.sh",
}


@dataclass(frozen=True)
class ChallengeLookup:
    """Result of locating a challenge directory by id."""

    challenge_id: str
    directory: Path | None
    status: str  # "ok" | "missing_challenge" | "ambiguous_challenge"


@dataclass(frozen=True)
class ChallengeResumePlan:
    """Per-challenge resume plan.

    ``skipped_stages`` holds the connected passed prefix in conceptual order.
    ``first_pending_stage`` is the first stage NOT in ``skipped_stages`` or
    None when every stage is skipped (all-skipped short circuit).
    ``stage_sources`` maps a skipped stage to the historical event id that
    justified the carry-forward (used in carry-forward message strings).
    """

    challenge_id: str
    directory: Path | None
    lookup_status: str
    skipped_stages: tuple[str, ...] = ()
    first_pending_stage: str | None = "design"
    stage_sources: dict[str, int] = field(default_factory=dict)

    @property
    def all_skipped(self) -> bool:
        return len(self.skipped_stages) == len(STAGE_ORDER)


@dataclass(frozen=True)
class ShardResumePlan:
    """Resume plan for an entire shard."""

    shard: str
    previous_claim_event_id: int | None
    challenges: tuple[ChallengeResumePlan, ...]

    @property
    def all_challenges_fully_skipped(self) -> bool:
        return bool(self.challenges) and all(
            plan.all_skipped for plan in self.challenges
        )


def find_challenge_directory(
    paths: ProjectPaths, challenge_id: str
) -> ChallengeLookup:
    """Locate a unique ``<challenge_id>-<slug>`` directory under work/challenges.

    The directory pattern follows ``work/challenges/<category>/<id>-<slug>/``.
    Zero matches return ``missing_challenge``; multiple matches return
    ``ambiguous_challenge``. Neither error case selects a directory.
    """
    matches: list[Path] = []
    for path in paths.challenges.glob("*/*"):
        if not path.is_dir():
            continue
        name = path.name
        if name == challenge_id or name.startswith(f"{challenge_id}-"):
            matches.append(path)
    if not matches:
        return ChallengeLookup(challenge_id, None, "missing_challenge")
    if len(matches) > 1:
        return ChallengeLookup(challenge_id, None, "ambiguous_challenge")
    return ChallengeLookup(challenge_id, matches[0], "ok")


def _read_metadata(challenge_dir: Path) -> dict[str, Any] | None:
    metadata_path = challenge_dir / "metadata.json"
    if not metadata_path.is_file():
        return None
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_business_source(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size == 0:
            return False
    except OSError:
        return False
    if path.suffix.lower() in _DOC_EXTENSIONS:
        return False
    if path.name in _BUILD_FILENAMES:
        return False
    # Treat shell scripts whose name starts with build/compile as build-only.
    lowered = path.name.lower()
    if lowered.endswith(".sh") and (
        lowered.startswith("build") or lowered.startswith("compile")
    ):
        return False
    return True


def _any_business_source(root: Path) -> bool:
    if not root.is_dir():
        return False
    for entry in root.rglob("*"):
        if _is_business_source(entry):
            return True
    return False


def design_evidence(challenge_dir: Path, challenge_id: str) -> bool:
    metadata = _read_metadata(challenge_dir)
    if metadata is None:
        return False
    return metadata.get("id") == challenge_id


def implement_evidence(challenge_dir: Path, category: str) -> bool:
    if category in {"web", "pwn"}:
        deploy = challenge_dir / "deploy"
        if not (deploy / "src").is_dir():
            return False
        if not (deploy / "Dockerfile").is_file():
            return False
        if not (deploy / "docker-compose.yml").is_file():
            return False
        return _any_business_source(deploy / "src")
    if category == "re":
        return _any_business_source(challenge_dir / "src")
    return False


def _sha256_of_file(path: Path) -> str | None:
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


def _safe_artifact_path(challenge_dir: Path, artifact: str) -> Path | None:
    """Return the resolved artifact path if and only if it stays under dist/.

    Rejects absolute paths, parent traversal, and any resolution that escapes
    the challenge directory or sits outside ``dist/``.
    """
    candidate = Path(artifact)
    if candidate.is_absolute():
        return None
    if any(part == ".." for part in candidate.parts):
        return None
    base = challenge_dir.resolve()
    resolved = (challenge_dir / candidate).resolve()
    try:
        relative = resolved.relative_to(base)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] != "dist":
        return None
    return resolved


def build_evidence(
    challenge_dir: Path,
    category: str,
    image_exists: Callable[[str], bool],
) -> bool:
    metadata = _read_metadata(challenge_dir)
    if metadata is None:
        return False
    if metadata.get("build_status") != "passed":
        return False
    build_command = metadata.get("build_command")
    if not isinstance(build_command, str) or not build_command.strip():
        return False
    if category in {"web", "pwn"}:
        docker_image = metadata.get("docker_image")
        if not isinstance(docker_image, str) or not docker_image.strip():
            return False
        return image_exists(docker_image)
    if category == "re":
        artifact = metadata.get("artifact")
        expected_sha = metadata.get("artifact_sha256")
        if (
            not isinstance(artifact, str)
            or not artifact.strip()
            or not isinstance(expected_sha, str)
            or not expected_sha.strip()
        ):
            return False
        resolved = _safe_artifact_path(challenge_dir, artifact.strip())
        if resolved is None or not resolved.is_file():
            return False
        actual = _sha256_of_file(resolved)
        return actual is not None and actual == expected_sha.strip()
    return False


def validate_resume_evidence(
    challenge_dir: Path,
    challenge_events: Iterable[dict[str, Any]],
) -> bool:
    if not (challenge_dir / "validate.sh").is_file():
        return False
    if not (challenge_dir / "solve" / "solve.py").is_file():
        return False
    metadata = _read_metadata(challenge_dir)
    if metadata is None or metadata.get("solve_status") != "passed":
        return False
    return any(
        event.get("stage") == "validate" and event.get("status") == "passed"
        for event in challenge_events
    )


def document_evidence(challenge_dir: Path) -> bool:
    for relative in ("writeup/wp.md", "README.md"):
        path = challenge_dir / relative
        if not path.is_file():
            return False
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size <= _DOCUMENT_MIN_BYTES:
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        heading_count = sum(
            1 for line in text.splitlines() if line.startswith(_DOCUMENT_HEADING_PREFIX)
        )
        if heading_count < _DOCUMENT_MIN_HEADINGS:
            return False
    return True


def _latest_stage_event(
    events: list[dict[str, Any]], stage: str
) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("stage") == stage:
            return event
    return None


def _stage_evidence_ok(
    stage: str,
    challenge_dir: Path,
    category: str,
    image_exists: Callable[[str], bool],
    events: list[dict[str, Any]],
    challenge_id: str,
) -> bool:
    if stage == "design":
        return design_evidence(challenge_dir, challenge_id)
    if stage == "implement":
        return implement_evidence(challenge_dir, category)
    if stage == "build":
        return build_evidence(challenge_dir, category, image_exists)
    if stage == "validate":
        return validate_resume_evidence(challenge_dir, events)
    if stage == "document":
        return document_evidence(challenge_dir)
    return False


def _category_from_dir(challenge_dir: Path, paths: ProjectPaths) -> str:
    try:
        relative = challenge_dir.resolve().relative_to(paths.challenges.resolve())
    except ValueError:
        return ""
    return relative.parts[0] if relative.parts else ""


def compute_resume_plan(
    *,
    state: StateStore,
    paths: ProjectPaths,
    shard: str,
    challenge_ids: list[str],
    image_exists: Callable[[str], bool] = default_image_exists,
) -> ShardResumePlan:
    """Compute a resume plan for the given shard before the new queued event.

    Callers MUST call this BEFORE writing the current run's shard-level
    queued/running event. The plan reads the latest shard-level queued/running
    event as the lower window bound.
    """
    previous_claim = state.latest_claim_event(shard)
    previous_id = previous_claim["id"] if previous_claim else None

    plans: list[ChallengeResumePlan] = []
    for challenge_id in challenge_ids:
        lookup = find_challenge_directory(paths, challenge_id)
        if lookup.directory is None:
            plans.append(
                ChallengeResumePlan(
                    challenge_id=challenge_id,
                    directory=None,
                    lookup_status=lookup.status,
                    skipped_stages=(),
                    first_pending_stage="design",
                    stage_sources={},
                )
            )
            continue

        category = _category_from_dir(lookup.directory, paths)
        events: list[dict[str, Any]] = []
        if previous_id is not None:
            events = state.events_for_challenge(
                shard, challenge_id, after_id=previous_id
            )

        skipped: list[str] = []
        sources: dict[str, int] = {}
        for stage in STAGE_ORDER:
            latest = _latest_stage_event(events, stage)
            if latest is None or latest.get("status") != "passed":
                break
            if not _stage_evidence_ok(
                stage,
                lookup.directory,
                category,
                image_exists,
                events,
                challenge_id,
            ):
                break
            skipped.append(stage)
            sources[stage] = int(latest["id"])

        next_index = len(skipped)
        first_pending = (
            STAGE_ORDER[next_index] if next_index < len(STAGE_ORDER) else None
        )

        plans.append(
            ChallengeResumePlan(
                challenge_id=challenge_id,
                directory=lookup.directory,
                lookup_status=lookup.status,
                skipped_stages=tuple(skipped),
                first_pending_stage=first_pending,
                stage_sources=sources,
            )
        )

    return ShardResumePlan(
        shard=shard,
        previous_claim_event_id=previous_id,
        challenges=tuple(plans),
    )


def carry_forward_message(stage: str, source_event_id: int) -> str:
    """Format a carry-forward message with the required ``carry-forward:`` prefix."""
    return (
        f"carry-forward: skipping {stage} from historical event "
        f"#{source_event_id}; evidence revalidated"
    )


def validator_message(
    *,
    status: str,
    elapsed: float | None = None,
    flag_matched: bool | None = None,
    error: str | None = None,
) -> str:
    """Format a fresh validator-written message with the required ``validator:`` prefix."""
    parts = [f"validator: status={status}"]
    if elapsed is not None:
        parts.append(f"elapsed={elapsed:.2f}s")
    if flag_matched is not None:
        parts.append(f"flag_matched={'yes' if flag_matched else 'no'}")
    if error:
        parts.append(f"error={error}")
    return " ".join(parts)
