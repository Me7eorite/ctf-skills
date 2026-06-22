"""Publisher-owned boundary from execution workspace output to canonical challenges."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from core.jsonio import read_json, write_json
from core.paths import ProjectPaths
from hermes.workspace import (
    ExecutionWorkspace,
    WorkspacePromotionError,
    _match_claimed_id,
    _matching_directories,
    _validate_challenges,
)

try:  # POSIX-only flock primitive (Decision 5)
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows hosts hit preflight error
    fcntl = None  # type: ignore[assignment]

_MANIFEST_PROJECTION_FIELDS = {"output_manifest_hash", "publish_generation"}
_STATE_FILES = {
    "first-validation-failure.json",
    "publish-journal.json",
    "publish-status.json",
    "highest-committed-generation.json",
    "validated-output.json",
    "validation-history.json",
}
_CONTRACT_INPUT_HASH_EXCLUDED_PATHS = {f"state/{name}" for name in _STATE_FILES}
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_MAX_FILES = 50_000
_DEFAULT_MAX_DEPTH = 64
_DEFAULT_MAX_COMPONENT_BYTES = 255
_DEFAULT_LOCK_TIMEOUT_SECONDS = 30
_PUBLISH_PHASES = {
    "contract",
    "allowlist",
    "policy",
    "limits",
    "stage",
    "commit",
    "manifest",
    "rollback",
    "recovery",
}


class WorkspacePublishError(WorkspacePromotionError):
    """Publisher failure with a stable operational phase."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        claimed_id: str | None = None,
        path: str | None = None,
    ) -> None:
        if phase not in _PUBLISH_PHASES:
            raise ValueError(f"unknown publisher phase: {phase}")
        super().__init__(message)
        self.phase = phase
        self.claimed_id = claimed_id
        self.path = path


@dataclass(frozen=True)
class PolicySelector:
    path: str
    json_field: str | None = None


@dataclass(frozen=True)
class ChangePolicy:
    base_artifact_relpath: str
    preserve: tuple[PolicySelector, ...]
    forbid: tuple[str, ...]


@dataclass(frozen=True)
class PublicationContract:
    """Immutable host-owned inputs captured before Hermes invocation."""

    category: str
    challenge_ids: tuple[str, ...]
    execution_mode: str
    resume_output_targets: Mapping[str, str]
    shard_snapshot_hash: str
    manifest_projection_hash: str
    input_hashes: Mapping[str, str] = field(default_factory=dict)
    base_artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    change_policy: ChangePolicy | None = None


@dataclass(frozen=True)
class PublishResult:
    published_paths: list[Path]
    quarantined: list[Path]
    output_manifest_hash: str
    outcome: str = "succeeded"


@dataclass(frozen=True)
class WorkspaceValidationSet:
    """Exact workspace candidates and the hash approved by host validation."""

    candidates: Mapping[str, Path]
    output_manifest_hash: str


@dataclass(frozen=True)
class PublisherLimits:
    max_bytes: int = _DEFAULT_MAX_BYTES
    max_files: int = _DEFAULT_MAX_FILES
    max_depth: int = _DEFAULT_MAX_DEPTH
    max_component_bytes: int = _DEFAULT_MAX_COMPONENT_BYTES


def prepare_publication_contract(
    paths: ProjectPaths,
    workspace: ExecutionWorkspace,
    payload: Mapping[str, Any],
    *,
    resume_output_targets: Mapping[str, str] | None = None,
) -> PublicationContract:
    """Capture host-owned publication inputs before Hermes can mutate output."""
    del paths
    try:
        category, challenge_ids = _validate_challenges(payload.get("challenges"))
    except Exception as exc:
        raise WorkspacePublishError(str(exc), phase="contract") from exc

    _verify_state_directory_for_contract(workspace)
    manifest = _read_manifest(workspace)
    execution_mode = _normalize_execution_mode(payload)
    if resume_output_targets is None:
        raw_targets = manifest.get("resume_output_targets", {})
        resume_output_targets = raw_targets if isinstance(raw_targets, Mapping) else {}

    change_policy = _read_change_policy(workspace.input / "change-policy.json")
    base_hashes = _hash_tree(workspace.input / "base-artifact")
    if change_policy is not None and not (workspace.input / "base-artifact").is_dir():
        raise WorkspacePublishError(
            "change-policy requires base-artifact materialization",
            phase="contract",
        )

    return PublicationContract(
        category=category,
        challenge_ids=tuple(sorted(challenge_ids)),
        execution_mode=execution_mode,
        resume_output_targets=dict(resume_output_targets),
        shard_snapshot_hash=_file_hash(workspace.input / "shard.json"),
        manifest_projection_hash=_manifest_projection_hash(manifest),
        input_hashes=_manifest_input_hashes(manifest),
        base_artifact_hashes=base_hashes,
        change_policy=change_policy,
    )


