"""Host-side final artifact evidence for Pwn solver repairs."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from core.jsonio import read_json, write_json

PWN_FINAL_ARTIFACT_REL = "attachments/vuln"
PWN_FINAL_ARTIFACT_PROMPT_PATH = "./attachments/vuln"
PWN_KEY_SYMBOLS = ("win", "main", "vuln", "setup_fake_stack", "fake_stack")
_BINARY_SHA_RE = re.compile(
    r"(?m)^(?P<prefix>\s*BINARY_SHA256\s*=\s*)[\"'][^\"']*[\"']"
)


class PwnArtifactEvidenceError(ValueError):
    """Raised when final Pwn artifact evidence cannot be trusted."""


def final_pwn_artifact_evidence(challenge_dir: Path) -> dict[str, Any] | None:
    """Return final solver evidence derived only from the player attachment."""

    metadata = read_json(challenge_dir / "metadata.json", {})
    if not isinstance(metadata, dict) or metadata.get("category") != "pwn":
        return None
    artifact_rel = PWN_FINAL_ARTIFACT_REL
    artifact_prompt_path = PWN_FINAL_ARTIFACT_PROMPT_PATH
    artifact = challenge_dir / artifact_rel
    deploy_src = challenge_dir / "deploy" / "src" / "vuln"
    deploy_src_sha = (
        _sha256_file(deploy_src)
        if deploy_src.is_file() and not deploy_src.is_symlink()
        else None
    )
    if not artifact.is_file() or artifact.is_symlink():
        return {
            "path": artifact_prompt_path,
            "available": False,
            "metadata_artifact": metadata.get("artifact"),
            "metadata_artifact_sha256": metadata.get("artifact_sha256"),
            "deploy_src_vuln_sha256": deploy_src_sha,
            "error": f"{artifact_rel} missing",
        }
    artifact_sha = _sha256_file(artifact)
    return {
        "path": artifact_prompt_path,
        "available": True,
        "sha256": artifact_sha,
        "metadata_artifact": metadata.get("artifact"),
        "metadata_artifact_sha256": metadata.get("artifact_sha256"),
        "symbols": _readelf_symbols(artifact),
        "deploy_src_vuln_sha256": deploy_src_sha,
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
    deploy_sha = evidence.get("deploy_src_vuln_sha256") or "(unavailable)"
    return "\n".join(
        [
            "FINAL SOLVER EVIDENCE SOURCE:",
            "Use only ./attachments/vuln for exp.py and pwn_debug_report.json.",
            "Do not use deploy/src/vuln for solver offsets, symbols, gadgets, or report sha.",
            "BINARY_SHA256 in exp.py is mandatory and must equal metadata.artifact_sha256.",
            "pwn_debug_report.json is host-generated from attachments/vuln; do not hand-edit binary.sha256.",
            f"- artifact path: {artifact_path}",
            f"- artifact available: {available}",
            f"- {rel_artifact_path} sha256: {sha}",
            f"- metadata.artifact_sha256: {metadata_sha}",
            f"- key symbols from {rel_artifact_path}:",
            *symbol_lines,
            f"- deploy/src/vuln sha256: {deploy_sha} (UNTRUSTED / DO NOT USE)",
        ]
    )


def ensure_pwn_solver_evidence(challenge_dir: Path) -> tuple[str, ...]:
    """Ensure host-owned Pwn solver evidence is bound to the final attachment."""

    metadata_path = challenge_dir / "metadata.json"
    metadata = read_json(metadata_path, {})
    if not isinstance(metadata, dict) or metadata.get("category") != "pwn":
        return ()
    if metadata.get("artifact") != PWN_FINAL_ARTIFACT_REL:
        return ()

    artifact_path = challenge_dir / PWN_FINAL_ARTIFACT_REL
    if not artifact_path.is_file() or artifact_path.is_symlink():
        raise PwnArtifactEvidenceError(f"{PWN_FINAL_ARTIFACT_REL} missing")

    actions: list[str] = []
    if _ensure_host_readable(artifact_path):
        actions.append("made attachments/vuln readable for host validation")
    artifact_sha = _sha256_file(artifact_path)
    if metadata.get("artifact_sha256") != artifact_sha:
        metadata["artifact_sha256"] = artifact_sha
        write_json(metadata_path, metadata)
        actions.append("updated metadata.artifact_sha256 from attachments/vuln")

    report_path = _write_pwn_debug_report(
        challenge_dir,
        artifact_rel=PWN_FINAL_ARTIFACT_REL,
        artifact_path=artifact_path,
        artifact_sha=artifact_sha,
    )
    actions.append(f"refreshed {report_path.relative_to(challenge_dir).as_posix()}")

    exp_path = challenge_dir / "writenup" / "exp.py"
    if exp_path.is_file() and not exp_path.is_symlink():
        if _ensure_exp_binary_sha(exp_path, artifact_sha):
            actions.append("updated writenup/exp.py BINARY_SHA256")
    return tuple(actions)


def _ensure_host_readable(path: Path) -> bool:
    try:
        current_mode = path.stat().st_mode
    except OSError as exc:
        raise PwnArtifactEvidenceError(f"cannot stat {PWN_FINAL_ARTIFACT_REL}: {exc}") from exc
    desired_mode = current_mode | 0o444
    if desired_mode == current_mode:
        return False
    try:
        os.chmod(path, desired_mode)
    except OSError as exc:
        raise PwnArtifactEvidenceError(
            f"cannot make {PWN_FINAL_ARTIFACT_REL} readable: {exc}"
        ) from exc
    return True


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
    return _write_pwn_debug_report(
        challenge_dir,
        artifact_rel=artifact_rel,
        artifact_path=artifact_path,
        artifact_sha=artifact_sha,
    )


def _write_pwn_debug_report(
    challenge_dir: Path,
    *,
    artifact_rel: str,
    artifact_path: Path,
    artifact_sha: str,
) -> Path:
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


def _ensure_exp_binary_sha(exp_path: Path, artifact_sha: str) -> bool:
    text = exp_path.read_text(encoding="utf-8", errors="replace")
    replacement = rf'\g<prefix>"{artifact_sha}"'
    new_text, count = _BINARY_SHA_RE.subn(replacement, text, count=1)
    if count == 0:
        insert_at = _binary_sha_insert_offset(text)
        line = f'BINARY_SHA256 = "{artifact_sha}"\n'
        new_text = text[:insert_at] + line + text[insert_at:]
    if new_text == text:
        return False
    exp_path.write_text(new_text, encoding="utf-8")
    return True


def _binary_sha_insert_offset(text: str) -> int:
    try:
        module = ast.parse(text)
    except (SyntaxError, ValueError):
        return _after_header_comments_offset(text)

    insert_line = 1
    body = list(module.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        insert_line = getattr(body[0], "end_lineno", body[0].lineno) + 1
        body = body[1:]
    for node in body:
        if not isinstance(node, ast.ImportFrom) or node.module != "__future__":
            break
        insert_line = getattr(node, "end_lineno", node.lineno) + 1

    lines = text.splitlines(keepends=True)
    if not lines:
        return 0
    if lines[0].startswith("#!"):
        insert_line = max(insert_line, 2)
    for idx, line in enumerate(lines[:2], start=1):
        if "coding" in line and line.lstrip().startswith("#"):
            insert_line = max(insert_line, idx + 1)
    return sum(len(line) for line in lines[: max(0, insert_line - 1)])


def _after_header_comments_offset(text: str) -> int:
    lines = text.splitlines(keepends=True)
    index = 0
    if index < len(lines) and lines[index].startswith("#!"):
        index += 1
    if (
        index < len(lines)
        and "coding" in lines[index]
        and lines[index].lstrip().startswith("#")
    ):
        index += 1
    return sum(len(line) for line in lines[:index])


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
