"""Structured Pwn diagnostics for validation repair.

The harness is intentionally conservative: it gathers reproducible evidence
from the current challenge directory and records unavailable tooling instead of
failing the caller.  Host validation remains authoritative.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from core.jsonio import read_json, write_json

FLAG_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])flag\{[^\r\n{}]+\}(?![A-Za-z0-9_])")

PWN_DEBUG_FILENAME = "pwn-debug-result.json"
READINESS_TOKENS = ("Choice:", "choice:", "menu", "Menu", ">", "$ ", "Welcome", "Input")
FAILURE_STAGES = {
    "readiness",
    "service_not_started",
    "external_unavailable",
    "connection",
    "prompt_desync",
    "leak",
    "canary_or_offset",
    "payload_control_flow",
    "shell",
    "flag_read",
    "solver",
    "contract",
    "unknown",
}
SERVICE_MODES = {"managed", "external", "not_started"}


def run_pwn_debug(
    challenge_dir: Path,
    *,
    output_path: Path | None = None,
    timeout: int = 8,
    run_exp: bool = True,
    service_mode: str = "external",
) -> dict[str, Any]:
    """Collect bounded Pwn diagnostics and write a stable JSON result."""
    if service_mode not in SERVICE_MODES:
        raise ValueError(f"unsupported pwn-debug service_mode: {service_mode}")
    challenge_dir = challenge_dir.resolve()
    metadata = read_json(challenge_dir / "metadata.json", {})
    if not isinstance(metadata, dict):
        metadata = {}
    artifact = _artifact_path(challenge_dir, metadata)
    result: dict[str, Any] = {
        "schema_version": 1,
        "challenge_dir": str(challenge_dir),
        "binary_path": str(artifact) if artifact else None,
        "metadata_summary": _metadata_summary(metadata),
        "tools": {},
        "binary": _binary_summary(artifact, challenge_dir) if artifact else None,
        "symbols": {},
        "got_plt_gadgets": {},
        "inferred_stack_layout": _infer_stack_layout(challenge_dir),
        "service_mode": service_mode,
        "service_readiness": {},
        "format_string_leak_sampling": {},
        "exp_execution": {},
        "failure_stage": "unknown",
        "actionable_summary": "No specific pwn-debug action inferred yet.",
    }
    if artifact and artifact.is_file():
        result["tools"] = _tool_summaries(artifact)
        result["symbols"] = _symbol_summary(artifact)
        result["got_plt_gadgets"] = _got_plt_gadget_summary(artifact)
    service_started = False
    if service_mode == "managed":
        result["managed_service"] = _compose_command(challenge_dir, "up", "-d", timeout=timeout)
        service_started = result["managed_service"].get("status") == "ok"
    elif service_mode == "not_started":
        result["service_readiness"] = {
            "status": "not_started",
            "reason": "pwn-debug did not start service; validation result remains authoritative",
        }
    try:
        if service_mode != "not_started":
            result["service_readiness"] = collect_service_readiness(challenge_dir, metadata)
        ready = _service_probe_ready(result.get("service_readiness"))
        if service_mode == "managed" and not service_started:
            result["exp_execution"] = {
                "status": "skipped",
                "reason": "managed docker-compose up did not complete",
            }
        elif service_mode == "not_started":
            result["exp_execution"] = {
                "status": "skipped",
                "reason": "service_not_started",
            }
        else:
            result["format_string_leak_sampling"] = _format_string_leak_sampling(
                challenge_dir,
                metadata,
                timeout=timeout,
            )
            if run_exp and (service_mode == "external" or ready):
                result["exp_execution"] = _run_exp(challenge_dir, timeout=timeout)
            elif run_exp:
                result["exp_execution"] = {
                    "status": "skipped",
                    "reason": "service probe did not reach ready state",
                }
    finally:
        if service_mode == "managed":
            result["managed_cleanup"] = _compose_command(
                challenge_dir,
                "down",
                "--remove-orphans",
                timeout=timeout,
            )
    result["failure_stage"] = classify_pwn_failure_stage(
        pwn_debug=result,
        stdout_tail=_nested_text(result.get("exp_execution"), "stdout_tail"),
        stderr_tail=_nested_text(result.get("exp_execution"), "stderr_tail"),
        returncode=_nested_int(result.get("exp_execution"), "returncode"),
    )
    result["actionable_summary"] = _actionable_summary(result["failure_stage"], result)
    if output_path is None:
        output_path = challenge_dir / "logs" / PWN_DEBUG_FILENAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, result)
    result["result_path"] = str(output_path)
    result["result_sha256"] = _sha256_if_file(output_path)
    write_json(output_path, result)
    return result


def collect_service_readiness(
    challenge_dir: Path,
    metadata: Mapping[str, Any],
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Collect Docker/Compose state plus a fresh TCP banner probe."""
    host = str(os.environ.get("CHAL_HOST") or metadata.get("host") or "127.0.0.1")
    port = _metadata_port(metadata)
    image = metadata.get("docker_image")
    result: dict[str, Any] = {
        "host": host,
        "port": port,
        "image": image if isinstance(image, str) else None,
        "container": None,
        "docker": _run_optional(["docker", "version", "--format", "{{.Server.Version}}"], cwd=challenge_dir, timeout=3),
        "compose_ps": _compose_command(challenge_dir, "ps", timeout=5),
        "compose_logs_tail": _compose_command(challenge_dir, "logs", "--no-color", "--tail=120", timeout=5),
        "tcp_probe": {},
        "matched_readiness_token": None,
    }
    if port is not None:
        probe = tcp_readiness_probe(host, port, timeout=timeout)
        result["tcp_probe"] = probe
        result["matched_readiness_token"] = probe.get("matched_token")
    else:
        result["tcp_probe"] = {"status": "skipped", "reason": "no port in metadata/environment"}
    if _command_unavailable(result["docker"]):
        result["docker_status"] = "docker_unavailable"
    return result