def publish_workspace_output(
    paths: ProjectPaths,
    workspace: ExecutionWorkspace,
    *,
    contract: PublicationContract,
) -> PublishResult:
    """Publish claimed workspace outputs into canonical challenge storage."""
    _preflight_platform()
    _bootstrap_recover_journal(paths, workspace)
    _verify_contract(workspace, contract)
    candidates = _collect_candidates(workspace, contract)
    _cross_check_resume_targets(candidates, contract)
    limits = _publisher_limits_from_env()
    _enforce_output_limits(candidates, limits)
    _enforce_change_policy(workspace, candidates, contract.change_policy)
    staged_hash = _output_manifest_hash(candidates)
    committed = _read_high_water(workspace)
    if committed and committed.get("output_manifest_hash") == staged_hash:
        _write_terminal_marker(workspace, status="noop", output_hash=staged_hash)
        _run_retention_sweep(paths)
        return PublishResult(
            published_paths=[],
            quarantined=[],
            output_manifest_hash=staged_hash,
            outcome="noop",
        )

    canonical_root = paths.challenges / contract.category
    canonical_root.mkdir(parents=True, exist_ok=True)
    quarantine_root = workspace.root / "quarantine" / contract.category
    _verify_same_filesystem(canonical_root, workspace, quarantine_root)
    published: list[Path] = []
    quarantined: list[Path] = []
    published_by_id: dict[str, Path] = {}
    rollback_entries: list[tuple[Path, Path | None, Path | None]] = []
    generation = _next_generation(committed)
    lock_timeout = _positive_env_int(
        "BUILD_PUBLISH_LOCK_TIMEOUT_SECONDS",
        _DEFAULT_LOCK_TIMEOUT_SECONDS,
    )

    try:
        with _acquire_publisher_locks(
            paths,
            contract.category,
            contract.challenge_ids,
            timeout_seconds=lock_timeout,
        ):
            # Stage every candidate into a temp sibling BEFORE any canonical
            # rename so a failure in id N never leaves canonical mutated.
            staged_temps: dict[str, Path] = {}
            for challenge_id in contract.challenge_ids:
                source = candidates[challenge_id]
                existing = _matching_directories(canonical_root, challenge_id)
                if len(existing) > 1:
                    raise WorkspacePublishError(
                        f"multiple canonical directories for claimed id {challenge_id}",
                        phase="allowlist",
                        claimed_id=challenge_id,
                    )
                temporary = canonical_root / (f".workspace-{workspace.workspace_id}-{uuid.uuid4().hex}")
                shutil.copytree(source, temporary, symlinks=True)
                _enforce_output_limits({challenge_id: temporary}, limits)
                staged_temps[challenge_id] = temporary

            # All temps in place — durable journal records the full plan
            # before any canonical mutation.
            _write_publish_journal(
                workspace,
                generation,
                staged_hash,
                phase="stage",
                category=contract.category,
                entries=[
                    {
                        "claimed_id": cid,
                        "source": str(candidates[cid]),
                        "temp": str(staged_temps[cid]),
                        "canonical": str(canonical_root / candidates[cid].name),
                    }
                    for cid in contract.challenge_ids
                ],
            )

            try:
                for challenge_id in contract.challenge_ids:
                    source = candidates[challenge_id]
                    existing = _matching_directories(canonical_root, challenge_id)
                    if len(existing) > 1:
                        raise WorkspacePublishError(
                            f"multiple canonical directories for claimed id {challenge_id}",
                            phase="allowlist",
                            claimed_id=challenge_id,
                        )
                    temporary = staged_temps[challenge_id]
                    quarantined_path: Path | None = None
                    predecessor_path: Path | None = existing[0] if existing else None
                    if existing:
                        quarantine_root.mkdir(parents=True, exist_ok=True)
                        quarantined_path = quarantine_root / existing[0].name
                        if quarantined_path.exists():
                            quarantined_path = quarantine_root / (f"{existing[0].name}.repair-{uuid.uuid4().hex}")
                        existing[0].replace(quarantined_path)
                        quarantined.append(quarantined_path)
                    destination = canonical_root / source.name
                    if destination.exists():
                        raise WorkspacePublishError(
                            f"promotion destination exists: {destination.name}",
                            phase="commit",
                            claimed_id=challenge_id,
                            path=destination.name,
                        )
                    rollback_entries.append((destination, quarantined_path, predecessor_path))
                    temporary.replace(destination)
                    published.append(destination)
                    published_by_id[challenge_id] = destination
                _write_publish_journal(
                    workspace,
                    generation,
                    staged_hash,
                    phase="manifest",
                    category=contract.category,
                )
                canonical_hash = _output_manifest_hash(published_by_id)
                if canonical_hash != staged_hash:
                    raise WorkspacePublishError(
                        "canonical output hash mismatch after publish",
                        phase="manifest",
                    )
                _update_manifest_and_high_water(workspace, generation, staged_hash)
                _write_publish_journal(
                    workspace,
                    generation,
                    staged_hash,
                    phase="committed",
                    category=contract.category,
                )
            except BaseException:
                _write_publish_journal(
                    workspace,
                    generation,
                    staged_hash,
                    phase="rollback",
                    category=contract.category,
                )
                _rollback_published_batch(rollback_entries)
                for temp in staged_temps.values():
                    if temp.exists():
                        shutil.rmtree(temp, ignore_errors=True)
                raise
    except BaseException:
        _write_terminal_marker(workspace, status="failed", output_hash=staged_hash)
        _run_retention_sweep(paths)
        raise
    else:
        _remove_publish_journal(workspace)
        _write_terminal_marker(workspace, status="succeeded", output_hash=staged_hash)
        _run_retention_sweep(paths)
        return PublishResult(
            published_paths=published,
            quarantined=quarantined,
            output_manifest_hash=staged_hash,
        )


def prepare_workspace_validation(
    workspace: ExecutionWorkspace,
    *,
    contract: PublicationContract,
) -> WorkspaceValidationSet:
    """Resolve and preflight the exact workspace output that must be validated.

    This performs the publisher's immutable-contract, allowlist, size,
    resume-binding, and revision-policy checks without touching canonical
    challenge storage.
    """
    _preflight_platform()
    _verify_contract(workspace, contract)
    candidates = _collect_candidates(workspace, contract)
    _cross_check_resume_targets(candidates, contract)
    _enforce_output_limits(candidates, _publisher_limits_from_env())
    _enforce_change_policy(workspace, candidates, contract.change_policy)
    return WorkspaceValidationSet(
        candidates=dict(candidates),
        output_manifest_hash=_output_manifest_hash(candidates),
    )


