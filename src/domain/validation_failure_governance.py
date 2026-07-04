"""Normalized validation failure governance helpers.

This module keeps batch-level validation classification separate from the
lower-level diagnostic codes produced by ``domain.validation``. The detailed
codes remain the repair evidence; the normalized class and signature provide a
stable routing surface for batch orchestration.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from core.jsonio import read_json
from core.paths import ProjectPaths

VALIDATION_FAILURE_CLASSES = ("timeout", "service-readiness", "contract", "solver")

_NO_VALIDATION_CLASS_PHASES = {
    "hermes_auth",
    "hermes_rate_limit",
    "hermes_timeout",
    "terminal_workspace",
    "materialize",
    "contract_prepare",
}
_READINESS_CODES = {
    "compose_cross_talk",
    "pwn_bad_binary_path",
    "pwn_service_readiness_failed",
    "pwn_port_only_readiness",
    "pwn_bad_readiness_probe",
}
_CONTRACT_STATUSES = {
    "contract_failed",
    "missing_validation",
    "invalid_metadata",
    "missing_challenge",
    "ambiguous_challenge",
}
_SOLVER_STATUSES = {"nonzero_exit", "flag_mismatch"}
_TIMEOUT_STATUSES = {"timeout"}
_SOLVER_CODES = {
    "missing_dependency",
    "flag_mismatch",
    "nonzero_exit",
    "pwn_bad_offset",
    "pwn_bruteforce_timeout",
    "pwn_payload_no_flag",
    "pwn_prompt_mismatch",
    "pwn_rop_missing_gadget",
    "pwn_rop_stack_alignment",
    "pwn_bad_libc_base",
    "pwn_libc_leak_failed",
    "pwn_pie_base_failed",
    "pwn_shell_no_flag",
    "pwn_remote_local_mismatch",
    "pwn_prompt_eof",
}
_VOLATILE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/(?:workspace/executions|root/ctf-skills/work/executions)/[^\s\"')]+"), "<workspace-path>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b(?:container|container_id|cid)(?:=|:|\s+)[0-9a-fA-F]{12,64}\b"), "container=<id>"),
    (re.compile(r"\b[0-9a-fA-F]{32,64}\b"), "<hex>"),
    (re.compile(r"\b(?:elapsed|duration|time)=?\s*\d+(?:\.\d+)?s\b", re.I), "elapsed=<time>"),
    (re.compile(r"\b(?:port|CHAL_PORT)=?\s*\d{2,5}\b"), "port=<port>"),
    (re.compile(r"(?:127\.0\.0\.1|localhost):\d{2,5}"), "localhost:<port>"),
    (re.compile(r"\b0x[0-9a-fA-F]{6,}\b"), "0x<addr>"),
)


def normalized_validation_failure_class(
    result: Mapping[str, Any],
    *,
    runner_phase: str | None = "validation",
) -> str | None:
    """Map a failed validation result to the first-rollout class set."""
    if runner_phase and runner_phase != "validation":
        return None
    if str(result.get("hermes_phase") or "") in _NO_VALIDATION_CLASS_PHASES:
        return None
    if result.get("solve_status") not in {None, "failed"}:
        return None

    status = str(result.get("validation_status") or result.get("status") or "").strip()
    detail_items = _failure_details(result)
    detail_codes = {str(item.get("code") or "").strip() for item in detail_items}
    detail_phases = {str(item.get("phase") or "").strip() for item in detail_items}

    if status in _TIMEOUT_STATUSES or "timeout" in detail_codes:
        return "timeout"
    if detail_codes & _READINESS_CODES:
        return "service-readiness"
    if "pwn_prompt_eof" in detail_codes and not _readiness_established(result, detail_items):
        return "service-readiness"
    if status in _CONTRACT_STATUSES or detail_phases & {"contract", "gate"}:
        return "contract"
    if status in _SOLVER_STATUSES or detail_codes & _SOLVER_CODES:
        return "solver"
    if result.get("validation_contract_errors") or result.get("contract_errors"):
        return "contract"
    if result.get("solve_status") == "failed":
        return "solver"
    return None


def validation_failure_signature(
    result: Mapping[str, Any],
    *,
    failure_class: str | None = None,
) -> str | None:
    """Derive a compact invocation-local signature for repeated-failure checks."""
    failure_class = failure_class or normalized_validation_failure_class(result)
    if failure_class is None:
        return None

    parts: list[str] = [failure_class]
    status = str(result.get("validation_status") or result.get("status") or "").strip()
    if status:
        parts.append(f"status={status}")

    details = _failure_details(result)
    if details:
        detail = details[0]
        for key in ("code", "phase", "path"):
            value = _stable_text(detail.get(key))
            if value:
                parts.append(f"{key}={value}")
        message = _stable_text(detail.get("message"))
        marker = _message_marker(message)
        if marker:
            parts.append(f"message={marker}")

    text = _combined_text(result)
    missing_module = _missing_module(text)
    if missing_module:
        parts.append(f"missing_module={missing_module}")
    traceback_frame = _traceback_frame(text)
    if traceback_frame:
        parts.append(f"frame={traceback_frame}")
    prompt_marker = _prompt_marker(text)
    if prompt_marker:
        parts.append(f"prompt={prompt_marker}")
    if not details:
        marker = _message_marker(text)
        if marker:
            parts.append(f"text={marker}")

    signature = "|".join(part for part in parts if part)
    return _normalize_signature_text(signature)[:400]


def annotate_validation_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with normalized class/signature fields when applicable."""
    annotated = dict(result)
    failure_class = normalized_validation_failure_class(annotated)
    if failure_class:
        annotated["validation_failure_class"] = failure_class
        signature = validation_failure_signature(annotated, failure_class=failure_class)
        if signature:
            annotated["validation_failure_signature"] = signature
    return annotated


