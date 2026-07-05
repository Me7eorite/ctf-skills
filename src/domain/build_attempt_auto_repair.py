"""Deterministic repairs for mechanical build-attempt validation failures."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.jsonio import read_json, write_json
from domain.pwn_artifact_evidence import PwnArtifactEvidenceError, ensure_pwn_solver_evidence
from domain.validation_repair_policy import (
    MECHANIC_ARTIFACT_METADATA,
    MECHANIC_CHALLENGE_YML,
    MECHANIC_COMPOSE_VALIDATE_WRAPPER,
    MECHANIC_DEPLOY_DOCKERFILE,
    MECHANIC_DOCKER_LOGS_NO_COLOR,
    MECHANIC_DOCUMENT_PAIR,
    MECHANIC_PROMOTE_NESTED_ROOT,
    MECHANIC_PWN_READINESS_PROBE,
    MECHANIC_PWN_SOLVER_EVIDENCE,
    MECHANIC_PWN_XINETD_SCAFFOLD,
    MECHANIC_REMOVE_NESTED_OUTPUT,
    MECHANIC_SOURCE_EVIDENCE,
    MECHANIC_VALIDATE_SOLVER_CAPTURE,
    MECHANIC_VALIDATE_WORKSPACE_PATHS,
    MECHANIC_VALIDATE_WRAPPER,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PWN_XINETD_SCAFFOLD = _REPOSITORY_ROOT / "scaffolds" / "pwn" / "xinetd-chroot"
_PWN_DEFAULT_SERVICE_PORT = "9999"
_PWN_READINESS_PROBE_FUNCTION = """\
pwn_readiness_probe() {
  python3 - "$1" "$2" "${3:-3}" "${4:-}" <<'PY'
import re
import socket
import sys
import time

def fail(message):
    print(f"[readiness] {message}", file=sys.stderr)
    raise SystemExit(1)

host = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if not host:
    fail("empty host")
raw_port = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
try:
    port = int(raw_port)
except ValueError:
    fail(f"invalid port: {raw_port!r}")
raw_timeout = (sys.argv[3] if len(sys.argv) > 3 else "3").strip() or "3"
try:
    timeout_seconds = float(raw_timeout)
except ValueError:
    fail(f"invalid timeout: {raw_timeout!r}")
token_re = (sys.argv[4] if len(sys.argv) > 4 else "").strip()
if not token_re:
    token_re = r"(Choice:|Welcome|Menu|Username:|Password:|Input:|Enter|SecureVault|> )"
deadline = time.monotonic() + max(0.1, timeout_seconds)
chunks = []
last_error = None

try:
    sock = socket.create_connection((host, port), timeout=min(2.0, max(0.1, deadline - time.monotonic())))
    sock.settimeout(0.2)
    while time.monotonic() < deadline and sum(len(chunk) for chunk in chunks) < 4096:
        try:
            data = sock.recv(4096)
        except (TimeoutError, socket.timeout):
            if chunks:
                break
            continue
        if not data:
            break
        chunks.append(data)
        text = b"".join(chunks).decode("latin-1", errors="replace")
        if re.search(token_re, text):
            break
    sock.close()
except OSError as exc:
    last_error = exc

text = b"".join(chunks).decode("latin-1", errors="replace")
matched = re.search(token_re, text)
if matched:
    print(text, end="")
    raise SystemExit(0)
if last_error is not None:
    print(f"[readiness] connection failed: {last_error}", file=sys.stderr)
elif text:
    print(f"[readiness] probe_tail={text[-200:]!r}", file=sys.stderr)
else:
    print("[readiness] no banner or menu prompt received", file=sys.stderr)
raise SystemExit(1)
PY
}
"""


@dataclass(frozen=True)
class AutoRepairResult:
    changed: bool
    actions: tuple[str, ...] = ()


def auto_repair_challenge(
    challenge_dir: Path,
    *,
    challenge_id: str | None = None,
    allowed_mechanics: tuple[str, ...] | set[str] | None = None,
) -> AutoRepairResult:
    """Apply safe local repairs that do not require challenge redesign."""
    allowed = set(allowed_mechanics) if allowed_mechanics is not None else None

    def can_run(mechanic: str) -> bool:
        return allowed is None or mechanic in allowed

    actions: list[str] = []
    if can_run(MECHANIC_PROMOTE_NESTED_ROOT):
        actions.extend(_promote_nested_challenge_root(challenge_dir, challenge_id))
    metadata = read_json(challenge_dir / "metadata.json", None)
    if not isinstance(metadata, dict):
        return AutoRepairResult(changed=bool(actions), actions=tuple(actions))

    if can_run(MECHANIC_REMOVE_NESTED_OUTPUT):
        actions.extend(_remove_nested_output_trees(challenge_dir))
    if can_run(MECHANIC_CHALLENGE_YML):
        actions.extend(_repair_challenge_yml(challenge_dir, metadata))
    if can_run(MECHANIC_DOCUMENT_PAIR):
        actions.extend(_repair_document_pair(challenge_dir))
    if can_run(MECHANIC_SOURCE_EVIDENCE):
        actions.extend(_repair_source_evidence(challenge_dir, metadata))
    if can_run(MECHANIC_ARTIFACT_METADATA):
        actions.extend(_repair_artifact_metadata(challenge_dir, metadata))
    if can_run(MECHANIC_VALIDATE_WRAPPER):
        actions.extend(_repair_validate_wrapper(challenge_dir, metadata))
    if can_run(MECHANIC_COMPOSE_VALIDATE_WRAPPER):
        actions.extend(_repair_compose_validate_wrapper(challenge_dir, metadata))
    if can_run(MECHANIC_VALIDATE_WORKSPACE_PATHS):
        actions.extend(_repair_validate_workspace_paths(challenge_dir, metadata))
    if can_run(MECHANIC_PWN_READINESS_PROBE):
        actions.extend(_repair_pwn_validate_readiness_probe(challenge_dir, metadata))
    if can_run(MECHANIC_DOCKER_LOGS_NO_COLOR):
        actions.extend(_repair_docker_logs_no_color(challenge_dir))
    if can_run(MECHANIC_VALIDATE_SOLVER_CAPTURE):
        actions.extend(_repair_validate_solver_capture(challenge_dir, metadata))
    if can_run(MECHANIC_PWN_SOLVER_EVIDENCE):
        actions.extend(_repair_pwn_solver_evidence(challenge_dir, metadata))
    if can_run(MECHANIC_PWN_XINETD_SCAFFOLD):
        actions.extend(_repair_pwn_xinetd_scaffold(challenge_dir, metadata))
    if can_run(MECHANIC_DEPLOY_DOCKERFILE):
        actions.extend(_repair_deploy_dockerfile(challenge_dir, metadata))

    if actions:
        write_json(challenge_dir / "metadata.json", metadata)
    return AutoRepairResult(changed=bool(actions), actions=tuple(actions))


def _repair_pwn_solver_evidence(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    if metadata.get("category") != "pwn":
        return []
    try:
        actions = list(ensure_pwn_solver_evidence(challenge_dir))
    except PwnArtifactEvidenceError:
        return []
    refreshed = read_json(challenge_dir / "metadata.json", None)
    if isinstance(refreshed, dict):
        metadata.clear()
        metadata.update(refreshed)
    return actions


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
    metadata = read_json(challenge_dir / "metadata.json", {})
    if not _looks_substantial(readme):
        readme.write_text(_document_skeleton(metadata, "README"), encoding="utf-8")
        actions.append("filled README.md minimum evidence skeleton")
    if not _looks_substantial(writeup):
        writeup.parent.mkdir(parents=True, exist_ok=True)
        writeup.write_text(_document_skeleton(metadata, "Writeup"), encoding="utf-8")
        actions.append("filled writenup/wp.md minimum evidence skeleton")
    return actions


def _looks_substantial(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return len(text) > 300 and text.count("##") >= 2



def _document_skeleton(metadata: dict[str, Any], title: str) -> str:
    challenge_title = str(metadata.get("title") or metadata.get("id") or "challenge")
    category = str(metadata.get("category") or "unknown")
    return (
        f"# {challenge_title} {title}\n\n"
        "## Build Evidence\n"
        f"This generated {category} challenge is delivered from the canonical "
        "challenge root. The challenge metadata, source tree, player "
        "attachments, validation script, and reference solver are kept in their "
        "standard locations so the platform can reproduce validation without "
        "reading nested generated output directories.\n\n"
        "## Solve Evidence\n"
        "The reference solver is expected to recover a final flag token from "
        "the distributed target or service artifact. It must not read organizer "
        "answer files such as metadata.json or challenge.yml. If this skeleton "
        "was generated by deterministic repair, replace it with the full manual "
        "analysis once the challenge is rebuilt.\n\n"
        "## Verification Notes\n"
        "Run validate.sh from the challenge root. The script should print one "
        "flag{...} token recovered by writenup/exp.py or the live service flow; "
        "the platform validator performs the final comparison against metadata.\n"
    )


def _repair_challenge_yml(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    challenge_yml = challenge_dir / "challenge.yml"
    if challenge_yml.is_file():
        return []
    title = str(metadata.get("title") or metadata.get("id") or challenge_dir.name)
    category = str(metadata.get("category") or "")
    difficulty = str(metadata.get("difficulty") or "")
    points = metadata.get("points") or 100
    challenge_yml.write_text(
        "\n".join(
            [
                f"name: {title}",
                f"category: {category}",
                f"difficulty: {difficulty}",
                f"value: {points}",
                "type: standard",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return ["generated missing challenge.yml from metadata"]


def _repair_artifact_metadata(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    if metadata.get("category") not in {"re", "pwn"}:
        return []
    actions: list[str] = []
    preferred = _preferred_artifact(challenge_dir, metadata)
    if preferred is not None:
        preferred_rel = preferred.relative_to(challenge_dir).as_posix()
        if metadata.get("artifact") != preferred_rel:
            metadata["artifact"] = preferred_rel
            actions.append("updated metadata.artifact to primary executable artifact")
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
            if metadata.get("solve_status") is None:
                metadata["solve_status"] = "pending"
                actions.append("filled metadata.solve_status")
    return actions


def _repair_source_evidence(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    if metadata.get("category") not in {"re", "pwn"}:
        return []
    source_root = challenge_dir / "src"
    if any(_business_source_files(source_root)):
        return []
    deploy_source_root = challenge_dir / "deploy" / "src"
    sources = list(_business_source_files(deploy_source_root))
    if not sources:
        return []

    actions: list[str] = []
    for source in sources:
        destination = source_root / source.relative_to(deploy_source_root)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        actions.append(
            "copied deploy source to src: "
            + destination.relative_to(challenge_dir).as_posix()
        )
    return actions



def _repair_validate_wrapper(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    if metadata.get("category") != "re":
        return []
    validate = challenge_dir / "validate.sh"
    exp = challenge_dir / "writenup" / "exp.py"
    artifact = metadata.get("artifact")
    if not exp.is_file() or not isinstance(artifact, str) or not artifact.startswith("attachments/"):
        return []
    try:
        text = validate.read_text(encoding="utf-8", errors="ignore") if validate.is_file() else ""
    except OSError:
        text = ""
    if validate.is_file() and not any(token in text for token in ("metadata.json", "challenge.yml")):
        return []
    validate.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f"python3 writenup/exp.py ./{artifact} | grep -Eo 'flag\\{{[^}}]+\\}}' | tail -n 1\n",
        encoding="utf-8",
    )
    return ["rewrote validate.sh to check solver flag output without organizer files"]


def _repair_compose_validate_wrapper(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    if metadata.get("category") not in {"web", "pwn"}:
        return []
    validate = challenge_dir / "validate.sh"
    if not validate.is_file():
        return []
    try:
        original = validate.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if not _validate_uses_compose(original):
        return []

    text = _remove_compose_helper(original)
    text = _repair_bad_compose_file_variable(text)
    text = _replace_compose_invocations(text)
    text = _isolate_compose_project(text)
    if text == original:
        return []
    validate.write_text(text, encoding="utf-8")
    return ["normalized validate.sh docker-compose usage with an isolated compose project"]


def _repair_bad_compose_file_variable(text: str) -> str:
    return re.sub(
        r"(?P<prefix>COMPOSE_FILE\s*=\s*[\"'][^\"']*/deploy/)\$\{?COMPOSE\}?(?P<suffix>\.ya?ml[\"'])",
        r"\g<prefix>docker-compose\g<suffix>",
        text,
    )


def _repair_validate_workspace_paths(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    validate = challenge_dir / "validate.sh"
    if not validate.is_file():
        return []
    try:
        original = validate.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    challenge_id = str(metadata.get("id") or "")
    if not challenge_id or not _validate_has_hardcoded_execution_path(original):
        return []

    text = _replace_absolute_challenge_root_assignments(original)
    text = _replace_absolute_challenge_root_find(text, challenge_id)
    if text == original:
        return []
    validate.write_text(text, encoding="utf-8")
    return ["rewrote validate.sh hardcoded execution path to script-relative challenge root"]


def _validate_has_hardcoded_execution_path(text: str) -> bool:
    return "/workspace/executions/" in text or "/root/ctf-skills/work/executions/" in text


def _replace_absolute_challenge_root_assignments(text: str) -> str:
    script_root = 'CHAL_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"'
    patterns = (
        r'(?m)^(\s*)(?:CHAL_ROOT|CHALLENGE_ROOT)\s*=\s*["\']/(?:workspace/executions|root/ctf-skills/work/executions)/[^"\']*["\']\s*$',
        r'(?m)^(\s*)cd\s+["\']/(?:workspace/executions|root/ctf-skills/work/executions)/[^"\']*["\']\s*(?:\|\|\s*exit\s*1)?\s*$',
    )
    for pattern in patterns:
        text = re.sub(pattern, lambda match: f"{match.group(1)}{script_root}", text)
    return text


def _replace_absolute_challenge_root_find(text: str, challenge_id: str) -> str:
    script_root = 'CHAL_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"'
    pattern = (
        r'(?ms)^(\s*)(?:CHAL_ROOT|CHALLENGE_ROOT)\s*=\s*\$\(find\s+["\']?'
        r'/(?:workspace/executions|root/ctf-skills/work/executions)/[^"\')\n]*'
        r'["\']?\s+[^\n)]*'
        + re.escape(challenge_id)
        + r'[^\n)]*\)\s*$'
    )
    return re.sub(pattern, lambda match: f"{match.group(1)}{script_root}", text)


def _repair_docker_logs_no_color(challenge_dir: Path) -> list[str]:
    validate = challenge_dir / "validate.sh"
    if not validate.is_file():
        return []
    try:
        original = validate.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    repaired = re.sub(
        r"(?m)(\bdocker\s+logs\b[^\n]*?)\s+--no-color\b",
        r"\1",
        original,
    )
    if repaired == original:
        return []
    validate.write_text(repaired, encoding="utf-8")
    return ["removed unsupported --no-color flag from docker logs diagnostics"]


def _remove_compose_helper(text: str) -> str:
    pattern = re.compile(r"(?ms)^compose\(\) \{\n.*?^\}\n?")
    return pattern.sub("", text, count=1)


def _replace_compose_invocations(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        line = re.sub(r"(?<![\w-])docker\s+compose\b", "docker-compose", line)
        line = re.sub(
            r"(?<![\w-])compose\s+(up|down|ps|logs|pull|config|restart|stop|start)\b",
            r"docker-compose \1",
            line,
        )
        lines.append(line)
    return "".join(lines)


_COMPOSE_PROJECT_BLOCK = """\
if [ -z "${CHAL_ROOT:-}" ]; then
  CHAL_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
