"""Host-side final artifact evidence for Pwn solver repairs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from core.jsonio import read_json

PWN_FINAL_ARTIFACT_REL = "attachments/vuln"
PWN_FINAL_ARTIFACT_PROMPT_PATH = "./attachments/vuln"
PWN_KEY_SYMBOLS = ("win", "main", "vuln", "setup_fake_stack", "fake_stack")


class PwnArtifactEvidenceError(ValueError):
    """Raised when final Pwn artifact evidence cannot be trusted."""


def final_pwn_artifact_evidence(challenge_dir: Path) -> dict[str, Any] | None:
    """Return final solver evidence derived only from the player attachment."""

    metadata = read_json(challenge_dir / "metadata.json", {})
    if not isinstance(metadata, dict) or metadata.get("category") != "pwn":
        return None
    artifact_rel = _pwn_final_artifact_rel(metadata)
    artifact_prompt_path = f"./{artifact_rel}"
    artifact = challenge_dir / artifact_rel
    if not artifact.is_file() or artifact.is_symlink():
        return {
            "path": artifact_prompt_path,
            "available": False,
            "metadata_artifact_sha256": metadata.get("artifact_sha256"),
            "error": f"{artifact_rel} missing",
        }
    return {
        "path": artifact_prompt_path,
        "available": True,
        "sha256": _sha256_file(artifact),
        "metadata_artifact_sha256": metadata.get("artifact_sha256"),
        "symbols": _readelf_symbols(artifact),
    }


def final_pwn_artifact_prompt_block(challenge_dir: Path) -> str:
    """Render repair/validation-debug instructions for final Pwn evidence."""

    evidence = final_pwn_artifact_evidence(challenge_dir)
    if evidence is None:
        return ""
    symbols = evidence.get("symbols")
    if isinstance(symbols, dict) and symbols:
        symbol_lines = [
            f"  - {name}: {symbols[name]}"
            for name in PWN_KEY_SYMBOLS
            if isinstance(symbols.get(name), str)
        ]
    else:
        symbol_lines = ["  - (unavailable)"]
    available = "yes" if evidence.get("available") else "no"
    sha = evidence.get("sha256") or "(unavailable)"
    metadata_sha = evidence.get("metadata_artifact_sha256") or "(unavailable)"
    artifact_path = str(evidence.get("path") or PWN_FINAL_ARTIFACT_PROMPT_PATH)
    rel_artifact_path = artifact_path[2:] if artifact_path.startswith("./") else artifact_path
    return "\n".join(
        [
            "FINAL SOLVER EVIDENCE SOURCE:",
            f"Use only {artifact_path} for exp.py and pwn_debug_report.json.",
            "Do not use deploy/src binaries for solver offsets, symbols, gadgets, or report sha.",
            f"- artifact path: {artifact_path}",
            f"- artifact available: {available}",
            f"- {rel_artifact_path} sha256: {sha}",
            f"- metadata.artifact_sha256: {metadata_sha}",
            f"- key symbols from {rel_artifact_path}:",
            *symbol_lines,
            "- deploy/src is an untrusted build intermediate for solver evidence.",
        ]
    )


def refresh_pwn_debug_report(challenge_dir: Path) -> Path | None:
    """Rewrite writenup/pwn_debug_report.json from the final player attachment only."""

    metadata = read_json(challenge_dir / "metadata.json", {})
    if not isinstance(metadata, dict) or metadata.get("category") != "pwn":
        return None
    artifact_rel = _pwn_final_artifact_rel(metadata)
    artifact_path = challenge_dir / artifact_rel
    if not artifact_path.is_file() or artifact_path.is_symlink():
        raise PwnArtifactEvidenceError(f"{artifact_rel} missing")
    artifact_sha = _sha256_file(artifact_path)
    metadata_sha = metadata.get("artifact_sha256")
    if metadata_sha != artifact_sha:
        raise PwnArtifactEvidenceError(
            f"metadata.artifact_sha256 does not match {artifact_rel}"
        )
    report = {
        "binary": {
            "path": artifact_rel,
            "sha256": artifact_sha,
            "source": "final_artifact",
        },
        "symbols": _readelf_symbols(artifact_path),
        "checksec": _checksec(artifact_path),
    }
    report_path = challenge_dir / "writenup" / "pwn_debug_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report_path


def _pwn_final_artifact_rel(metadata: dict[str, Any]) -> str:
    artifact = metadata.get("artifact")
    if isinstance(artifact, str) and artifact.startswith("attachments/") and ".." not in Path(artifact).parts:
        return artifact
    return PWN_FINAL_ARTIFACT_REL


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readelf_symbols(path: Path) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["readelf", "-sW", str(path)],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0:
        return {}
    symbols: dict[str, str] = {}
    wanted = set(PWN_KEY_SYMBOLS)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 8:
            continue
        name = parts[7].split("@", 1)[0]
        if name not in wanted:
            continue
        try:
            address = int(parts[1], 16)
        except ValueError:
            continue
        symbols[name] = f"0x{address:x}"
    return symbols


def _checksec(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["checksec", "--file", str(path), "--output", "json"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout.strip()[:2000]}
    return parsed if isinstance(parsed, dict) else {}