def record_workspace_terminal(
    paths: ProjectPaths,
    workspace: ExecutionWorkspace,
    *,
    status: str,
    output_hash: str | None = None,
) -> None:
    """Record a terminal pre-publication outcome for retention accounting."""
    if status not in {"failed", "succeeded", "noop"}:
        raise ValueError(f"invalid workspace terminal status: {status}")
    _write_terminal_marker(workspace, status=status, output_hash=output_hash)
    _run_retention_sweep(paths)


def _verify_contract(
    workspace: ExecutionWorkspace,
    contract: PublicationContract,
) -> None:
    _verify_state_directory_for_contract(workspace)
    if _file_hash(workspace.input / "shard.json") != contract.shard_snapshot_hash:
        raise WorkspacePublishError("input/shard.json changed after contract capture", phase="contract")
    manifest = _read_manifest(workspace)
    if _manifest_projection_hash(manifest) != contract.manifest_projection_hash:
        raise WorkspacePublishError("input/manifest.json changed after contract capture", phase="contract")
    if _hash_tree(workspace.input / "base-artifact") != dict(contract.base_artifact_hashes):
        raise WorkspacePublishError("input/base-artifact changed after contract capture", phase="contract")
    current_policy = _read_change_policy(workspace.input / "change-policy.json")
    if current_policy != contract.change_policy:
        raise WorkspacePublishError("input/change-policy.json changed after contract capture", phase="contract")


def _collect_candidates(
    workspace: ExecutionWorkspace,
    contract: PublicationContract,
) -> dict[str, Path]:
    challenge_ids = set(contract.challenge_ids)
    output_root = workspace.output
    expected_root = output_root / "challenges" / contract.category
    _reject_output_tree_symlinks(output_root)
    _reject_nonconforming_output_tree(output_root, expected_root, challenge_ids)
    if not expected_root.is_dir():
        raise WorkspacePublishError(
            f"missing output category directory: {contract.category}",
            phase="allowlist",
        )

    candidates: dict[str, Path] = {}
    for entry in expected_root.iterdir():
        if not entry.is_dir() or entry.is_symlink():
            raise WorkspacePublishError(
                f"invalid output entry: {entry.name}",
                phase="allowlist",
                path=entry.name,
            )
        challenge_id = _match_claimed_id(entry.name, challenge_ids)
        if challenge_id is None:
            raise WorkspacePublishError(
                f"unclaimed output directory: {entry.name}",
                phase="allowlist",
                path=entry.name,
            )
        if challenge_id in candidates:
            raise WorkspacePublishError(
                f"multiple output directories for claimed id {challenge_id}",
                phase="allowlist",
                claimed_id=challenge_id,
            )
        metadata = read_json(entry / "metadata.json", None)
        if not isinstance(metadata, dict):
            raise WorkspacePublishError(
                f"missing metadata.json for {challenge_id}",
                phase="allowlist",
                claimed_id=challenge_id,
            )
        if metadata.get("id") != challenge_id or metadata.get("category") != contract.category:
            raise WorkspacePublishError(
                f"metadata mismatch for {challenge_id}",
                phase="allowlist",
                claimed_id=challenge_id,
            )
        candidates[challenge_id] = entry

    missing = challenge_ids - candidates.keys()
    if missing:
        raise WorkspacePublishError(
            f"missing claimed output: {', '.join(sorted(missing))}",
            phase="allowlist",
        )
    return candidates


def _normalize_execution_mode(payload: Mapping[str, Any]) -> str:
    raw = payload.get("execution_mode")
    resume_source = payload.get("resume_from_shard_basename")
    if raw is None:
        return "resume" if resume_source else "clean"
    if raw not in {"resume", "clean"}:
        raise WorkspacePublishError(f"unsupported execution_mode: {raw!r}", phase="contract")
    if raw == "resume" and not isinstance(resume_source, str):
        raise WorkspacePublishError("explicit resume requires resume_from_shard_basename", phase="contract")
    if raw == "clean" and resume_source is not None:
        raise WorkspacePublishError("explicit clean forbids resume_from_shard_basename", phase="contract")
    return raw


def _reject_output_tree_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise WorkspacePublishError(
            "symlink is not allowed: output",
            phase="allowlist",
            path="output",
        )
    if not root.exists():
        return
    for entry in root.rglob("*"):
        if entry.is_symlink():
            relative = entry.relative_to(root).as_posix()
            raise WorkspacePublishError(
                f"symlink is not allowed: output/{relative}",
                phase="allowlist",
                path=f"output/{relative}",
            )


def _reject_nonconforming_output_tree(
    output_root: Path,
    expected_root: Path,
    challenge_ids: set[str],
) -> None:
    for entry in output_root.rglob("*"):
        if not entry.is_dir():
            continue
        if _match_claimed_id(entry.name, challenge_ids) is None:
            continue
        try:
            entry.relative_to(expected_root)
        except ValueError as exc:
            relative = entry.relative_to(output_root).as_posix()
            raise WorkspacePublishError(
                f"claimed output uses non-conforming layout: output/{relative}",
                phase="allowlist",
                path=f"output/{relative}",
            ) from exc


def _publisher_limits_from_env() -> PublisherLimits:
    return PublisherLimits(
        max_bytes=_positive_env_int("BUILD_PUBLISH_MAX_BYTES", _DEFAULT_MAX_BYTES),
        max_files=_positive_env_int("BUILD_PUBLISH_MAX_FILES", _DEFAULT_MAX_FILES),
        max_depth=_positive_env_int("BUILD_PUBLISH_MAX_DEPTH", _DEFAULT_MAX_DEPTH),
        max_component_bytes=_positive_env_int(
            "BUILD_PUBLISH_MAX_COMPONENT_BYTES",
            _DEFAULT_MAX_COMPONENT_BYTES,
        ),
    )