def tcp_readiness_probe(
    host: str,
    port: int,
    *,
    timeout: float = 2.0,
    tokens: tuple[str, ...] = READINESS_TOKENS,
    max_bytes: int = 4096,
) -> dict[str, Any]:
    """Open a fresh TCP connection and succeed as soon as a prompt token appears."""
    started = time.monotonic()
    chunks: list[bytes] = []
    status = "failed"
    matched: str | None = None
    error: str | None = None
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(0.15)
            status = "connected"
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and sum(len(c) for c in chunks) < max_bytes:
                try:
                    data = sock.recv(512)
                except TimeoutError:
                    data = b""
                except socket.timeout:
                    data = b""
                if data:
                    chunks.append(data)
                    text = b"".join(chunks).decode("utf-8", errors="replace")
                    for token in tokens:
                        if token in text:
                            matched = token
                            status = "ready"
                            raise _ProbeComplete
                else:
                    time.sleep(0.03)
    except _ProbeComplete:
        pass
    except OSError as exc:
        error = f"{exc.__class__.__name__}: {exc}"
        status = "failed"
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    return {
        "status": status,
        "matched_token": matched,
        "elapsed": round(time.monotonic() - started, 3),
        "raw_output_tail": raw[-1000:],
        **({"error": error} if error else {}),
    }


