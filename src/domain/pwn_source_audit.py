"""Lightweight source-level checks for generated pwn challenges.

This module intentionally stays conservative and text-based. It does not try to
prove exploitability; it only catches early drift where the generated C source
does not contain the primitive declared by the design/build contract.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AuditPriority = Literal[
    "challenge_escape",
    "disqualifier",
    "missing_evidence",
    "not_realized",
]


@dataclass(frozen=True)
class PwnSourceAuditFinding:
    code: str
    message: str
    path: str | None
    line: int | None
    priority: AuditPriority
    technique: str
    evidence: dict[str, str]
    hint: str


@dataclass(frozen=True)
class _CSource:
    path: Path
    relative_path: str
    text: str


@dataclass(frozen=True)
class _OverflowEvidence:
    path: str
    line: int
    buffer: str
    buffer_size: int
    call: str
    write_size: str


@dataclass(frozen=True)
class _SafeBoundEvidence:
    path: str
    line: int
    buffer: str
    buffer_size: int
    call: str


_MAX_SOURCE_BYTES = 512 * 1024
_SOURCE_ROOTS = ("deploy/src", "src", "deploy")
_EXCLUDED_PARTS = {"attachments", "writenup", "state", "__pycache__"}
_STACK_BUFFER_RE = re.compile(
    r"\b(?:char|unsigned\s+char|uint8_t|int8_t)\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]"
)
_FORMAT_SINK_RE = re.compile(
    r"\b(?:printf|syslog)\s*\(\s*([A-Za-z_]\w*)\s*\)"
    r"|\b(?:fprintf|dprintf)\s*\(\s*[^,]+,\s*([A-Za-z_]\w*)\s*(?:,|\))"
    r"|\b(?:sprintf|snprintf)\s*\(\s*[^,]+,\s*(?:[^,]+,\s*)?([A-Za-z_]\w*)\s*(?:,|\))",
    re.MULTILINE,
)
_SAFE_FORMAT_RE = re.compile(
    r"\b(?:printf|fprintf|dprintf|sprintf|snprintf|syslog)\s*\([^;\n]*\"[^\"]*%s[^\"]*\"",
    re.MULTILINE,
)
_FIXED_SECRET_RE = re.compile(
    r"\b(?:strcmp|strncmp|memcmp)\s*\([^;\n]*\"([^\"\\\r\n]{3,80})\"[^;\n]*\)",
    re.MULTILINE,
)
_FLAG_LITERAL_RE = re.compile(r"flag\{[^}\r\n]{1,200}\}", re.IGNORECASE)
_DEBUG_WORD_RE = re.compile(r"\b(?:debug|backdoor|testmode|devmode|admin_secret)\b", re.IGNORECASE)
_FLAG_OR_SHELL_RE = re.compile(
    r"(?:/bin/sh|/flag|cat\s+/flag|print_flag\s*\(|read_flag\s*\(|system\s*\()",
    re.IGNORECASE,
)
_LIBC_CALL_RE = re.compile(r"\b(?:puts|printf|read|write|gets|fgets|scanf|recv)\s*\(")
_LEAK_RE = re.compile(
    r"(?:%p|printf\s*\([^;\n]*%p|puts\s*\([^;\n]*got|write\s*\([^;\n]*(?:got|puts|printf))",
    re.IGNORECASE,
)
_REENTRY_RE = re.compile(r"\b(?:while|for)\s*\(|\bmain\s*\(|\bvuln\s*\([^;\n]*\)\s*;", re.MULTILINE)


def audit_pwn_c_sources(
    challenge_dir: Path,
    metadata: Mapping[str, Any],
    build_contract: Mapping[str, Any] | None = None,
) -> PwnSourceAuditFinding | None:
    """Return the first blocking source-audit finding for recognized pwn techniques.

    Unknown techniques are not blocked by the MVP. Recognized techniques are:
    ret2win/stack overflow, format string, and ret2libc.
    """

    technique = _declared_technique(metadata, build_contract)
    if not technique:
        return None

    sources = _collect_c_sources(challenge_dir)
    if not sources:
        return PwnSourceAuditFinding(
            code="pwn_source_audit_no_c_source",
            message="declared pwn technique but no C source files were found before build",
            path=None,
            line=None,
            priority="missing_evidence",
            technique=technique,
            evidence={"source_roots": ", ".join(_SOURCE_ROOTS)},
            hint="Add the generated .c source under deploy/src, src, or deploy before Docker build.",
        )

    escape = _challenge_escape_finding(sources, technique)
    if escape is not None:
        return escape

    if _is_format_string(technique):
        return _format_string_finding(sources, technique)
    if _is_ret2win(technique):
        return _ret2win_finding(sources, technique)
    if _is_ret2libc(technique) or _is_stack_overflow(technique):
        return _ret2libc_or_stack_finding(sources, technique)
    return None


def _declared_technique(
    metadata: Mapping[str, Any],
    build_contract: Mapping[str, Any] | None,
) -> str:
    labels: list[str] = []
    for key in ("primary_technique", "technique"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            labels.append(value.strip())
    if isinstance(build_contract, Mapping):
        profile = build_contract.get("required_profile")
        if isinstance(profile, Mapping):
            semantic = profile.get("semantic")
            if isinstance(semantic, Mapping):
                for key in ("sub_technique", "family"):
                    value = semantic.get(key)
                    if isinstance(value, str) and value.strip():
                        labels.append(value.strip())
    embedded_contract = metadata.get("build_contract")
    if isinstance(embedded_contract, Mapping):
        labels.append(_declared_technique({}, embedded_contract))
    return " ".join(label for label in labels if label).lower()


def _collect_c_sources(challenge_dir: Path) -> list[_CSource]:
    paths: list[Path] = []
    for root in _SOURCE_ROOTS:
        source_root = challenge_dir / root
        if source_root.is_file() and source_root.suffix == ".c":
            paths.append(source_root)
        elif source_root.is_dir():
            paths.extend(source_root.rglob("*.c"))
    unique_paths = sorted({path.resolve() for path in paths})
    sources: list[_CSource] = []
    for path in unique_paths:
        try:
            relative = path.relative_to(challenge_dir.resolve()).as_posix()
        except ValueError:
            continue
        if any(part in _EXCLUDED_PARTS for part in Path(relative).parts):
            continue
        try:
            if path.stat().st_size > _MAX_SOURCE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sources.append(_CSource(path=path, relative_path=relative, text=text))
    return sources


def _challenge_escape_finding(
    sources: list[_CSource],
    technique: str,
) -> PwnSourceAuditFinding | None:
    for source in sources:
        flag_match = _FLAG_LITERAL_RE.search(source.text)
        if flag_match:
            return _finding(
                code="pwn_source_audit_plaintext_flag_exposure",
                message="C source contains a plaintext flag literal, which bypasses the declared pwn primitive",
                source=source,
                index=flag_match.start(),
                priority="challenge_escape",
                technique=technique,
                evidence={"literal": flag_match.group(0)[:80]},
                hint=(
                    "Remove the real flag from source; read it only from the "
                    "protected runtime flag path after exploitation."
                ),
            )
        secret_match = _FIXED_SECRET_RE.search(source.text)
        if secret_match and _nearby_flag_or_shell(source.text, secret_match.start()):
            secret_line = _line_at(source.text, secret_match.start()).strip()
            return _finding(
                code="pwn_source_audit_fixed_secret_escape",
                message="C source contains a fixed password/token path to the flag or shell",
                source=source,
                index=secret_match.start(),
                priority="challenge_escape",
                technique=technique,
                evidence={"secret": secret_match.group(1), "call": secret_line},
                hint=(
                    "Remove the fixed credential shortcut or change the "
                    "declared technique to match the actual intended path."
                ),
            )
        debug_match = _DEBUG_WORD_RE.search(source.text)
        if debug_match and _nearby_flag_or_shell(source.text, debug_match.start()):
            return _finding(
                code="pwn_source_audit_debug_backdoor_escape",
                message="C source contains a debug/backdoor path to the flag or shell",
                source=source,
                index=debug_match.start(),
                priority="challenge_escape",
                technique=technique,
                evidence={"line": _line_at(source.text, debug_match.start()).strip()},
                hint="Remove debug/backdoor commands from the player-reachable code path.",
            )
    return None


def _format_string_finding(
    sources: list[_CSource],
    technique: str,
) -> PwnSourceAuditFinding | None:
    for source in sources:
        match = _FORMAT_SINK_RE.search(source.text)
        if match:
            variable = next(group for group in match.groups() if group)
            return None if variable else None
    safe = _first_match(sources, _SAFE_FORMAT_RE)
    if safe is not None:
        source, match = safe
        return _finding(
            code="pwn_source_audit_format_string_not_realized",
            message="declared format string, but C source only shows bounded/constant format printing",
            source=source,
            index=match.start(),
            priority="not_realized",
            technique=technique,
            evidence={"call": _line_at(source.text, match.start()).strip()},
            hint=(
                "Pass player-controlled input as the format argument, for "
                "example printf(name), or update the declared technique."
            ),
        )
    return PwnSourceAuditFinding(
        code="pwn_source_audit_format_string_not_realized",
        message="declared format string, but no player-controlled format sink was found in C source",
        path=None,
        line=None,
        priority="not_realized",
        technique=technique,
        evidence={"expected": "printf(user_input) or equivalent"},
        hint=(
            "Add a reachable format sink using player-controlled input as the "
            "format string, or update the declared technique."
        ),
    )


def _ret2win_finding(
    sources: list[_CSource],
    technique: str,
) -> PwnSourceAuditFinding | None:
    overflow = _first_overflow(sources)
    if overflow is None:
        return _stack_missing_finding(sources, technique)
    if not _has_ret2win_target(sources):
        return PwnSourceAuditFinding(
            code="pwn_source_audit_ret2win_target_missing",
            message="declared ret2win, but no win/flag/shell target function was found in C source",
            path=overflow.path,
            line=overflow.line,
            priority="missing_evidence",
            technique=technique,
            evidence={
                "overflow_call": overflow.call,
                "buffer": f"{overflow.buffer}[{overflow.buffer_size}]",
            },
            hint="Add an unreachable win/print_flag/read_flag/shell target, or update the declared technique.",
        )
    return None


def _ret2libc_or_stack_finding(
    sources: list[_CSource],
    technique: str,
) -> PwnSourceAuditFinding | None:
    overflow = _first_overflow(sources)
    if overflow is None:
        return _stack_missing_finding(sources, technique)
    if _is_ret2libc(technique):
        haystack = "\n".join(source.text for source in sources)
        if not _LIBC_CALL_RE.search(haystack):
            return PwnSourceAuditFinding(
                code="pwn_source_audit_ret2libc_libc_call_missing",
                message="declared ret2libc, but no libc-facing call was found in C source",
                path=overflow.path,
                line=overflow.line,
                priority="missing_evidence",
                technique=technique,
                evidence={"overflow_call": overflow.call},
                hint="Use normal libc I/O calls in the target or change the declared technique.",
            )
        if "leak" in technique and not (_LEAK_RE.search(haystack) or _REENTRY_RE.search(haystack)):
            return PwnSourceAuditFinding(
                code="pwn_source_audit_ret2libc_leak_not_realized",
                message="declared ret2libc with leak, but no source-level leak or second-stage entry was found",
                path=overflow.path,
                line=overflow.line,
                priority="not_realized",
                technique=technique,
                evidence={"overflow_call": overflow.call},
                hint=(
                    "Add an address leak or a repeated vulnerable entry for "
                    "leak-then-final payload, or update the declared technique."
                ),
            )
    return None


def _stack_missing_finding(
    sources: list[_CSource],
    technique: str,
) -> PwnSourceAuditFinding:
    safe = _first_safe_bound(sources)
    if safe is not None:
        return PwnSourceAuditFinding(
            code="pwn_source_audit_bounded_read_disqualifies_overflow",
            message="declared stack overflow, but the observed stack input is bounded to the buffer size",
            path=safe.path,
            line=safe.line,
            priority="disqualifier",
            technique=technique,
            evidence={
                "buffer": f"{safe.buffer}[{safe.buffer_size}]",
                "call": safe.call,
            },
            hint=(
                "Use a reachable stack buffer with an input/copy bound larger "
                "than the buffer, or update the declared technique."
            ),
        )
    return PwnSourceAuditFinding(
        code="pwn_source_audit_stack_overflow_not_realized",
        message="declared stack overflow/ret2libc/ret2win, but no overflowing stack input path was found in C source",
        path=None,
        line=None,
        priority="not_realized",
        technique=technique,
        evidence={"expected": "stack buffer plus unbounded or oversized input/copy"},
        hint="Add a reachable stack buffer and an oversized read/copy into it, or update the declared technique.",
    )


def _first_overflow(sources: list[_CSource]) -> _OverflowEvidence | None:
    for source in sources:
        for buffer, size in _stack_buffers(source.text):
            evidence = _overflow_for_buffer(source, buffer, size)
            if evidence is not None:
                return evidence
    return None


def _first_safe_bound(sources: list[_CSource]) -> _SafeBoundEvidence | None:
    for source in sources:
        for buffer, size in _stack_buffers(source.text):
            evidence = _safe_bound_for_buffer(source, buffer, size)
            if evidence is not None:
                return evidence
    return None


def _stack_buffers(text: str) -> list[tuple[str, int]]:
    return [(match.group(1), int(match.group(2))) for match in _STACK_BUFFER_RE.finditer(text)]


def _overflow_for_buffer(source: _CSource, buffer: str, size: int) -> _OverflowEvidence | None:
    patterns = [
        (rf"\bgets\s*\(\s*{re.escape(buffer)}\s*\)", "unbounded"),
        (rf"\bstrcpy\s*\(\s*{re.escape(buffer)}\s*,", "unbounded"),
        (rf"\bsprintf\s*\(\s*{re.escape(buffer)}\s*,", "unbounded"),
    ]
    for pattern, write_size in patterns:
        match = re.search(pattern, source.text)
        if match:
            return _overflow_evidence(source, match, buffer, size, write_size)

    numeric_patterns = [
        rf"\bread\s*\([^,]+,\s*{re.escape(buffer)}\s*,\s*(\d+)\s*\)",
        rf"\brecv\s*\([^,]+,\s*{re.escape(buffer)}\s*,\s*(\d+)\s*,",
        rf"\bfgets\s*\(\s*{re.escape(buffer)}\s*,\s*(\d+)\s*,",
        rf"\bmemcpy\s*\(\s*{re.escape(buffer)}\s*,[^,]+,\s*(\d+)\s*\)",
        rf"\bstrncpy\s*\(\s*{re.escape(buffer)}\s*,[^,]+,\s*(\d+)\s*\)",
    ]
    for pattern in numeric_patterns:
        match = re.search(pattern, source.text)
        if match and int(match.group(1)) > size:
            return _overflow_evidence(source, match, buffer, size, match.group(1))

    scanf_pattern = rf"\bscanf\s*\(\s*\"([^\"]*)\"\s*,\s*&?\s*{re.escape(buffer)}\s*\)"
    match = re.search(scanf_pattern, source.text)
    if match:
        width = _scanf_s_width(match.group(1))
        if width is None or width >= size:
            return _overflow_evidence(source, match, buffer, size, width and str(width) or "unbounded")
    return None


def _safe_bound_for_buffer(source: _CSource, buffer: str, size: int) -> _SafeBoundEvidence | None:
    safe_patterns = [
        rf"\bfgets\s*\(\s*{re.escape(buffer)}\s*,\s*sizeof\s*\(?\s*{re.escape(buffer)}\s*\)?\s*,",
        rf"\bread\s*\([^,]+,\s*{re.escape(buffer)}\s*,\s*sizeof\s*\(?\s*{re.escape(buffer)}\s*\)?\s*\)",
        rf"\brecv\s*\([^,]+,\s*{re.escape(buffer)}\s*,\s*sizeof\s*\(?\s*{re.escape(buffer)}\s*\)?\s*,",
    ]
    for pattern in safe_patterns:
        match = re.search(pattern, source.text)
        if match:
            return _safe_evidence(source, match, buffer, size)

    numeric_patterns = [
        rf"\bfgets\s*\(\s*{re.escape(buffer)}\s*,\s*(\d+)\s*,",
        rf"\bread\s*\([^,]+,\s*{re.escape(buffer)}\s*,\s*(\d+)\s*\)",
        rf"\brecv\s*\([^,]+,\s*{re.escape(buffer)}\s*,\s*(\d+)\s*,",
    ]
    for pattern in numeric_patterns:
        match = re.search(pattern, source.text)
        if match and int(match.group(1)) <= size:
            return _safe_evidence(source, match, buffer, size)

    scanf_pattern = rf"\bscanf\s*\(\s*\"([^\"]*)\"\s*,\s*&?\s*{re.escape(buffer)}\s*\)"
    match = re.search(scanf_pattern, source.text)
    if match:
        width = _scanf_s_width(match.group(1))
        if width is not None and width < size:
            return _safe_evidence(source, match, buffer, size)
    return None


def _overflow_evidence(
    source: _CSource,
    match: re.Match[str],
    buffer: str,
    size: int,
    write_size: str,
) -> _OverflowEvidence:
    return _OverflowEvidence(
        path=source.relative_path,
        line=_line_number(source.text, match.start()),
        buffer=buffer,
        buffer_size=size,
        call=_line_at(source.text, match.start()).strip(),
        write_size=write_size,
    )


def _safe_evidence(
    source: _CSource,
    match: re.Match[str],
    buffer: str,
    size: int,
) -> _SafeBoundEvidence:
    return _SafeBoundEvidence(
        path=source.relative_path,
        line=_line_number(source.text, match.start()),
        buffer=buffer,
        buffer_size=size,
        call=_line_at(source.text, match.start()).strip(),
    )


def _scanf_s_width(format_string: str) -> int | None:
    match = re.search(r"%(?:\*)?(\d*)s", format_string)
    if not match:
        return None
    width = match.group(1)
    return int(width) if width else None


def _has_ret2win_target(sources: list[_CSource]) -> bool:
    haystack = "\n".join(source.text for source in sources)
    if re.search(r"\b(?:win|print_flag|read_flag|shell)\s*\(", haystack):
        return True
    return bool(re.search(r"(?:/flag|/bin/sh)", haystack))


def _nearby_flag_or_shell(text: str, index: int) -> bool:
    start = max(0, index - 500)
    end = min(len(text), index + 700)
    return bool(_FLAG_OR_SHELL_RE.search(text[start:end]))


def _first_match(
    sources: list[_CSource],
    pattern: re.Pattern[str],
) -> tuple[_CSource, re.Match[str]] | None:
    for source in sources:
        match = pattern.search(source.text)
        if match:
            return source, match
    return None


def _finding(
    *,
    code: str,
    message: str,
    source: _CSource,
    index: int,
    priority: AuditPriority,
    technique: str,
    evidence: dict[str, str],
    hint: str,
) -> PwnSourceAuditFinding:
    return PwnSourceAuditFinding(
        code=code,
        message=message,
        path=source.relative_path,
        line=_line_number(source.text, index),
        priority=priority,
        technique=technique,
        evidence=evidence,
        hint=hint,
    )


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _line_at(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return text[start:end]


def _is_ret2win(technique: str) -> bool:
    normalized = _normalize(technique)
    return "ret2win" in normalized or "return to win" in normalized


def _is_stack_overflow(technique: str) -> bool:
    normalized = _normalize(technique)
    return (
        "stack overflow" in normalized
        or "stack buffer overflow" in normalized
        or "buffer overflow" in normalized
    )


def _is_format_string(technique: str) -> bool:
    normalized = _normalize(technique)
    return "format string" in normalized or "fsb" in normalized


def _is_ret2libc(technique: str) -> bool:
    normalized = _normalize(technique)
    return "ret2libc" in normalized or "return to libc" in normalized


def _normalize(value: str) -> str:
    return re.sub(r"[_\-]+", " ", value.lower())