def _positive_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise WorkspacePublishError(
            f"{name} must be a positive integer",
            phase="limits",
        ) from exc
    if value <= 0:
        raise WorkspacePublishError(
            f"{name} must be a positive integer",
            phase="limits",
        )
    return value


def _enforce_output_limits(
    candidates: Mapping[str, Path],
    limits: PublisherLimits,
) -> None:
    total_bytes = 0
    total_files = 0
    for challenge_id, root in candidates.items():
        for path in [root, *root.rglob("*")]:
            relative = path.relative_to(root)
            _validate_path_limits(relative, limits, challenge_id)
            stat = path.lstat()
            if path.is_symlink():
                raise WorkspacePublishError(
                    f"symlink is not allowed: {relative.as_posix()}",
                    phase="limits",
                    claimed_id=challenge_id,
                    path=relative.as_posix(),
                )
            if path.is_file():
                total_files += 1
                total_bytes += stat.st_size
                if total_files > limits.max_files:
                    raise WorkspacePublishError(
                        "publisher file-count limit exceeded",
                        phase="limits",
                        claimed_id=challenge_id,
                    )
                if total_bytes > limits.max_bytes:
                    raise WorkspacePublishError(
                        "publisher byte limit exceeded",
                        phase="limits",
                        claimed_id=challenge_id,
                    )
            elif not path.is_dir():
                raise WorkspacePublishError(
                    f"special file is not allowed: {relative.as_posix()}",
                    phase="limits",
                    claimed_id=challenge_id,
                    path=relative.as_posix(),
                )


def _validate_path_limits(
    relative: Path,
    limits: PublisherLimits,
    challenge_id: str,
) -> None:
    parts = () if relative.as_posix() == "." else relative.parts
    if len(parts) > limits.max_depth:
        raise WorkspacePublishError(
            "publisher path-depth limit exceeded",
            phase="limits",
            claimed_id=challenge_id,
            path=relative.as_posix(),
        )
    for component in parts:
        if len(component.encode("utf-8")) > limits.max_component_bytes:
            raise WorkspacePublishError(
                "publisher path-component length limit exceeded",
                phase="limits",
                claimed_id=challenge_id,
                path=relative.as_posix(),
            )


def _read_manifest(workspace: ExecutionWorkspace) -> dict[str, Any]:
    manifest = read_json(workspace.manifest, None)
    if not isinstance(manifest, dict):
        raise WorkspacePublishError("input/manifest.json is not readable JSON", phase="contract")
    return manifest


def _read_optional_json(path: Path) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    value = read_json(path, None)
    if not isinstance(value, Mapping):
        raise WorkspacePublishError(f"{path.name} must contain an object", phase="contract")
    return dict(value)


def _read_change_policy(path: Path) -> ChangePolicy | None:
    raw = _read_optional_json(path)
    if raw is None:
        return None
    return _parse_change_policy(raw)


def _parse_change_policy(raw: Mapping[str, Any]) -> ChangePolicy:
    expected = {"base_artifact_relpath", "preserve", "forbid"}
    unknown = set(raw) - expected
    if unknown:
        raise WorkspacePublishError(
            f"unknown change-policy key: {sorted(unknown)[0]}",
            phase="policy",
        )
    base = raw.get("base_artifact_relpath")
    preserve = raw.get("preserve")
    forbid = raw.get("forbid")
    if not isinstance(base, str):
        raise WorkspacePublishError("change-policy base_artifact_relpath must be a string", phase="policy")
    if not isinstance(preserve, list) or not all(isinstance(item, str) for item in preserve):
        raise WorkspacePublishError("change-policy preserve must be a string list", phase="policy")
    if not isinstance(forbid, list) or not all(isinstance(item, str) for item in forbid):
        raise WorkspacePublishError("change-policy forbid must be a string list", phase="policy")
    return ChangePolicy(
        base_artifact_relpath=_normalize_policy_path(base, field_name="base_artifact_relpath"),
        preserve=_parse_preserve_selectors(preserve),
        forbid=tuple(_normalize_unique_policy_paths(forbid, field_name="forbid")),
    )


def _parse_preserve_selectors(values: Sequence[str]) -> tuple[PolicySelector, ...]:
    seen: set[str] = set()
    selectors: list[PolicySelector] = []
    for value in values:
        path_part, separator, field_name = value.partition("#")
        path = _normalize_policy_path(path_part, field_name="preserve")
        if separator:
            if not field_name or "#" in field_name:
                raise WorkspacePublishError(
                    f"invalid preserve selector: {value}",
                    phase="policy",
                )
            selector = PolicySelector(path=path, json_field=field_name)
        else:
            selector = PolicySelector(path=path)
        key = f"{selector.path}#{selector.json_field or ''}"
        if key in seen:
            raise WorkspacePublishError(f"duplicate preserve entry: {value}", phase="policy")
        seen.add(key)
        selectors.append(selector)
    return tuple(selectors)


def _normalize_unique_policy_paths(values: Sequence[str], *, field_name: str) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for value in values:
        path = _normalize_policy_path(value, field_name=field_name)
        if path in seen:
            raise WorkspacePublishError(f"duplicate {field_name} entry: {value}", phase="policy")
        seen.add(path)
        paths.append(path)
    return paths


def _normalize_policy_path(value: str, *, field_name: str) -> str:
    if not value or "\\" in value or "\x00" in value or value.startswith("/"):
        raise WorkspacePublishError(f"invalid {field_name} path: {value!r}", phase="policy")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise WorkspacePublishError(f"invalid {field_name} path: {value!r}", phase="policy")
    return "/".join(parts)