def classify_pwn_failure_stage(
    *,
    status: str | None = None,
    error: str | None = None,
    stdout_tail: str | None = None,
    stderr_tail: str | None = None,
    returncode: int | None = None,
    pwn_debug: Mapping[str, Any] | None = None,
) -> str:
    """Classify a Pwn failure into repair-oriented stages."""
    text = "\n".join(str(item) for item in (status, error, stdout_tail, stderr_tail) if item)
    lower = text.lower()
    debug = pwn_debug or {}
    readiness = debug.get("service_readiness") if isinstance(debug, Mapping) else None
    probe = readiness.get("tcp_probe") if isinstance(readiness, Mapping) else None
    probe_status = str(probe.get("status") or "") if isinstance(probe, Mapping) else ""
    raw_probe = str(probe.get("raw_output_tail") or "") if isinstance(probe, Mapping) else ""
    exp = debug.get("exp_execution") if isinstance(debug, Mapping) else None
    final_flag = exp.get("final_flag_candidate") if isinstance(exp, Mapping) else None
    leak_sample = debug.get("format_string_leak_sampling") if isinstance(debug, Mapping) else None

    service_mode = str(debug.get("service_mode") or "") if isinstance(debug, Mapping) else ""
    readiness_status = str(readiness.get("status") or "") if isinstance(readiness, Mapping) else ""
    exploit_started = _exploit_started(text)
    leak_failed = _leak_failed(text)

    if "contract" in lower or status in {"contract_failed", "solver_evidence_stale"}:
        return "contract"
    if service_mode == "not_started" or readiness_status == "not_started":
        return "service_not_started"
    if leak_failed:
        return "leak"
    if exploit_started:
        if any(marker in lower for marker in ("stack smashing", "canary", "bad offset", "cyclic", "saved rip")):
            return "canary_or_offset"
        if any(marker in lower for marker in ("got shell", "interactive shell", "$ ")) and not final_flag:
            return "flag_read"
        if any(marker in lower for marker in ("failed to extract flag", "flag not captured", "payload", "no flag")):
            return "payload_control_flow"
        if returncode not in (None, 0):
            return "solver"
    if service_mode == "external" and any(marker in lower for marker in ("connection refused", "connection reset")):
        if probe_status != "ready" and not raw_probe:
            return "external_unavailable"
    if probe_status in {"failed", "connected"} and not raw_probe:
        return "readiness"
    if any(marker in lower for marker in ("service not ready", "readiness", "no banner", "no prompt")):
        if "service is ready" not in lower and "readiness ok" not in lower:
            return "readiness"
    if any(marker in lower for marker in ("brokenpipe", "broken pipe", "protocol desync", "prompt desync")):
        if probe_status == "ready" or raw_probe:
            return "prompt_desync"
    if any(marker in lower for marker in ("connection refused", "connection reset", "eoferror", "got eof")):
        if probe_status == "ready" or raw_probe:
            return "prompt_desync"
        return "readiness"
    if isinstance(leak_sample, Mapping):
        stable = leak_sample.get("stable")
        if stable is False or leak_sample.get("status") == "unstable":
            return "leak"
    if any(marker in lower for marker in ("leak failed", "could not leak", "empty leak", "unstable leak")):
        return "leak"
    if any(marker in lower for marker in ("failed to extract flag", "flag not captured", "payload", "no flag")):
        if probe_status == "ready" or raw_probe:
            return "payload_control_flow"
    if any(marker in lower for marker in ("stack smashing", "canary", "bad offset", "cyclic", "saved rip")):
        return "canary_or_offset"
    if any(marker in lower for marker in ("got shell", "interactive shell", "$ ")) and not final_flag:
        return "flag_read"
    if final_flag:
        return "solver" if returncode not in (None, 0) else "unknown"
    if returncode not in (None, 0):
        return "solver"
    return "unknown"


def _exploit_started(text: str) -> bool:
    lower = text.lower()
    return bool(
        re.search(r"\b(service\s+(?:is\s+)?ready[,;:\s-]+running exploit)\b", lower)
        or re.search(r"\b(running exploit|exploit phase|starting exploit|launching exploit)\b", lower)
        or re.search(r"(?im)^\s*\[\*\]\s*===\s*stage\s+\d+:", text)
        or re.search(r"(?im)^\s*(?:stage\s+\d+|alloc|free|view|write|leak|payload|shell|flag)\b", text)
    )


def _leak_failed(text: str) -> bool:
    lower = text.lower()
    if any(
        marker in lower
        for marker in (
            "pwn_libc_leak_failed",
            "failed to leak libc base",
            "failed to leak libc",
            "leak failed",
            "could not leak",
            "empty leak",
            "unstable leak",
            "all-zero leak",
            "all zero leak",
        )
    ):
        return True
    if "leak data" in lower and re.search(r"\b0{8,}\b", lower):
        return True
    return False


class _ProbeComplete(Exception):
    pass


def _artifact_path(challenge_dir: Path, metadata: Mapping[str, Any]) -> Path | None:
    artifact = metadata.get("artifact")
    if isinstance(artifact, str) and artifact.startswith("attachments/") and ".." not in Path(artifact).parts:
        return challenge_dir / artifact
    return None


def _metadata_summary(metadata: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "category",
        "title",
        "difficulty",
        "primary_technique",
        "runtime",
        "artifact",
        "artifact_sha256",
        "docker_image",
        "port",
    )
    return {key: metadata.get(key) for key in keys if metadata.get(key) not in (None, "")}


def _binary_summary(path: Path, challenge_dir: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(challenge_dir).as_posix() if path.is_relative_to(challenge_dir) else str(path),
        "sha256": _sha256_if_file(path),
        "size": path.stat().st_size if path.is_file() else None,
        "is_elf": is_elf(path),
    }


def _tool_summaries(path: Path) -> dict[str, Any]:
    return {
        "file": _run_optional(["file", str(path)], timeout=3),
        "checksec": _run_optional(["checksec", "--file", str(path)], timeout=5),
        "readelf_header": _run_optional(["readelf", "-h", str(path)], timeout=5),
        "readelf_symbols": _run_optional(["readelf", "-sW", str(path)], timeout=5),
        "objdump_disassembly": _run_optional(["objdump", "-d", str(path)], timeout=5),
    }