def attempt_level_validation_failure(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize one-attempt-to-one-challenge validation failure data.

    Multi-challenge failures intentionally do not guess a single attempt class.
    """
    failed = [result for result in results if result.get("solve_status") == "failed"]
    if not failed:
        return {}
    if len(failed) != 1:
        return {"failed_count": len(failed)}
    result = annotate_validation_result(failed[0])
    return {
        key: value
        for key in (
            "challenge_id",
            "validation_status",
            "validation_error",
            "validation_failure_class",
            "validation_failure_signature",
            "validation_failure_details",
            "validation_contract_errors",
            "validation_stdout_tail",
            "validation_stderr_tail",
            "validation_command",
            "validation_returncode",
            "validation_final_flag_candidate",
            "validation_diagnostic_unavailable",
        )
        if (value := result.get(key)) not in (None, "", [])
    }


def latest_failed_validation(
    paths: ProjectPaths,
    attempt_id: UUID | str,
    *,
    report_path: Path | None = None,
    progress_messages: Sequence[str] | None = None,
    artifact_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Derive the latest failed validation summary for a build attempt.

    Source precedence follows the change design: validation history first, then
    report entries, progress messages, and artifact metadata.
    """
    attempt_root = paths.executions / str(attempt_id)
    history = read_json(attempt_root / "current" / "state" / "validation-history.json", None)
    if isinstance(history, list):
        for entry in reversed(history):
            summary = summarize_validation_entry(entry, source="validation-history")
            if summary:
                return summary

    for candidate in _candidate_report_paths(attempt_root, report_path):
        report = read_json(candidate, None)
        summary = _summarize_report(report)
        if summary:
            summary["source"] = "report"
            return summary

    if progress_messages:
        for message in reversed(progress_messages):
            summary = _summarize_progress_message(message)
            if summary:
                return summary

    if artifact_metadata:
        summary = _summarize_single_result(artifact_metadata, source="artifact-metadata")
        if summary:
            return summary
    return None


def summarize_validation_entry(entry: Any, *, source: str = "validation-history") -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    results = entry.get("results")
    if not isinstance(results, list):
        return None
    failed = [result for result in results if isinstance(result, Mapping) and result.get("solve_status") == "failed"]
    if not failed:
        return None
    if len(failed) != 1:
        return {
            "source": source,
            "round": entry.get("round"),
            "failed_count": len(failed),
        }
    summary = _summarize_single_result(failed[0], source=source)
    if summary is not None:
        summary["round"] = entry.get("round")
    return summary


def _summarize_single_result(result: Mapping[str, Any], *, source: str) -> dict[str, Any] | None:
    if result.get("solve_status") not in {"failed", None}:
        return None
    normalized = annotate_validation_result(_canonical_result(result))
    summary = {
        "source": source,
        "challenge_id": normalized.get("challenge_id"),
        "validation_status": normalized.get("validation_status"),
        "validation_error": _stable_text(normalized.get("validation_error"), limit=1000),
        "validation_failure_class": normalized.get("validation_failure_class"),
        "validation_failure_signature": normalized.get("validation_failure_signature"),
        "validation_failure_details": normalized.get("validation_failure_details"),
        "validation_contract_errors": normalized.get("validation_contract_errors"),
        "validation_stdout_tail": _stable_text(normalized.get("validation_stdout_tail"), limit=1000),
        "validation_stderr_tail": _stable_text(normalized.get("validation_stderr_tail"), limit=1000),
        "validation_command": normalized.get("validation_command"),
        "validation_returncode": normalized.get("validation_returncode"),
        "validation_final_flag_candidate": _stable_text(
            normalized.get("validation_final_flag_candidate"),
            limit=200,
        ),
        "validation_diagnostic_unavailable": normalized.get("validation_diagnostic_unavailable"),
        "failure_kind": normalized.get("failure_kind"),
        "failure_hint": _stable_text(normalized.get("failure_hint"), limit=1000),
        "failed_step": _stable_text(normalized.get("failed_step"), limit=1000),
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [])}