def _enforce_change_policy(
    workspace: ExecutionWorkspace,
    candidates: Mapping[str, Path],
    policy: ChangePolicy | None,
) -> None:
    if policy is None:
        return
    base_root = workspace.input / "base-artifact" / policy.base_artifact_relpath
    _reject_policy_symlink(base_root, policy.base_artifact_relpath)
    for challenge_id, staging_root in candidates.items():
        for selector in policy.preserve:
            _enforce_preserve_selector(
                base_root,
                staging_root,
                selector,
                challenge_id=challenge_id,
            )
        for forbidden in policy.forbid:
            _enforce_forbid_prefix(
                base_root,
                staging_root,
                forbidden,
                challenge_id=challenge_id,
            )


def _enforce_preserve_selector(
    base_root: Path,
    staging_root: Path,
    selector: PolicySelector,
    *,
    challenge_id: str,
) -> None:
    base = base_root / selector.path
    staging = staging_root / selector.path
    _reject_policy_symlink(base, selector.path)
    _reject_policy_symlink(staging, selector.path)
    if selector.json_field is not None:
        base_value = _read_json_field(base, selector.json_field, selector.path)
        staging_value = _read_json_field(staging, selector.json_field, selector.path)
        if base_value != staging_value:
            raise WorkspacePublishError(
                f"preserve mismatch: {selector.path}#{selector.json_field}",
                phase="policy",
                claimed_id=challenge_id,
                path=selector.path,
            )
        return
    if _policy_file_hash(base, selector.path) != _policy_file_hash(
        staging,
        selector.path,
    ):
        raise WorkspacePublishError(
            f"preserve mismatch: {selector.path}",
            phase="policy",
            claimed_id=challenge_id,
            path=selector.path,
        )


def _enforce_forbid_prefix(
    base_root: Path,
    staging_root: Path,
    forbidden: str,
    *,
    challenge_id: str,
) -> None:
    staging_prefix = staging_root / forbidden
    if not staging_prefix.exists():
        return
    base_prefix = base_root / forbidden
    _reject_policy_symlink(staging_prefix, forbidden)
    _reject_policy_symlink(base_prefix, forbidden)
    for staging_path in [staging_prefix, *staging_prefix.rglob("*")]:
        relative = staging_path.relative_to(staging_root).as_posix()
        base_path = base_root / relative
        _reject_policy_symlink(staging_path, relative)
        _reject_policy_symlink(base_path, relative)
    if not base_path.exists():
        raise WorkspacePublishError(
            f"forbid newly added path: {forbidden}",
            phase="policy",
            claimed_id=challenge_id,
            path=relative,
        )


def _read_json_field(path: Path, field_name: str, selector: str) -> Any:
    value = read_json(path, None)
    if not isinstance(value, Mapping) or field_name not in value:
        raise WorkspacePublishError(
            f"missing JSON field for preserve entry: {selector}#{field_name}",
            phase="policy",
            path=selector,
        )
    return value[field_name]


def _policy_file_hash(path: Path, relative: str) -> str:
    _reject_policy_symlink(path, relative)
    if not path.is_file():
        raise WorkspacePublishError(
            f"preserve path must exist as a regular file: {relative}",
            phase="policy",
            path=relative,
        )
    return _file_hash(path)


def _reject_policy_symlink(path: Path, relative: str) -> None:
    if path.is_symlink():
        raise WorkspacePublishError(
            f"symlink is not allowed in change-policy path: {relative}",
            phase="policy",
            path=relative,
        )


def _manifest_input_hashes(manifest: Mapping[str, Any]) -> dict[str, str]:
    raw = manifest.get("input_hashes", {})
    if not isinstance(raw, Mapping):
        raise WorkspacePublishError("manifest input_hashes is malformed", phase="contract")
    hashes: dict[str, str] = {}
    for key, value in raw.items():
        relative = str(key)
        if relative in _CONTRACT_INPUT_HASH_EXCLUDED_PATHS:
            continue
        if relative.startswith("state/"):
            raise WorkspacePublishError(
                f"unexpected state input hash: {relative}",
                phase="contract",
                path=relative,
            )
        hashes[relative] = str(value)
    return hashes


def _manifest_projection_hash(manifest: Mapping[str, Any]) -> str:
    projection = {key: value for key, value in manifest.items() if key not in _MANIFEST_PROJECTION_FIELDS}
    encoded = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _verify_state_directory_for_contract(workspace: ExecutionWorkspace) -> None:
    workspace.state.mkdir(exist_ok=True)
    for entry in workspace.state.iterdir():
        if entry.name not in _STATE_FILES:
            raise WorkspacePublishError(
                f"unexpected publisher state path: {entry.name}",
                phase="contract",
                path=f"state/{entry.name}",
            )
        if entry.name == "publish-journal.json":
            raise WorkspacePublishError(
                "in-flight publish journal requires recovery before publication",
                phase="recovery",
                path="state/publish-journal.json",
            )


def _read_high_water(workspace: ExecutionWorkspace) -> dict[str, Any]:
    path = workspace.state / "highest-committed-generation.json"
    if not path.exists():
        return {}
    value = read_json(path, None)
    if not isinstance(value, dict):
        raise WorkspacePublishError("invalid highest-committed-generation.json", phase="contract")
    return value


def _next_generation(committed: Mapping[str, Any]) -> int:
    raw = committed.get("publish_generation", 0)
    if not isinstance(raw, int) or raw < 0:
        raise WorkspacePublishError("invalid committed publish generation", phase="contract")
    generation = raw + 1
    if generation <= raw:
        raise WorkspacePublishError("publish generation must increase", phase="manifest")
    return generation