def _symbol_summary(path: Path) -> dict[str, Any]:
    run = _run_optional(["readelf", "-sW", str(path)], timeout=5, tail_limit=20000)
    symbols: dict[str, str] = {}
    stdout = run.get("stdout_tail")
    if isinstance(stdout, str):
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) >= 8 and parts[3] == "FUNC":
                name = parts[7].split("@", 1)[0]
                if name in {"main", "vuln", "win", "read_flag", "setup", "greet"}:
                    symbols[name] = parts[1]
    return symbols


def _got_plt_gadget_summary(path: Path) -> dict[str, Any]:
    disasm = _run_optional(["objdump", "-d", str(path)], timeout=5, tail_limit=40000)
    text = str(disasm.get("stdout_tail") or "")
    gadgets = sorted(set(re.findall(r"\b(?:ret|syscall|pop\s+%?rdi|pop\s+%?rsi|pop\s+%?rdx)\b", text)))[:20]
    plt = sorted(set(re.findall(r"<([^>]+@plt)>", text)))[:40]
    return {"plt": plt, "gadgets": gadgets}


def _infer_stack_layout(challenge_dir: Path) -> dict[str, Any]:
    text = _read_joined(challenge_dir / "writenup" / "exp.py", challenge_dir / "writenup" / "pwn_debug_report.json")
    offsets: dict[str, int] = {}
    for name, pattern in {
        "buffer_offset": r"(?:OFFSET|offset|padding|buf(?:fer)?_size)\s*=\s*(0x[0-9a-fA-F_]+|\d+)",
        "canary_offset": r"canary[_\s-]*(?:offset|idx|index|pos(?:ition)?)\s*=\s*(0x[0-9a-fA-F_]+|\d+)",
        "return_address_offset": r"(?:ret|rip|return)[_\s-]*(?:offset|idx|index)?\s*=\s*(0x[0-9a-fA-F_]+|\d+)",
    }.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            offsets[name] = int(match.group(1).replace("_", ""), 0)
    return offsets or {"status": "unavailable"}


def _format_string_leak_sampling(
    challenge_dir: Path,
    metadata: Mapping[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    exp_text = _read_text(challenge_dir / "writenup" / "exp.py") or ""
    metadata_text = json.dumps(metadata, ensure_ascii=False).lower()
    if not any(
        token in (exp_text + metadata_text).lower()
        for token in ("format", "%p", "canary", "pie leak", "libc leak")
    ):
        return {"status": "not_declared"}
    values = re.findall(r"0x[0-9a-fA-F_]+", exp_text)
    classified = [
        {"value": value, "classification": classify_leak_value(value)}
        for value in values[:80]
    ]
    return {
        "status": "static_sample",
        "samples": [],
        "candidate_values": classified,
        "stable": None,
        "note": "Dynamic multi-connection sampling is skipped unless service coordinates are available.",
    }


def classify_leak_value(value: str | int) -> str:
    try:
        candidate = int(str(value).replace("_", ""), 0)
    except ValueError:
        return "unknown"
    if candidate == 0:
        return "null"
    if candidate < 0x1000:
        return "small"
    if 0x7FFF00000000 <= candidate <= 0x7FFFFFFFFFFF:
        return "stack"
    if 0x7F0000000000 <= candidate <= 0x7FFFFFFFFFFF:
        return "libc"
    if 0x550000000000 <= candidate <= 0x57FFFFFFFFFF:
        return "pie"
    if candidate & 0xFF == 0:
        return "canary"
    return "unknown"


def _run_exp(challenge_dir: Path, *, timeout: int) -> dict[str, Any]:
    exp = challenge_dir / "writenup" / "exp.py"
    if not exp.is_file():
        return {"status": "skipped", "reason": "writenup/exp.py missing"}
    command = [sys.executable, str(exp)]
    started = time.monotonic()
    try:
        process = subprocess.run(
            command,
            cwd=challenge_dir,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "command": command,
            "elapsed": round(time.monotonic() - started, 2),
            "stdout_tail": _tail_text(exc.stdout),
            "stderr_tail": _tail_text(exc.stderr),
        }
    except OSError as exc:
        return {"status": "error", "command": command, "error": f"{exc.__class__.__name__}: {exc}"}
    stdout_tail = process.stdout[-2000:]
    stderr_tail = process.stderr[-2000:]
    matches = FLAG_TOKEN_RE.findall(process.stdout)
    return {
        "status": "exited",
        "command": command,
        "returncode": process.returncode,
        "elapsed": round(time.monotonic() - started, 2),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "final_flag_candidate": matches[-1] if matches else None,
    }


def _run_optional(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 5,
    tail_limit: int = 4000,
) -> dict[str, Any]:
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"status": "unavailable", "command": command, "reason": f"{command[0]} not found"}
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "command": command,
            "stdout_tail": _tail_text(exc.stdout, limit=tail_limit),
            "stderr_tail": _tail_text(exc.stderr, limit=tail_limit),
        }
    except OSError as exc:
        return {"status": "error", "command": command, "error": f"{exc.__class__.__name__}: {exc}"}
    return {
        "status": "ok" if process.returncode == 0 else "nonzero",
        "command": command,
        "returncode": process.returncode,
        "stdout_tail": (process.stdout or "")[-tail_limit:],
        "stderr_tail": (process.stderr or "")[-tail_limit:],
    }