def _canonical_result(result: Mapping[str, Any]) -> dict[str, Any]:
    canonical = dict(result)
    if "challenge_id" not in canonical and isinstance(canonical.get("id"), str):
        canonical["challenge_id"] = canonical["id"]
    if "validation_failure_details" not in canonical and "failure_details" in canonical:
        canonical["validation_failure_details"] = canonical.get("failure_details")
    return canonical


def _summarize_report(report: Any) -> dict[str, Any] | None:
    if not isinstance(report, Mapping):
        return None
    challenges = report.get("challenges")
    if not isinstance(challenges, list):
        return None
    return summarize_validation_entry({"round": None, "results": challenges}, source="report")


def _summarize_progress_message(message: str) -> dict[str, Any] | None:
    text = message.strip()
    if not text:
        return None
    status_match = re.search(r"\bstatus=([^\s]+)", text)
    if not status_match:
        return None
    error_match = re.search(r"\berror=(.+)$", text)
    result = {
        "solve_status": "failed",
        "validation_status": status_match.group(1),
        "validation_error": error_match.group(1).strip() if error_match else text,
    }
    summary = _summarize_single_result(result, source="progress-message")
    return summary


def _candidate_report_paths(attempt_root: Path, report_path: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if report_path is not None:
        candidates.append(report_path)
    candidates.extend(
        [
            attempt_root / "current" / "logs" / "report.json",
            attempt_root / "logs" / "report.json",
        ]
    )
    attempts = attempt_root / "attempts"
    if attempts.is_dir():
        candidates.extend(
            item / "logs" / "report.json"
            for item in sorted(attempts.glob("iter-*"), reverse=True)
            if item.is_dir()
        )
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _failure_details(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = result.get("validation_failure_details") or result.get("failure_details")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _readiness_established(
    result: Mapping[str, Any],
    details: Sequence[Mapping[str, Any]],
) -> bool:
    values: list[Any] = [
        result.get("service_readiness"),
        result.get("readiness_probe_status"),
        result.get("readiness_status"),
        result.get("readiness_established"),
    ]
    for detail in details:
        values.extend(
            [
                detail.get("service_readiness"),
                detail.get("readiness"),
                detail.get("readiness_probe_status"),
                detail.get("readiness_established"),
            ]
        )
    for value in values:
        if value is True:
            return True
        if isinstance(value, str) and value.strip().lower() in {
            "ready",
            "passed",
            "ok",
            "established",
            "true",
        }:
            return True
    return False


def _combined_text(result: Mapping[str, Any]) -> str:
    fields = (
        "validation_error",
        "validation_stderr_tail",
        "validation_stdout_tail",
        "stderr_tail",
        "stdout_tail",
        "error",
    )
    return "\n".join(str(result.get(field) or "") for field in fields)


def _stable_text(value: Any, *, limit: int = 240) -> str | None:
    if value is None:
        return None
    text = _normalize_signature_text(str(value).strip())
    if not text:
        return None
    return text[:limit]


def _normalize_signature_text(text: str) -> str:
    normalized = text
    for pattern, replacement in _VOLATILE_REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _missing_module(text: str) -> str | None:
    patterns = (
        r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]",
        r"No module named ['\"]([^'\"]+)['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _normalize_signature_text(match.group(1))[:80]
    return None


def _traceback_frame(text: str) -> str | None:
    matches = re.findall(r'File "([^"]+)", line \d+, in ([^\n]+)', text)
    if not matches:
        return None
    path, function = matches[-1]
    return f"{Path(path).name}:{_normalize_signature_text(function)[:80]}"


def _prompt_marker(text: str) -> str | None:
    patterns = (
        r"recvuntil\(([^)\n]+)\)",
        r"readuntil\(([^)\n]+)\)",
        r"prompt(?: marker)?[:=]\s*([^\n]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _normalize_signature_text(match.group(1).strip())[:80]
    return None


def _message_marker(text: str | None) -> str | None:
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    return _normalize_signature_text(lines[-1])[:160]
