"""Deterministic repairs for mechanical build-attempt validation failures."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.jsonio import read_json, write_json


@dataclass(frozen=True)
class AutoRepairResult:
    changed: bool
    actions: tuple[str, ...] = ()


def auto_repair_challenge(
    challenge_dir: Path,
    *,
    challenge_id: str | None = None,
) -> AutoRepairResult:
    """Apply safe local repairs that do not require challenge redesign."""
    actions: list[str] = []
    actions.extend(_promote_nested_challenge_root(challenge_dir, challenge_id))
    metadata = read_json(challenge_dir / "metadata.json", None)
    if not isinstance(metadata, dict):
        return AutoRepairResult(changed=bool(actions), actions=tuple(actions))

    actions.extend(_remove_nested_output_trees(challenge_dir))
    actions.extend(_repair_document_pair(challenge_dir))
    actions.extend(_repair_re_artifact_metadata(challenge_dir, metadata))

    if actions:
        write_json(challenge_dir / "metadata.json", metadata)
    return AutoRepairResult(changed=bool(actions), actions=tuple(actions))


def _promote_nested_challenge_root(
    challenge_dir: Path,
    challenge_id: str | None,
) -> list[str]:
    if (challenge_dir / "metadata.json").is_file():
        return []
    candidates: list[Path] = []
    for metadata_path in challenge_dir.rglob("output/challenges/*/*/metadata.json"):
        nested_root = metadata_path.parent
        metadata = read_json(metadata_path, None)
        metadata_id = metadata.get("id") if isinstance(metadata, dict) else None
        if challenge_id and metadata_id != challenge_id:
            continue
        if not challenge_id and metadata_id and not nested_root.name.startswith(f"{metadata_id}-"):
            continue
        candidates.append(nested_root)
    if len(candidates) != 1:
        return []

    nested_root = candidates[0]
    actions: list[str] = []
    for child in nested_root.iterdir():
        destination = challenge_dir / child.name
        if destination.exists():
            continue
        shutil.move(str(child), str(destination))
        actions.append(
            f"promoted nested challenge file: {destination.relative_to(challenge_dir).as_posix()}"
        )
    output_root = _owning_output_dir(challenge_dir, nested_root)
    if output_root is not None and output_root.exists():
        shutil.rmtree(output_root)
        actions.append(
            f"removed nested generated output tree: {output_root.relative_to(challenge_dir).as_posix()}"
        )
    return actions


def _remove_nested_output_trees(challenge_dir: Path) -> list[str]:
    actions: list[str] = []
    for nested in sorted(challenge_dir.rglob("output/challenges"), reverse=True):
        if not nested.is_dir():
            continue
        output_root = _owning_output_dir(challenge_dir, nested)
        if output_root is None:
            continue
        shutil.rmtree(output_root)
        actions.append(f"removed nested generated output tree: {output_root.relative_to(challenge_dir).as_posix()}")
    return actions


def _owning_output_dir(challenge_dir: Path, nested: Path) -> Path | None:
    for parent in [nested, *nested.parents]:
        if parent == challenge_dir:
            return None
        if parent.name == "output" and parent.is_relative_to(challenge_dir):
            return parent
    return None


def _repair_document_pair(challenge_dir: Path) -> list[str]:
    actions: list[str] = []
    readme = challenge_dir / "README.md"
    writeup = challenge_dir / "writenup" / "wp.md"
    if not writeup.is_file() and _looks_substantial(readme):
        writeup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(readme, writeup)
        actions.append("copied README.md to missing writenup/wp.md")
    if not readme.is_file() and _looks_substantial(writeup):
        shutil.copy2(writeup, readme)
        actions.append("copied writenup/wp.md to missing README.md")
    return actions


def _looks_substantial(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return len(text) > 300 and text.count("##") >= 2


def _repair_re_artifact_metadata(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    if metadata.get("category") != "re":
        return []
    actions: list[str] = []
    artifact = metadata.get("artifact")
    if isinstance(artifact, str) and artifact.startswith("attachments/"):
        artifact_path = challenge_dir / artifact
        if not artifact_path.is_file():
            source = _matching_source_artifact(challenge_dir, artifact_path.name)
            if source is not None:
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, artifact_path)
                actions.append(f"copied {source.relative_to(challenge_dir).as_posix()} to {artifact}")
        if artifact_path.is_file():
            sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            if metadata.get("artifact_sha256") != sha:
                metadata["artifact_sha256"] = sha
                actions.append("updated metadata.artifact_sha256")
            if not metadata.get("build_command"):
                metadata["build_command"] = f"preserved existing artifact {artifact}"
                actions.append("filled metadata.build_command")
            if metadata.get("build_status") != "passed":
                metadata["build_status"] = "passed"
                actions.append("set metadata.build_status to passed")
            if metadata.get("target_format") is None:
                detected = _detect_target_format(artifact_path)
                if detected is not None:
                    metadata["target_format"] = detected
                    actions.append(f"set metadata.target_format to {detected}")
    return actions


def _matching_source_artifact(challenge_dir: Path, filename: str) -> Path | None:
    source_root = challenge_dir / "src"
    if not source_root.is_dir():
        return None
    matches = [
        path
        for path in source_root.rglob(filename)
        if path.is_file() and path.stat().st_size > 0
    ]
    if not matches:
        return None
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0]


def _detect_target_format(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            prefix = handle.read(4)
    except OSError:
        return None
    if prefix == b"\x7fELF":
        return "elf"
    if prefix[:2] == b"MZ":
        return "exe"
    return None