def _compose_command(challenge_dir: Path, *args: str, timeout: int) -> dict[str, Any]:
    compose = challenge_dir / "deploy" / "docker-compose.yml"
    if not compose.is_file():
        return {"status": "skipped", "reason": "deploy/docker-compose.yml missing"}
    project = _compose_project_name(challenge_dir)
    command = ["docker-compose", "-p", project, "-f", str(compose), *args]
    return _run_optional(command, cwd=challenge_dir, timeout=timeout)


def _compose_project_name(challenge_dir: Path) -> str:
    digest = hashlib.sha256(str(challenge_dir).encode("utf-8")).hexdigest()[:10]
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", challenge_dir.name).strip("-").lower()
    stem = stem[:32] or "challenge"
    return f"cf-{stem}-{digest}"


def _service_probe_ready(readiness: Any) -> bool:
    if not isinstance(readiness, Mapping):
        return False
    probe = readiness.get("tcp_probe")
    return isinstance(probe, Mapping) and probe.get("status") == "ready"


def _metadata_port(metadata: Mapping[str, Any]) -> int | None:
    for key in ("port", "host_port", "service_port"):
        value = os.environ.get("CHAL_PORT") if key == "port" and os.environ.get("CHAL_PORT") else metadata.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _command_unavailable(result: Any) -> bool:
    return isinstance(result, Mapping) and result.get("status") == "unavailable"


def _sha256_if_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _tail_text(value: Any, *, limit: int = 2000) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = text.strip()
    return text[-limit:] if text else None


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_joined(*paths: Path) -> str:
    return "\n".join(text for path in paths if (text := _read_text(path)))


def _nested_text(value: Any, key: str) -> str | None:
    if isinstance(value, Mapping):
        item = value.get(key)
        if isinstance(item, str):
            return item
    return None


def _nested_int(value: Any, key: str) -> int | None:
    if isinstance(value, Mapping):
        item = value.get(key)
        if isinstance(item, int):
            return item
    return None


def _actionable_summary(stage: str, result: Mapping[str, Any]) -> str:
    if stage == "service_not_started":
        return "pwn-debug did not start service; validation result remains authoritative."
    if stage == "external_unavailable":
        return (
            "External service was unavailable; do not override authoritative "
            "validation with pwn-debug readiness claims."
        )
    if stage == "readiness":
        return (
            "Fix service readiness first: container state, prompt/banner probe, "
            "host/port wiring, and raw TCP output."
        )
    if stage == "connection":
        return "Service appears reachable; repair solver connection/prompt synchronization before payload changes."
    if stage == "leak":
        return "Repair leak collection and verify stable positions across fresh connections."
    if stage == "canary_or_offset":
        return "Recompute canary position and overflow offsets from the current artifact and dynamic evidence."
    if stage == "payload_control_flow":
        return "Leaks/readiness are past the first hurdle; debug final payload control flow and flag extraction."
    if stage == "flag_read":
        return "Code execution likely reached a shell/path, but the final /flag read path failed."
    if stage == "contract":
        return "Fix source/design/metadata/artifact contract drift before runtime solver tuning."
    if stage == "solver":
        return "Inspect exp.py return code, stdout/stderr, and validate.sh wrapper behavior."
    return "No specific stage inferred; inspect pwn-debug JSON and validation history."
