"""Build execution workspace lifecycle and manifest helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from core.jsonio import read_json, write_json
from core.paths import ProjectPaths
from core.queue import SUPPORTED_CATEGORIES

_LOGGER = logging.getLogger(__name__)
_MANUAL_PREFIX = "manual-"
_MANUAL_RETENTION = timedelta(days=7)
_LAYOUT = ("input", "references", "output", "logs", "bin", "state")
# Phase 1 (9-references → 3): the design skill is now a single core file
# (`design-core.md` — output shape, spec template, quality gate, safety) plus
# a unified `category-tactics.md` covering every category. Per-category
# splits and the standalone delivery-format reference were removed.
_COMMON_REFERENCES = ("design-core.md", "category-tactics.md")
_CATEGORIES_WITH_REFERENCES = frozenset({"web", "pwn", "re"})
# 中文注释：`_CHALLENGE_NAMESPACE` 只用来识别"这个目录名属于挑战命名空间"，
# 比之前 ^(web|pwn|re)-\d+ 宽松，能覆盖真实 design-task 生成的
# web-<hex8>-<NNNN>-<slug> 形态。具体哪一个 id 是已认领的，由 `_match_claimed_id`
# 拿 shard payload 里的 ids 集合做精确匹配，不再依赖 regex 的格式假设。
_CHALLENGE_NAMESPACE = re.compile(r"^(web|pwn|re)-[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _match_claimed_id(name: str, claimed_ids: set[str]) -> str | None:
    """Return the claimed challenge_id whose directory name is `name`, or None.

    Matches exact id (`web-abcdef12-0001`) or id + slug (`web-abcdef12-0001-demo`).
    Order-independent: longer ids win automatically because `startswith` is
    deterministic for the given claimed-ids set.
    """
    for cid in claimed_ids:
        if name == cid or name.startswith(f"{cid}-"):
            return cid
    return None


class WorkspacePreflightError(ValueError):
    """Workspace is unsafe or incomplete for a Hermes build invocation."""


class WorkspacePromotionError(ValueError):
    """Workspace output cannot be safely promoted."""


@dataclass(frozen=True)
class ExecutionWorkspace:
    """Paths and identity for one build invocation."""

    workspace_id: str
    root: Path

    @property
    def input(self) -> Path:
        return self.root / "input"

    @property
    def references(self) -> Path:
        return self.root / "references"

    @property
    def manifest(self) -> Path:
        return self.input / "manifest.json"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def report(self) -> Path:
        return self.logs / "report.json"

    @property
    def hermes_log(self) -> Path:
        return self.logs / "hermes.log"

    @property
    def state(self) -> Path:
        return self.root / "state"


def derive_workspace_id(payload: Mapping[str, Any]) -> str:
    """Use the attributed build attempt UUID, otherwise create a manual id."""
    raw_attempt_id = payload.get("build_attempt_id")
    if raw_attempt_id is None:
        return f"{_MANUAL_PREFIX}{uuid.uuid4()}"
    try:
        return str(uuid.UUID(str(raw_attempt_id)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("build_attempt_id must be a UUID") from exc


def prepare_workspace(
    paths: ProjectPaths,
    *,
    shard: Path,
    original_shard_name: str,
    worker: str,
    now: datetime | None = None,
) -> ExecutionWorkspace:
    """Create a clean fixed-layout workspace and immutable shard manifest."""
    payload = read_json(shard, None)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid shard payload: {shard.name}")

    created_at = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    executions = _executions_path(paths)
    executions.mkdir(parents=True, exist_ok=True)
    _gc_manual_workspaces(executions, created_at)

    workspace_id = derive_workspace_id(payload)
    root = executions / workspace_id
    _recreate_owned_workspace(executions, root)
    for name in _LAYOUT:
        (root / name).mkdir()

    shard_snapshot = root / "input" / "shard.json"
    shutil.copyfile(shard, shard_snapshot)
    challenges = payload.get("challenges")
    category = _single_category(challenges)
    materialized = _materialize_context(paths, root, category)
    input_files = [shard_snapshot, *materialized]
    manifest = {
        "workspace_id": workspace_id,
        "original_shard_basename": Path(original_shard_name).name,
        "running_shard_basename": shard.name,
        "worker": worker,
        "category": category,
        "build_attempt_id": payload.get("build_attempt_id"),
        "design_task_id": payload.get("design_task_id"),
        "created_at": created_at.astimezone(timezone.utc).isoformat(),
        "input_hashes": {
            path.relative_to(root).as_posix(): f"sha256:{_sha256(path)}"
            for path in input_files
        },
        "allowed_static_reference_roots": [],
        "reference_files": [
            path.relative_to(root).as_posix()
            for path in materialized
            if path.is_relative_to(root / "references")
        ],
    }
    write_json(root / "input" / "manifest.json", manifest)
    return ExecutionWorkspace(workspace_id=workspace_id, root=root)


def preflight_workspace(
    workspace: ExecutionWorkspace,
    *,
    profile_name: str,
    profile_exists: Callable[[str], bool],
) -> dict[str, Any]:
    """Validate a materialized workspace before any model invocation."""
    if not profile_exists(profile_name):
        raise WorkspacePreflightError(
            f"Hermes profile {profile_name!r} does not exist; "
            f"run: hermes profile create {profile_name}"
        )

    shard_path = workspace.input / "shard.json"
    if shard_path.is_symlink() or not shard_path.is_file():
        raise WorkspacePreflightError("input/shard.json must be a regular file")
    try:
        payload = json.loads(shard_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspacePreflightError("input/shard.json is not readable JSON") from exc
    if not isinstance(payload, dict):
        raise WorkspacePreflightError("input/shard.json must contain a JSON object")

    category, challenge_ids = _validate_challenges(payload.get("challenges"))
    expected_profile = f"cf-{category}"
    if profile_name != expected_profile:
        raise WorkspacePreflightError(
            f"profile/category mismatch: {profile_name!r} != {expected_profile!r}"
        )

    manifest = read_json(workspace.manifest, None)
    if not isinstance(manifest, dict):
        raise WorkspacePreflightError("input/manifest.json is not readable JSON")
    if manifest.get("category") != category:
        raise WorkspacePreflightError("manifest category does not match shard category")
    _verify_materialized_hashes(workspace, manifest)
    _verify_output_writable(workspace.output)
    _verify_progress_shim(workspace)
    _reject_unrelated_artifacts(workspace.root, challenge_ids)
    _verify_reference_symlinks(workspace.references, manifest)
    return payload


def _verify_progress_shim(workspace: ExecutionWorkspace) -> None:
    """Fail closed if `./bin/progress` shim is missing or not executable.

    The prompt renders `./bin/progress`; if the shim is absent or unreadable
    here, Hermes would only discover it mid-run, after the model has already
    started. preflight is the documented fail-closed gate.
    """
    shim = workspace.root / "bin" / "progress"
    if shim.is_symlink() or not shim.is_file():
        raise WorkspacePreflightError(
            "bin/progress shim is missing; runner must materialize it before preflight"
        )
    if not os.access(shim, os.X_OK):
        raise WorkspacePreflightError("bin/progress shim is not executable")


def import_workspace_report(workspace: ExecutionWorkspace, legacy_report: Path) -> bool:
    """Copy a workspace report to the legacy report location when present."""
    source = workspace.report
    if source.is_symlink() or not source.is_file():
        return False
    legacy_report.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, legacy_report)
    return True


def record_effective_timeout(
    workspace: ExecutionWorkspace,
    *,
    seconds: int,
    source: str,
) -> None:
    manifest = read_json(workspace.manifest, None)
    if not isinstance(manifest, dict):
        raise WorkspacePreflightError("input/manifest.json is not readable JSON")
    manifest["effective_timeout_seconds"] = seconds
    manifest["timeout_source"] = source
    write_json(workspace.manifest, manifest)


def materialize_resume_outputs(
    paths: ProjectPaths,
    workspace: ExecutionWorkspace,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    """Copy existing claimed canonical artifacts into isolated workspace output.

    Returns a mapping ``{claimed_id: workspace_relative_path}`` for every id
    that already had a canonical directory; ids without a canonical
    predecessor are omitted.
    """
    category, challenge_ids = _validate_challenges(payload.get("challenges"))
    destination_root = workspace.output / "challenges" / category
    destination_root.mkdir(parents=True, exist_ok=True)
    targets: dict[str, str] = {}
    for challenge_id in challenge_ids:
        existing = _matching_directories(paths.challenges / category, challenge_id)
        if len(existing) > 1:
            raise WorkspacePromotionError(
                f"multiple canonical directories for claimed id {challenge_id}"
            )
        if not existing:
            continue
        source = existing[0]
        _reject_tree_symlinks(source)
        destination = destination_root / source.name
        shutil.copytree(source, destination)
        targets[challenge_id] = destination.relative_to(workspace.root).as_posix()
    return targets


def promote_claimed_outputs(
    paths: ProjectPaths,
    workspace: ExecutionWorkspace,
    payload: Mapping[str, Any],
) -> list[Path]:
    del paths, workspace, payload
    raise WorkspacePromotionError(
        "promote_claimed_outputs removed; use hermes.build_publisher."
        "publish_workspace_output with a PublicationContract"
    )


def _executions_path(paths: ProjectPaths) -> Path:
    # Preserve structural test doubles while ProjectPaths exposes the contract.
    return getattr(paths, "executions", paths.root / "work" / "executions")


def _single_category(challenges: Any) -> str | None:
    if not isinstance(challenges, list):
        return None
    categories = {
        item.get("category")
        for item in challenges
        if isinstance(item, dict) and isinstance(item.get("category"), str)
    }
    return next(iter(categories)) if len(categories) == 1 else None


def _materialize_context(
    paths: ProjectPaths,
    root: Path,
    category: str | None,
) -> list[Path]:
    copied: list[Path] = []
    generation_target = root / "input" / "generation-profiles.json"
    _copy_regular_file(paths.generation_profile, generation_target)
    copied.append(generation_target)

    skill_target = root / "references" / "design-challenges" / "SKILL.md"
    _copy_regular_file(paths.design_skill, skill_target)
    copied.append(skill_target)
    if category not in _CATEGORIES_WITH_REFERENCES:
        return copied

    references_target = skill_target.parent / "references"
    for filename in _COMMON_REFERENCES:
        target = references_target / filename
        _copy_regular_file(paths.design_references / filename, target)
        copied.append(target)
    return copied


def _copy_regular_file(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"required workspace input is not a regular file: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _validate_challenges(challenges: Any) -> tuple[str, set[str]]:
    if not isinstance(challenges, list) or not challenges:
        raise WorkspacePreflightError("shard challenges must be a non-empty array")
    categories: set[str] = set()
    challenge_ids: set[str] = set()
    for challenge in challenges:
        if not isinstance(challenge, dict):
            raise WorkspacePreflightError("every shard challenge must be an object")
        category = challenge.get("category")
        challenge_id = challenge.get("id")
        if category not in SUPPORTED_CATEGORIES:
            raise WorkspacePreflightError(f"unsupported challenge category: {category!r}")
        if not isinstance(challenge_id, str) or not challenge_id:
            raise WorkspacePreflightError("every shard challenge must have a string id")
        categories.add(category)
        challenge_ids.add(challenge_id)
    if len(categories) != 1:
        raise WorkspacePreflightError("all shard challenges must use one category")
    return next(iter(categories)), challenge_ids


def _verify_materialized_hashes(
    workspace: ExecutionWorkspace,
    manifest: dict[str, Any],
) -> None:
    hashes = manifest.get("input_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise WorkspacePreflightError("manifest input_hashes is missing")
    for relative, expected in hashes.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise WorkspacePreflightError("manifest input_hashes is malformed")
        path = workspace.root / relative
        try:
            path.resolve().relative_to(workspace.root.resolve())
        except ValueError as exc:
            raise WorkspacePreflightError(f"hashed input escapes workspace: {relative}") from exc
        if path.is_symlink() or not path.is_file():
            raise WorkspacePreflightError(f"hashed input is not a regular file: {relative}")
        actual = f"sha256:{_sha256(path)}"
        if actual != expected:
            raise WorkspacePreflightError(f"input hash mismatch: {relative}")


def _verify_output_writable(output: Path) -> None:
    if output.is_symlink() or not output.is_dir():
        raise WorkspacePreflightError("output must be a regular directory")
    try:
        with tempfile.NamedTemporaryFile(prefix=".preflight-", dir=output, delete=True):
            pass
    except OSError as exc:
        raise WorkspacePreflightError("output is not writable") from exc


def _reject_unrelated_artifacts(root: Path, challenge_ids: set[str]) -> None:
    for current, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            entry = Path(current) / name
            names = [name]
            if entry.is_symlink():
                names.append(entry.resolve(strict=False).name)
            for candidate in names:
                if not _CHALLENGE_NAMESPACE.match(candidate):
                    continue
                if _match_claimed_id(candidate, challenge_ids) is None:
                    raise WorkspacePreflightError(
                        f"workspace contains unrelated challenge artifact: {candidate}"
                    )


def _verify_reference_symlinks(
    references: Path,
    manifest: dict[str, Any],
) -> None:
    raw_roots = manifest.get("allowed_static_reference_roots")
    if not isinstance(raw_roots, list) or not all(
        isinstance(item, str) for item in raw_roots
    ):
        raise WorkspacePreflightError("manifest reference-root allowlist is malformed")
    allowed_roots = [Path(item).resolve() for item in raw_roots]
    for entry in references.rglob("*"):
        if not entry.is_symlink():
            continue
        resolved = entry.resolve(strict=False)
        if not any(_is_relative_to(resolved, root) for root in allowed_roots):
            raise WorkspacePreflightError(f"unsafe reference symlink: {entry}")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _matching_directories(root: Path, challenge_id: str) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        entry
        for entry in root.glob(f"{challenge_id}-*")
        if entry.is_dir() and not entry.is_symlink()
    )


def _reject_tree_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise WorkspacePromotionError(f"symlink is not allowed: {root}")
    if not root.exists():
        return
    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise WorkspacePromotionError(f"symlink is not allowed: {entry}")


def _reject_nonconforming_output(
    output_root: Path,
    expected_root: Path,
    challenge_ids: set[str],
) -> None:
    for entry in output_root.rglob("*"):
        if not entry.is_dir():
            continue
        if not _CHALLENGE_NAMESPACE.match(entry.name):
            continue
        if _match_claimed_id(entry.name, challenge_ids) is None:
            raise WorkspacePromotionError(f"unclaimed output directory: {entry.name}")
        try:
            entry.relative_to(expected_root)
        except ValueError as exc:
            raise WorkspacePromotionError(
                f"claimed output uses non-conforming layout: {entry}"
            ) from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _recreate_owned_workspace(executions: Path, root: Path) -> None:
    if root.parent != executions or root.name in {"", ".", ".."}:
        raise ValueError("workspace must be a direct child of executions")
    if root.is_symlink():
        raise ValueError(f"workspace path must not be a symlink: {root}")
    if root.exists():
        if not root.is_dir():
            raise ValueError(f"workspace path is not a directory: {root}")
        shutil.rmtree(root)
    root.mkdir()


def _gc_manual_workspaces(executions: Path, now: datetime) -> None:
    cutoff = now.timestamp() - _MANUAL_RETENTION.total_seconds()
    for candidate in executions.glob(f"{_MANUAL_PREFIX}*"):
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            is_empty = next(candidate.iterdir(), None) is None
            is_orphaned = not (candidate / "input" / "manifest.json").is_file()
            if is_empty or is_orphaned or candidate.stat().st_mtime < cutoff:
                shutil.rmtree(candidate)
        except OSError as exc:
            _LOGGER.warning("manual workspace GC skipped for %s: %s", candidate, exc)