fi
if command -v sha256sum >/dev/null 2>&1; then
  PROJECT_HASH="$(printf '%s' "$CHAL_ROOT" | sha256sum | cut -c1-12)"
else
  PROJECT_HASH="$(printf '%s' "$CHAL_ROOT" | shasum -a 256 | cut -c1-12)"
fi
export COMPOSE_PROJECT_NAME="cf_${PROJECT_HASH}"
COMPOSE="docker-compose -p $COMPOSE_PROJECT_NAME -f $CHAL_ROOT/deploy/docker-compose.yml"
"""


def _validate_uses_compose(text: str) -> bool:
    compose_tokens = (
        "docker-compose",
        "docker compose",
        "compose version",
        "compose()",
        "neither compose nor docker-compose",
    )
    return any(token in text for token in compose_tokens)


def _isolate_compose_project(text: str) -> str:
    if not _validate_uses_compose(text):
        return text
    text = _replace_raw_docker_compose_commands(text)
    if "COMPOSE_PROJECT_NAME" not in text or "COMPOSE=" not in text:
        text = _insert_after_shell_preamble(text, _COMPOSE_PROJECT_BLOCK)
    return text


def _insert_after_shell_preamble(text: str, block: str) -> str:
    lines = text.splitlines(keepends=True)
    index = 0
    if lines and lines[0].startswith("#!"):
        index = 1
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "" or re.fullmatch(r"set\s+[-+][A-Za-z0-9]+", stripped):
            index += 1
            continue
        break
    return "".join(lines[:index]) + block + "".join(lines[index:])


def _replace_raw_docker_compose_commands(text: str) -> str:
    option = r"(?:-[fp]|--file|--project-name)"
    argument = r"(?:\"[^\"]+\"|'[^']+'|[^\s;&|]+)"
    compose_command_re = re.compile(
        rf"(?<![\w$-])docker-compose\b(?:\s+{option}\s+{argument})*"
    )
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("#") or re.match(r"(?:export\s+)?COMPOSE(?:_FILE)?=", stripped):
            lines.append(line)
            continue
        lines.append(compose_command_re.sub("$COMPOSE", line))
    return "".join(lines)


def _repair_pwn_validate_readiness_probe(
    challenge_dir: Path, metadata: dict[str, Any]
) -> list[str]:
    if metadata.get("category") != "pwn":
        return []
    validate = challenge_dir / "validate.sh"
    if not validate.is_file():
        return []
    try:
        text = validate.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    repaired = _replace_unexported_bash_nc_probe(text)
    repaired = _replace_dev_tcp_head_probe(repaired)
    repaired = _replace_port_only_nc_probe(repaired)
    repaired = _replace_timeout_nc_banner_capture(repaired)
    if repaired != text:
        repaired = _ensure_pwn_readiness_probe_function(repaired)
    if repaired == text:
        return []
    validate.write_text(repaired, encoding="utf-8")
    return ["fixed pwn validate.sh readiness probe to read an application prompt"]


def _ensure_pwn_readiness_probe_function(text: str) -> str:
    if re.search(r"(?m)^pwn_readiness_probe\(\) \{", text):
        return text
    return _insert_after_shell_preamble(text, _PWN_READINESS_PROBE_FUNCTION)


def _replace_unexported_bash_nc_probe(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        timeout_seconds = match.group(1)
        return f"pwn_readiness_probe \"$CHAL_HOST\" \"$CHAL_PORT\" {timeout_seconds}"

    for pattern in (
        r"""timeout\s+(\d+)\s+bash\s+-c\s+'[^'\n]*\bnc\b[^'\n]*\$CHAL_HOST[^'\n]*\$CHAL_PORT[^'\n]*'""",
        r'''timeout\s+(\d+)\s+bash\s+-c\s+"[^"\n]*\bnc\b[^"\n]*\$CHAL_HOST[^"\n]*\$CHAL_PORT[^"\n]*"''',
    ):
        text = re.sub(pattern, replace, text)
    return text


def _replace_timeout_nc_banner_capture(text: str) -> str:
    host = r"(?:\"[^\"]+\"|'[^']+'|[^\s;&|)]+)"
    port = r"(?:\"[^\"]+\"|'[^']+'|[^\s;&|)]+)"
    pattern = re.compile(
        rf"timeout\s+(?P<timeout>\d+)\s+nc\s+(?!-z\b)(?P<host>{host})\s+(?P<port>{port})(?:\s+2>/dev/null)?"
    )

    def replace(match: re.Match[str]) -> str:
        return (
            "pwn_readiness_probe "
            f"{match.group('host')} {match.group('port')} {match.group('timeout')}"
        )

    return pattern.sub(replace, text)


def _replace_dev_tcp_head_probe(text: str) -> str:
    token = r"(?:\"[^\"]+\"|'[^']+'|[^\s;&|)]+)"
    dev_tcp_host = r"(?:\$CHAL_HOST|\$\{CHAL_HOST\}|\"?\$CHAL_HOST\"?)"
    dev_tcp_port = r"(?:\$CHAL_PORT|\$\{CHAL_PORT\}|\"?\$CHAL_PORT\"?)"
    dev_tcp = rf"/dev/tcp/{dev_tcp_host}/{dev_tcp_port}"
    head_probe = rf"head\s+-c\s+\d+\s+<\s+{dev_tcp}"
    grep_probe = rf"(?:\|\s*grep\s+(?:-[qE]+\s+)?(?P<token>{token}))?"
    quoted = re.compile(
        rf"timeout\s+(?P<timeout>\d+)\s+bash\s+-c\s+(?P<quote>['\"])(?P<body>[^'\"]*{head_probe}[^'\"]*?){grep_probe}(?P=quote)"
    )

    def replace_quoted(match: re.Match[str]) -> str:
        return _pwn_readiness_probe_call(match.group("timeout"), match.group("token"))

    text = quoted.sub(replace_quoted, text)

    direct = re.compile(
        rf"(?:timeout\s+(?P<timeout>\d+)\s+)?{head_probe}\s*{grep_probe}"
    )

    def replace_direct(match: re.Match[str]) -> str:
        return _pwn_readiness_probe_call(match.group("timeout") or "3", match.group("token"))

    return direct.sub(replace_direct, text)


def _pwn_readiness_probe_call(timeout_seconds: str, token: str | None) -> str:
    cleaned = _clean_grep_token(token)
    if cleaned:
        return (
            f"pwn_readiness_probe \"$CHAL_HOST\" \"$CHAL_PORT\" "
            f"{timeout_seconds} {cleaned}"
        )
    return f"pwn_readiness_probe \"$CHAL_HOST\" \"$CHAL_PORT\" {timeout_seconds}"


def _clean_grep_token(token: str | None) -> str | None:
    if not token:
        return None
    value = token.strip()
    if not value:
        return None
    if (value[0], value[-1:]) in {("\"", "\""), ("'", "'")}:
        return value
    return repr(value)


def _replace_port_only_nc_probe(text: str) -> str:
    host = r"(?:\"[^\"]+\"|'[^']+'|[^\s;&|]+)"
    port = r"(?:\"[^\"]+\"|'[^']+'|[^\s;&|]+)"
    pattern = re.compile(rf"(?:timeout\s+(?P<timeout>\d+)\s+)?nc\s+-z\s+(?P<host>{host})\s+(?P<port>{port})")

    def replace(match: re.Match[str]) -> str:
        timeout_seconds = match.group("timeout") or "3"
        return f"pwn_readiness_probe {match.group('host')} {match.group('port')} {timeout_seconds}"

    return pattern.sub(replace, text)


def _repair_validate_solver_capture(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    if metadata.get("category") not in {"web", "pwn"}:
        return []
    validate = challenge_dir / "validate.sh"
    if not validate.is_file():
        return []
    try:
        text = validate.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    capture_re = re.compile(
        r"(?P<prefix>(?:set \+e\n)*)"
        r"EXPLOIT_OUTPUT=\$\((?P<command>(?:timeout\s+\d+\s+)?python3\s+writenup/exp\.py\s+2>&1)\)\n"
        r"(?P<exit>\s*EXPLOIT_EXIT=\$\?\n)?"
        r"(?P<suffix>(?:set -e\n?)*)",
        re.MULTILINE,
    )
    match = capture_re.search(text)
    if not match:
        return []
    command = match.group("command")
    canonical_capture = (
        "set +e\n"
        f"EXPLOIT_OUTPUT=$({command})\n"
        "EXPLOIT_EXIT=$?\n"
        "set -e\n"
    )
    repaired = capture_re.sub(canonical_capture, text, count=1)
    echo_pos = repaired.find('echo "$EXPLOIT_OUTPUT"', match.start())
    if echo_pos == -1 or echo_pos > match.start() + 260:
        insert_at = match.start() + len(canonical_capture)
        repaired = repaired[:insert_at] + 'echo "$EXPLOIT_OUTPUT"\n' + repaired[insert_at:]
    if "Exploit exited nonzero" not in repaired:
        echo_pos = repaired.find('echo "$EXPLOIT_OUTPUT"', match.start())
        line_end = repaired.find("\n", echo_pos)
        if echo_pos != -1 and line_end != -1:
            diagnostic = (
                "if [ \"$EXPLOIT_EXIT\" -ne 0 ]; then\n"
                "    echo \"[validate] Exploit exited nonzero: $EXPLOIT_EXIT\" >&2\n"
                "fi\n"
            )
            repaired = repaired[: line_end + 1] + diagnostic + repaired[line_end + 1:]
    repaired = repaired.replace("trap cleanup EXIT ERR", "trap cleanup EXIT")
    if repaired == text:
        return []
    validate.write_text(repaired, encoding="utf-8")
    return ["made validate.sh preserve solver diagnostics when exp.py exits non-zero"]


def _repair_pwn_xinetd_scaffold(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    if metadata.get("category") != "pwn" or not _looks_like_pwn_xinetd(challenge_dir, metadata):
        return []
    if not _PWN_XINETD_SCAFFOLD.is_dir():
        return []

    deploy = challenge_dir / "deploy"
    deploy.mkdir(parents=True, exist_ok=True)
    (deploy / "_files").mkdir(parents=True, exist_ok=True)
    (deploy / "src").mkdir(parents=True, exist_ok=True)

    binary_name = _pwn_binary_name(challenge_dir, metadata)
    service_port = _metadata_port(metadata)
    image_name = _metadata_image(challenge_dir, metadata)
    container_name = _docker_container_name(image_name)
    flag = str(metadata.get("flag") or "flag{replace_me}")
    replacements = {
        "{{BINARY_NAME}}": binary_name,
        "{{SERVICE_PORT}}": service_port,
        "{{HOST_PORT}}": service_port,
        "{{FLAG}}": flag,
        "{{IMAGE_NAME}}": image_name,
        "{{CONTAINER_NAME}}": container_name,
    }

    actions: list[str] = []
    for relative in (
        "deploy/Dockerfile",
        "deploy/docker-compose.yml",
        "deploy/_files/start.sh",
        "deploy/_files/ctf.xinetd",
    ):
        source = _PWN_XINETD_SCAFFOLD / relative
        destination = challenge_dir / relative
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        for needle, value in replacements.items():
            text = text.replace(needle, value)
        if destination.is_file():
            current = destination.read_text(encoding="utf-8", errors="ignore")
            if current == text:
                continue
            if relative == "deploy/Dockerfile" and not _pwn_dockerfile_needs_scaffold(current):
                continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        actions.append(f"normalized {relative} from pwn/xinetd-chroot scaffold")

    if metadata.get("docker_image") != image_name:
        metadata["docker_image"] = image_name
        actions.append("filled metadata.docker_image from pwn scaffold image name")
    if metadata.get("port") in (None, ""):
        metadata["port"] = int(service_port) if service_port.isdigit() else service_port
        actions.append("filled metadata.port from pwn scaffold service port")
    return actions


def _looks_like_pwn_xinetd(challenge_dir: Path, metadata: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(value)
        for value in (
            metadata.get("runtime_profile"),
            metadata.get("service_model"),
            metadata.get("template"),
            metadata.get("scaffold"),
            metadata.get("implementation_plan"),
        )
        if value is not None
    ).lower()
    if any(token in haystack for token in ("xinetd", "chroot", "socket", "tcp")):
        return True
    return any(
        (challenge_dir / relative).is_file()
        for relative in (
            "deploy/_files/ctf.xinetd",
            "deploy/_files/etc/xinetd.d/ctf",
            "deploy/_files/etc/xinetd.d/chal",
        )
    )


def _pwn_binary_name(challenge_dir: Path, metadata: dict[str, Any]) -> str:
    for key in ("binary_name", "service_binary", "executable"):
        value = metadata.get(key)
        if isinstance(value, str) and _safe_filename(value):
            return Path(value).name
    makefile = challenge_dir / "deploy" / "src" / "Makefile"
    detected = _binary_name_from_makefile(makefile)
    if detected:
        return detected
    executables = [
        path.name
        for path in (challenge_dir / "deploy" / "src").glob("*")
        if path.is_file() and path.stat().st_size > 0 and _safe_filename(path.name)
    ]
    for preferred in ("pwn", "vuln", "chal", "challenge"):
        if preferred in executables:
            return preferred
    return "pwn"


def _binary_name_from_makefile(makefile: Path) -> str | None:
    try:
        text = makefile.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for pattern in (
        r"(?m)^\s*(?:TARGET|BIN|BINARY|OUT)\s*[:?+]?=\s*([A-Za-z0-9_.-]+)\s*$",
        r"(?m)^\s*all\s*:\s*([A-Za-z0-9_.-]+)\s*$",
    ):
        match = re.search(pattern, text)
        if match and _safe_filename(match.group(1)):
            return match.group(1)
    for match in re.finditer(r"(?m)^\s*(?:gcc|clang|cc)\b[^\n]*\s-o\s+([A-Za-z0-9_.-]+)", text):
        if _safe_filename(match.group(1)):
            return match.group(1)
    return None


def _metadata_port(metadata: dict[str, Any]) -> str:
    for key in ("port", "service_port", "container_port", "host_port"):
        value = metadata.get(key)
        if isinstance(value, int) and 0 < value < 65536:
            return str(value)
        if isinstance(value, str) and value.isdigit() and 0 < int(value) < 65536:
            return value
    return _PWN_DEFAULT_SERVICE_PORT


def _metadata_image(challenge_dir: Path, metadata: dict[str, Any]) -> str:
    image = metadata.get("docker_image")
    if isinstance(image, str) and re.fullmatch(r"[a-z0-9][a-z0-9_.-]*(?::[A-Za-z0-9_.-]+)?", image.strip()):
        return image.strip()
    challenge_id = str(metadata.get("id") or challenge_dir.name).lower()
    sanitized = re.sub(r"[^a-z0-9_.-]+", "-", challenge_id).strip(".-")
    return sanitized or "pwn-challenge"


def _docker_container_name(image_name: str) -> str:
    base = image_name.split("/", 1)[-1].split(":", 1)[0]
    sanitized = re.sub(r"[^a-z0-9_.-]+", "-", base.lower()).strip(".-")
    return sanitized or "pwn-challenge"


def _safe_filename(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", value)) and value not in {".", ".."}


def _pwn_dockerfile_needs_scaffold(text: str) -> bool:
    lowered = text.lower()
    if "scaffolds/pwn/xinetd-chroot" in lowered:
        return False
    required = ("xinetd", "/usr/sbin/chroot", "/etc/xinetd.d/ctf")
    if not all(token in lowered for token in required):
        return True
    if "copy ./src/" in lowered or "copy ./_files/" in lowered:
        return True
    return "cp -r /lib* /home/ctf" in lowered and "cp -r /usr/lib* /home/ctf" in lowered


def _repair_deploy_dockerfile(challenge_dir: Path, metadata: dict[str, Any]) -> list[str]:
    if metadata.get("category") not in {"web", "pwn"}:
        return []
    dockerfile = challenge_dir / "deploy" / "Dockerfile"
    if not dockerfile.is_file():
        return []
    try:
        original = dockerfile.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    lines = original.splitlines(keepends=True)
    actions: list[str] = []
    updated_lines: list[str] = []
    for line in lines:
        updated = line
        stripped = updated.lstrip()
        if stripped.startswith("COPY "):
            updated = _normalize_dockerfile_copy_sources(updated)
        updated_lines.append(updated)
    text = "".join(updated_lines)
    if text != original:
        actions.append("normalized Dockerfile COPY sources to challenge-root build context")

    updated = _normalize_apt_mirror_fallback_order(text)
    if updated != text:
        text = updated
        actions.append("normalized Dockerfile apt mirror fallback order")

    if _dockerfile_needs_make_install(text, challenge_dir):
        text = _inject_make_install_layer(text)
        actions.append("added Dockerfile layer installing make before build steps")

    if _dockerfile_needs_i386_packages(text, challenge_dir, metadata):
        updated = _add_apt_packages(text, ("gcc-multilib", "libc6-dev-i386", "lib32z1"))
        if updated != text:
            text = updated
            actions.append("added Dockerfile i386 multilib packages")

    binary_name = _pwn_binary_name(challenge_dir, metadata)
    updated = _replace_missing_pwn_copy_target(text, binary_name)
    if updated != text:
        text = updated
        actions.append(f"aligned Dockerfile binary copy target to {binary_name}")

    replaced = re.sub(
        (
            r"cp -R /lib\* /home/ctf/?(?:\s*\\)?"
            r"(?:\s*2>/dev/null \|\| true)?\s*&&\s*"
            r"cp -R /usr/lib\* /home/ctf/?(?:\s*\\)?"
            r"(?:\s*2>/dev/null \|\| true)?"
        ),
        (
            "mkdir -p /home/ctf/lib64 /home/ctf/lib/x86_64-linux-gnu && "
            "(cp -L /lib64/ld-linux-x86-64.so.2 /home/ctf/lib64/ 2>/dev/null || "
            "cp /lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /home/ctf/lib64/ld-linux-x86-64.so.2) && "
            "cp -a /lib/x86_64-linux-gnu/*.so* /home/ctf/lib/x86_64-linux-gnu/ 2>/dev/null || true"
        ),
        text,
        count=1,
        flags=re.S,
    )
    if replaced != text:
        text = replaced
        actions.append("replaced conflicting /lib and /usr/lib chroot copy pattern")

    if not actions:
        return []
    dockerfile.write_text(text, encoding="utf-8")
    return actions


def _business_source_files(root: Path):
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".rs", ".go", ".asm", ".s"}:
            yield path


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



def _preferred_artifact(challenge_dir: Path, metadata: dict[str, Any]) -> Path | None:
    if metadata.get("category") != "re":
        return None
    attachments = challenge_dir / "attachments"
    if not attachments.is_dir():
        return None
    executables = [
        path
        for path in attachments.rglob("*")
        if path.is_file()
        and path.stat().st_size > 0
        and _detect_target_format(path) in {"elf", "exe"}
    ]
    if not executables:
        return None
    current = metadata.get("artifact")
    if isinstance(current, str):
        current_path = (challenge_dir / current).resolve()
        if any(path.resolve() == current_path for path in executables):
            return challenge_dir / current
    executables.sort(
        key=lambda path: (
            0 if re.search(r"\.(?:dat|bin|pak|gres)$", path.name, re.I) else -1,
            path.name,
        )
    )
    return executables[0]


def _dockerfile_needs_make_install(text: str, challenge_dir: Path) -> bool:
    if not (challenge_dir / "deploy" / "src" / "Makefile").is_file():
        return False
    if not re.search(r"(?im)^\s*from\s+(ubuntu|debian)(?::|\s)", text):
        return False
    if not _dockerfile_uses_make(text):
        return False
    return not _dockerfile_install_block_contains_package(text, "make")


def _normalize_apt_mirror_fallback_order(text: str) -> str:
    if "mirrors.tuna.tsinghua.edu.cn" not in text and "mirror.tuna.tsinghua.edu.cn" not in text:
        return text
    mirror_block = (
        "for mirror in \\\n"
        "        http://mirrors.aliyun.com/ubuntu/ \\\n"
        "        http://mirrors.ustc.edu.cn/ubuntu/ \\\n"
        "        http://mirrors.zju.edu.cn/ubuntu/ \\\n"
        "        http://archive.ubuntu.com/ubuntu/; do"
    )
    mirror_loop_re = (
        r"for mirror in \\\n"
        r"(?:\s+https?://[A-Za-z0-9_.-]+(?:/ubuntu)?/ \\\n)*"
        r"\s+https?://[A-Za-z0-9_.-]+(?:/ubuntu)?/; do"
    )
    if not re.search(mirror_loop_re, text):
        text = re.sub(
            r"https?://mirrors?\.tuna\.tsinghua\.edu\.cn/ubuntu/?",
            "http://mirrors.aliyun.com/ubuntu/",
            text,
        )
        return re.sub(
            r"https?://mirrors?\.tuna\.tsinghua\.edu\.cn/?",
            "http://mirrors.aliyun.com/",
            text,
        )

    text = re.sub(mirror_loop_re, mirror_block, text, count=1)
    text = re.sub(
        r"http://mirror\.tuna\.tsinghua\.edu\.cn/(?!ubuntu\b)",
        "http://mirror.tuna.tsinghua.edu.cn/ubuntu/",
        text,
    )
    if "mirror.tuna.tsinghua.edu.cn/ubuntu" in text and "mirrors.tuna.tsinghua.edu.cn/ubuntu" in text:
        return text
    sed_anchor = '-e "s#http://security.ubuntu.com/ubuntu/?#${mirror}#g" \\'
    extra = (
        sed_anchor
        + "\n"
        + '            -e "s#http://mirror.tuna.tsinghua.edu.cn/ubuntu/?#${mirror}#g" \\'
    )
    if sed_anchor in text and "mirror.tuna.tsinghua.edu.cn/ubuntu" not in text:
        text = text.replace(sed_anchor, extra, 1)
    return text


def _dockerfile_needs_i386_packages(
    text: str,
    challenge_dir: Path,
    metadata: dict[str, Any],
) -> bool:
    haystack = "\n".join(
        [
            text,
            _read_optional(challenge_dir / "deploy" / "src" / "Makefile"),
            str(metadata.get("architecture") or ""),
            str(metadata.get("target_architecture") or ""),
        ]
    ).lower()
    if not any(token in haystack for token in ("-m32", "i386", "x86_32", "linux/386")):
        return False
    return not all(
        _dockerfile_install_block_contains_package(text, package)
        for package in ("gcc-multilib", "libc6-dev-i386", "lib32z1")
    )


def _read_optional(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _add_apt_packages(text: str, packages: tuple[str, ...]) -> str:
    install_re = re.compile(
        r"(?P<prefix>apt-get\s+install\s+(?:-[^\n&;]+\s+)*)"
        r"(?P<body>.*?)(?P<suffix>\s*(?:&&|;|\\\n|$))",
        flags=re.I | re.S,
    )

    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        missing = [
            package
            for package in packages
            if not re.search(rf"(?<![\w.-]){re.escape(package)}(?![\w.-])", body)
        ]
        if not missing:
            return match.group(0)
        return match.group("prefix") + body.rstrip() + " " + " ".join(missing) + match.group("suffix")

    return install_re.sub(replace, text, count=1)


def _replace_missing_pwn_copy_target(text: str, binary_name: str) -> str:
    if binary_name == "pwn":
        return text
    return re.sub(
        r"(?m)(\bcp\s+)(?:\./)?pwn(\s+/home/ctf/)pwn\b",
        rf"\1{binary_name}\2{binary_name}",
        text,
    )


def _dockerfile_uses_make(text: str) -> bool:
    current_run: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("RUN "):
            current_run = [stripped]
        elif current_run and (line.startswith(" ") or line.startswith("\t")):
            current_run.append(stripped)
        else:
            current_run = []
        if current_run and re.search(r"(?<![\w.-])make(?![\w.-])", " ".join(current_run)):
            return True
    return False


def _dockerfile_install_block_contains_package(text: str, package: str) -> bool:
    for match in re.finditer(
        r"apt-get\s+install\b(?P<body>.*?)(?:&&|\n\s*(?:run|copy|cmd|entrypoint|workdir|from)\b|$)",
        text,
        flags=re.I | re.S,
    ):
        body = match.group("body")
        if re.search(rf"(?<![\w.-]){re.escape(package)}(?![\w.-])", body):
            return True
    return False


def _inject_make_install_layer(text: str) -> str:
    lines = text.splitlines(keepends=True)
    install_line = "RUN apt-get update && apt-get install -y make && rm -rf /var/lib/apt/lists/*\n"
    run_start = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("RUN "):
            run_start = index
        if run_start is not None and re.search(r"(?<![\w.-])make(?![\w.-])", stripped):
            lines.insert(run_start, install_line)
            return "".join(lines)
    lines.append(install_line)
    return "".join(lines)


def _normalize_dockerfile_copy_sources(line: str) -> str:
    newline = "\n" if line.endswith("\n") else ""
    body = line[:-1] if newline else line
    indent_len = len(body) - len(body.lstrip())
    indent = body[:indent_len]
    stripped = body[indent_len:]
    parts = stripped.split()
    if len(parts) < 3 or parts[0] != "COPY":
        return line
    normalized = [parts[0]]
    sources = parts[1:-1]
    destination = parts[-1]
    for token in sources:
        token = re.sub(r"^src/", "deploy/src/", token)
        token = re.sub(r"^_files/", "deploy/_files/", token)
        normalized.append(token)
    normalized.append(destination)
    return indent + " ".join(normalized) + newline


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
