"""Publisher-owned boundary from execution workspace output to canonical challenges."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.jsonio import read_json, write_json
from core.paths import ProjectPaths
from hermes.workspace import (
    ExecutionWorkspace,
    WorkspacePromotionError,
    _match_claimed_id,
    _matching_directories,
    _validate_challenges,
)

_MANIFEST_PROJECTION_FIELDS = {"output_manifest_hash", "publish_generation"}
_STATE_FILES = {
    "publish-journal.json",
    "publish-status.json",
    "highest-committed-generation.json",
}
_CONTRACT_INPUT_HASH_EXCLUDED_PATHS = {f"state/{name}" for name in _STATE_FILES}
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_MAX_FILES = 50_000
_DEFAULT_MAX_DEPTH = 64
_DEFAULT_MAX_COMPONENT_BYTES = 255
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
    _verify_contract(workspace, contract)
    candidates = _collect_candidates(workspace, contract)
    limits = _publisher_limits_from_env()
    _enforce_output_limits(candidates, limits)
    _enforce_change_policy(workspace, candidates, contract.change_policy)
    staged_hash = _output_manifest_hash(candidates)
    committed = _read_high_water(workspace)
    if committed and committed.get("output_manifest_hash") == staged_hash:
        return PublishResult(
            published_paths=[],
            quarantined=[],
            output_manifest_hash=staged_hash,
            outcome="noop",
        )

    canonical_root = paths.challenges / contract.category
    canonical_root.mkdir(parents=True, exist_ok=True)
    quarantine_root = workspace.root / "quarantine" / contract.category
    published: list[Path] = []
    quarantined: list[Path] = []
    published_by_id: dict[str, Path] = {}
    rollback_entries: list[tuple[Path, Path | None, Path | None]] = []
    generation = _next_generation(committed)
    _write_publish_journal(workspace, generation, staged_hash, phase="stage")

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
            temporary = canonical_root / (
                f".workspace-{workspace.workspace_id}-{uuid.uuid4().hex}"
            )
            shutil.copytree(source, temporary, symlinks=True)
            _enforce_output_limits({challenge_id: temporary}, limits)
            quarantined_path: Path | None = None
            predecessor_path: Path | None = existing[0] if existing else None
            try:
                if existing:
                    quarantine_root.mkdir(parents=True, exist_ok=True)
                    quarantined_path = quarantine_root / existing[0].name
                    if quarantined_path.exists():
                        quarantined_path = quarantine_root / (
                            f"{existing[0].name}.repair-{uuid.uuid4().hex}"
                        )
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
                temporary.replace(destination)
                published.append(destination)
                published_by_id[challenge_id] = destination
                rollback_entries.append((destination, quarantined_path, predecessor_path))
            except BaseException:
                if temporary.exists():
                    shutil.rmtree(temporary)
                if (
                    quarantined_path is not None
                    and quarantined_path.exists()
                    and existing
                    and not existing[0].exists()
                ):
                    quarantined_path.replace(existing[0])
                raise
        _write_publish_journal(workspace, generation, staged_hash, phase="manifest")
        canonical_hash = _output_manifest_hash(published_by_id)
        if canonical_hash != staged_hash:
            raise WorkspacePublishError(
                "canonical output hash mismatch after publish",
                phase="manifest",
            )
        _update_manifest_and_high_water(workspace, generation, staged_hash)
    except BaseException:
        _write_publish_journal(workspace, generation, staged_hash, phase="rollback")
        _rollback_published_batch(rollback_entries)
        raise
    else:
        _remove_publish_journal(workspace)
        return PublishResult(
            published_paths=published,
            quarantined=quarantined,
            output_manifest_hash=staged_hash,
        )


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
    projection = {
        key: value
        for key, value in manifest.items()
        if key not in _MANIFEST_PROJECTION_FIELDS
    }
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
) -> None:
    _write_atomic_json(
        workspace.state / "publish-journal.json",
        {
            "phase": phase,
            "publish_generation": generation,
            "output_manifest_hash": output_hash,
        },
    )


def _remove_publish_journal(workspace: ExecutionWorkspace) -> None:
    journal = workspace.state / "publish-journal.json"
    if journal.exists():
        journal.unlink()


def _write_atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    write_json(temporary, payload)
    temporary.replace(path)


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