def _update_manifest_and_high_water(
    workspace: ExecutionWorkspace,
    generation: int,
    output_hash: str,
) -> None:
    manifest = _read_manifest(workspace)
    manifest["publish_generation"] = generation
    manifest["output_manifest_hash"] = output_hash
    _write_atomic_json(workspace.manifest, manifest)
    _write_atomic_json(
        workspace.state / "highest-committed-generation.json",
        {
            "publish_generation": generation,
            "output_manifest_hash": output_hash,
        },
    )


def _write_publish_journal(
    workspace: ExecutionWorkspace,
    generation: int,
    output_hash: str,
    *,
    phase: str,
    category: str | None = None,
    entries: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "phase": phase,
        "publish_generation": generation,
        "output_manifest_hash": output_hash,
        "workspace_id": workspace.workspace_id,
    }
    if category is not None:
        payload["category"] = category
    if entries is not None:
        payload["entries"] = list(entries)
    else:
        existing = _read_optional_json(workspace.state / "publish-journal.json")
        if isinstance(existing, Mapping) and "entries" in existing:
            payload["entries"] = list(existing["entries"])
        if isinstance(existing, Mapping) and category is None and "category" in existing:
            payload["category"] = existing["category"]
    _write_atomic_json(workspace.state / "publish-journal.json", payload, fsync=True)


def _remove_publish_journal(workspace: ExecutionWorkspace) -> None:
    journal = workspace.state / "publish-journal.json"
    if journal.exists():
        journal.unlink()


def _write_atomic_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    fsync: bool = False,
) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    write_json(temporary, payload)
    if fsync:
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
    temporary.replace(path)
    if fsync:
        try:
            dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _rollback_published_batch(
    entries: Sequence[tuple[Path, Path | None, Path | None]],
) -> None:
    for destination, quarantined, predecessor in reversed(entries):
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        if quarantined is not None and predecessor is not None and quarantined.exists():
            quarantined.replace(predecessor)


def _output_manifest_hash(candidates: Mapping[str, Path]) -> str:
    records: list[tuple[str, str, str, str, str]] = []
    for challenge_id, root in candidates.items():
        for path in sorted([root, *root.rglob("*")], key=lambda item: item.relative_to(root).as_posix()):
            rel = path.relative_to(root).as_posix()
            stat = path.lstat()
            if path.is_dir():
                entry_type = "dir"
                content_hash = ""
            elif path.is_file():
                entry_type = "file"
                content_hash = _file_hash(path)
            else:
                raise WorkspacePublishError(
                    f"special file is not allowed: {rel}",
                    phase="allowlist",
                    claimed_id=challenge_id,
                    path=rel,
                )
            records.append(
                (
                    challenge_id,
                    rel,
                    entry_type,
                    str(stat.st_mode & 0o777),
                    content_hash,
                )
            )
    digest = hashlib.sha256()
    for record in records:
        digest.update(f"{len(record)}:".encode("ascii"))
        for field_value in record:
            encoded = field_value.encode("utf-8")
            digest.update(f"{len(encoded)}:".encode("ascii"))
            digest.update(encoded)
    return digest.hexdigest()


def _hash_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    if root.is_symlink() or not root.is_dir():
        raise WorkspacePublishError(f"{root.name} must be a directory", phase="contract")
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise WorkspacePublishError(f"symlink is not allowed: {relative}", phase="contract")
        if path.is_file():
            hashes[relative] = _file_hash(path)
    return hashes


def _file_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise WorkspacePublishError(f"expected regular file: {path.name}", phase="contract")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight_platform() -> None:
    if fcntl is None or not hasattr(fcntl, "flock"):
        raise WorkspacePublishError(
            "publisher requires POSIX fcntl.flock; non-POSIX host is unsupported",
            phase="contract",
        )


def _cross_check_resume_targets(
    candidates: Mapping[str, Path],
    contract: PublicationContract,
) -> None:
    targets = dict(contract.resume_output_targets or {})
    if not targets:
        return
    for cid, recorded in targets.items():
        observed = candidates.get(cid)
        if observed is None:
            raise WorkspacePublishError(
                f"resume target missing in staging: {cid}",
                phase="contract",
                claimed_id=cid,
                path=str(recorded),
            )
        observed_basename = observed.name
        recorded_basename = Path(str(recorded)).name
        if observed_basename != recorded_basename:
            raise WorkspacePublishError(
                f"resume target binding disagrees with staging: recorded={recorded} observed={observed_basename}",
                phase="contract",
                claimed_id=cid,
                path=observed_basename,
            )
    for cid in candidates:
        if cid in targets:
            continue
        # Unknown id with a target binding only fails when the binding
        # explicitly named that id; staging-only ids are checked by allowlist.


def _verify_same_filesystem(
    canonical_root: Path,
    workspace: ExecutionWorkspace,
    quarantine_root: Path,
) -> None:
    canonical_root.mkdir(parents=True, exist_ok=True)
    quarantine_root.mkdir(parents=True, exist_ok=True)
    workspace.output.mkdir(parents=True, exist_ok=True)
    try:
        canonical_dev = canonical_root.stat().st_dev
        quarantine_dev = quarantine_root.stat().st_dev
        workspace_dev = workspace.output.stat().st_dev
    except OSError as exc:
        raise WorkspacePublishError(
            f"could not stat publisher paths: {exc}",
            phase="commit",
        ) from exc
    if not (canonical_dev == quarantine_dev == workspace_dev):
        raise WorkspacePublishError(
            "canonical / quarantine / workspace.output must share one filesystem",
            phase="commit",
        )


def _write_terminal_marker(
    workspace: ExecutionWorkspace,
    *,
    status: str,
    output_hash: str,
) -> None:
    payload = {
        "status": status,
        "output_manifest_hash": output_hash,
        "wall_clock_seconds": time.time(),
    }
    _write_atomic_json(workspace.state / "publish-status.json", payload)


def _claimed_lock_digest(category: str, claimed_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(category.encode("utf-8"))
    digest.update(b"\0")
    digest.update(claimed_id.encode("utf-8"))
    return digest.hexdigest()


@contextmanager
def _acquire_publisher_locks(
    paths: ProjectPaths,
    category: str,
    challenge_ids: Sequence[str],
    *,
    timeout_seconds: int,
) -> Iterator[None]:
    lock_root = paths.build_publisher_locks
    try:
        lock_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspacePublishError(
            f"publisher lock root unavailable: {exc}",
            phase="commit",
        ) from exc
    sorted_ids = sorted(set(challenge_ids))
    fds: list[int] = []
    held_paths: list[Path] = []
    try:
        for cid in sorted_ids:
            lock_path = lock_root / _claimed_lock_digest(category, cid)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                _flock_with_timeout(fd, timeout_seconds, lock_path)
            except BaseException:
                os.close(fd)
                raise
            fds.append(fd)
            held_paths.append(lock_path)
        yield
    finally:
        for fd in fds:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[union-attr]
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass


def _flock_with_timeout(fd: int, timeout_seconds: int, lock_path: Path) -> None:
    assert fcntl is not None
    deadline = time.monotonic() + max(1, timeout_seconds)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise WorkspacePublishError(
                    f"publisher lock busy: {lock_path.name}",
                    phase="commit",
                    path=lock_path.name,
                ) from None
            time.sleep(0.1)


def _bootstrap_recover_journal(
    paths: ProjectPaths,
    workspace: ExecutionWorkspace,
) -> None:
    """Finalize or roll back an incomplete journal left by a prior process.

    Idempotent: callable repeatedly. Acquires the same publisher locks the
    original publish would have used, finalizes the high-water file when the
    journal recorded `committed`, otherwise rolls back the canonical batch.
    """
    journal_path = workspace.state / "publish-journal.json"
    if not journal_path.exists():
        return
    raw = read_json(journal_path, None)
    if not isinstance(raw, dict):
        raise WorkspacePublishError(
            "publish journal is malformed; manual recovery required",
            phase="recovery",
        )
    generation_value = raw.get("publish_generation")
    if not isinstance(generation_value, int):
        raise WorkspacePublishError(
            "in-flight publish journal requires recovery before publication (missing generation)",
            phase="recovery",
        )
    category = raw.get("category")
    if not isinstance(category, str):
        raise WorkspacePublishError(
            "in-flight publish journal requires recovery before publication (missing category)",
            phase="recovery",
        )
    entries = raw.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    claimed_ids = [
        entry.get("claimed_id")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("claimed_id"), str)
    ]
    committed = _read_high_water(workspace)
    committed_generation = committed.get("publish_generation") if isinstance(committed, Mapping) else 0
    if not isinstance(committed_generation, int):
        committed_generation = 0

    phase = raw.get("phase")
    output_hash = raw.get("output_manifest_hash")

    lock_timeout = _positive_env_int(
        "BUILD_PUBLISH_LOCK_TIMEOUT_SECONDS",
        _DEFAULT_LOCK_TIMEOUT_SECONDS,
    )

    if phase == "committed":
        # Crash window: canonical+manifest committed, high-water may lag.
        if generation_value > committed_generation:
            _write_atomic_json(
                workspace.state / "highest-committed-generation.json",
                {
                    "publish_generation": generation_value,
                    "output_manifest_hash": output_hash,
                },
            )
        journal_path.unlink()
        return

    if generation_value <= committed_generation:
        raise WorkspacePublishError(
            "publish journal generation older than high-water; manual review required",
            phase="recovery",
        )

    # Roll back: undo any canonical movement using the journal entries.
    canonical_root = paths.challenges / category
    rollback_entries: list[tuple[Path, Path | None, Path | None]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        destination_str = entry.get("canonical")
        if not isinstance(destination_str, str):
            continue
        destination = Path(destination_str)
        rollback_entries.append((destination, None, None))
    with _acquire_publisher_locks(
        paths,
        category,
        claimed_ids,
        timeout_seconds=lock_timeout,
    ):
        # Best-effort rollback: remove any temps for this workspace and any
        # newly-placed canonical directories the journal recorded.
        for entry in entries:
            if isinstance(entry, dict):
                temp_str = entry.get("temp")
                if isinstance(temp_str, str):
                    temp_path = Path(temp_str)
                    if temp_path.exists():
                        shutil.rmtree(temp_path, ignore_errors=True)
                canonical_str = entry.get("canonical")
                if isinstance(canonical_str, str):
                    canonical_path = Path(canonical_str)
                    if canonical_path.exists():
                        if canonical_path.is_dir():
                            shutil.rmtree(canonical_path)
                        else:
                            canonical_path.unlink()
        # Restore quarantined predecessors when the canonical slot is empty.
        quarantine_root = workspace.root / "quarantine" / category
        if quarantine_root.exists():
            for child in quarantine_root.iterdir():
                if not child.is_dir():
                    continue
                target = canonical_root / child.name
                if not target.exists():
                    try:
                        child.replace(target)
                    except OSError:
                        # Leave the predecessor in quarantine; operator inspection.
                        pass
        journal_path.unlink()


_DEFAULT_RETENTION_DAYS = 7
_DEFAULT_RETENTION_MAX_ROOTS = 20
_DEFAULT_SWEEP_INTERVAL_SECONDS = 60
_LAST_SWEEP_AT: dict[str, float] = {"value": 0.0, "pending": 0.0}


def _run_retention_sweep(paths: ProjectPaths) -> None:
    """Opportunistic retention sweep with per-process throttle.

    Errors are warnings-only (logged) and SHALL NOT propagate to the caller.
    Throttle suppresses calls within ``BUILD_PUBLISH_SWEEP_INTERVAL_SECONDS``;
    a suppressed call sets a ``pending`` flag so the next eligible call runs
    the sweep.
    """
    try:
        interval = _positive_env_int(
            "BUILD_PUBLISH_SWEEP_INTERVAL_SECONDS",
            _DEFAULT_SWEEP_INTERVAL_SECONDS,
        )
    except WorkspacePublishError:
        return
    now = time.monotonic()
    last = _LAST_SWEEP_AT["value"]
    if last and (now - last) < interval and not _LAST_SWEEP_AT["pending"]:
        _LAST_SWEEP_AT["pending"] = now
        return
    _LAST_SWEEP_AT["value"] = now
    _LAST_SWEEP_AT["pending"] = 0.0
    try:
        _sweep_retention_roots(paths)
    except Exception:  # noqa: BLE001 - sweep MUST NOT block publish result
        import logging

        logging.getLogger(__name__).warning("publisher retention sweep failed", exc_info=True)


def _sweep_retention_roots(paths: ProjectPaths) -> None:
    executions_root = paths.executions
    if not executions_root.is_dir():
        return
    candidates: list[tuple[float, Path]] = []
    for workspace_dir in executions_root.iterdir():
        if not workspace_dir.is_dir():
            continue
        if _workspace_has_active_journal(workspace_dir):
            continue
        marker = _read_terminal_marker(workspace_dir)
        if marker is None:
            continue
        timestamp = marker.get("wall_clock_seconds")
        if not isinstance(timestamp, (int, float)):
            continue
        if not _workspace_has_retained_artifacts(workspace_dir):
            continue
        if not _can_acquire_workspace_publisher_locks(paths, workspace_dir):
            continue
        candidates.append((float(timestamp), workspace_dir))

    now_wall = time.time()
    age_cutoff = now_wall - _DEFAULT_RETENTION_DAYS * 86400
    fresh: list[tuple[float, Path]] = []
    for timestamp, workspace_dir in candidates:
        if timestamp < age_cutoff:
            _purge_retained_artifacts(workspace_dir)
        else:
            fresh.append((timestamp, workspace_dir))

    if len(fresh) > _DEFAULT_RETENTION_MAX_ROOTS:
        fresh.sort(key=lambda item: item[0])
        evictable = fresh[: len(fresh) - _DEFAULT_RETENTION_MAX_ROOTS]
        for _, workspace_dir in evictable:
            _purge_retained_artifacts(workspace_dir)


def _workspace_has_active_journal(workspace_dir: Path) -> bool:
    return any(
        (active / "state" / "publish-journal.json").exists()
        for active in _workspace_active_roots(workspace_dir)
    )


def _read_terminal_marker(workspace_dir: Path) -> Mapping[str, Any] | None:
    for active in _workspace_active_roots(workspace_dir):
        path = active / "state" / "publish-status.json"
        if path.exists():
            value = read_json(path, None)
            return value if isinstance(value, Mapping) else None
    return None


def _workspace_has_retained_artifacts(workspace_dir: Path) -> bool:
    quarantine = workspace_dir / "quarantine"
    if quarantine.is_dir():
        for category in quarantine.iterdir():
            if category.is_dir() and any(category.iterdir()):
                return True
    marker = _read_terminal_marker(workspace_dir)
    status = marker.get("status") if isinstance(marker, Mapping) else None
    if status == "failed":
        for active in _workspace_active_roots(workspace_dir):
            for relative in ("output", "logs"):
                target = active / relative
                if target.is_dir() and any(target.iterdir()):
                    return True
        attempts = workspace_dir / "attempts"
        if attempts.is_dir() and any(attempts.iterdir()):
            return True
    return False


def _can_acquire_workspace_publisher_locks(
    paths: ProjectPaths,
    workspace_dir: Path,
) -> bool:
    """Best-effort non-blocking lock acquisition check.

    Without a journal we assume no per-id lock is held (the workspace is
    terminal). When a journal is present we try every (category, claimed_id)
    pair non-blockingly; any held lock skips the workspace.
    """
    journal_path = next(
        (
            active / "state" / "publish-journal.json"
            for active in _workspace_active_roots(workspace_dir)
            if (active / "state" / "publish-journal.json").exists()
        ),
        None,
    )
    if journal_path is None:
        return True
    raw = read_json(journal_path, None)
    if not isinstance(raw, dict):
        return True
    category = raw.get("category") if isinstance(raw.get("category"), str) else None
    entries = raw.get("entries") if isinstance(raw.get("entries"), list) else []
    claimed_ids = [
        entry.get("claimed_id")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("claimed_id"), str)
    ]
    if not category or not claimed_ids:
        return False
    if fcntl is None:
        return False
    lock_root = paths.build_publisher_locks
    for cid in claimed_ids:
        lock_path = lock_root / _claimed_lock_digest(category, cid)
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    return True


def _purge_retained_artifacts(workspace_dir: Path) -> None:
    targets = [workspace_dir / "quarantine"]
    for active in _workspace_active_roots(workspace_dir):
        targets.extend((active / "output", active / "logs"))
    attempts = workspace_dir / "attempts"
    if attempts.is_dir():
        for iteration in attempts.iterdir():
            if iteration.is_dir():
                targets.extend((iteration / "output", iteration / "logs"))
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


def _workspace_active_roots(workspace_dir: Path) -> tuple[Path, ...]:
    """Return two-layer active root first, with legacy root compatibility."""
    current = workspace_dir / "current"
    if current.is_dir():
        return (current, workspace_dir)
    return (workspace_dir,)
