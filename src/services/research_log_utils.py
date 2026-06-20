"""Safe helpers for reading Hermes research logs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.paths import ProjectPaths

SafeLogErrorCode = Literal[
    "no_log_file",
    "unsafe_log_path",
    "log_too_large",
    "log_unreadable",
]

MAX_RESEARCH_LOG_BYTES = 10 * 1024 * 1024
STDOUT_START_MARKER = "--- stdout ---"
STDOUT_END_MARKER = "--- end stdout ---"


class SafeResearchLogError(ValueError):
    """Raised when a research log cannot be safely read."""

    def __init__(self, code: SafeLogErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SafeResearchLog:
    path: Path
    text: str
    data: bytes


def read_safe_research_log(
    paths: ProjectPaths,
    stored_path: str | Path | None,
    *,
    max_bytes: int = MAX_RESEARCH_LOG_BYTES,
) -> SafeResearchLog:
    """Read a research log only if it resolves under ``paths.research_logs``."""
    if stored_path is None or str(stored_path) == "":
        raise SafeResearchLogError("no_log_file", "research run has no Hermes log path")

    raw = Path(stored_path)
    candidate = raw if raw.is_absolute() else paths.root / raw
    allowed_root = paths.research_logs.resolve()
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SafeResearchLogError("no_log_file", "Hermes log file does not exist") from exc
    except OSError as exc:
        raise SafeResearchLogError("log_unreadable", f"Hermes log path is unreadable: {exc}") from exc

    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise SafeResearchLogError(
            "unsafe_log_path",
            "Hermes log path is outside the allowed research log directory",
        ) from exc

    if not resolved.is_file():
        raise SafeResearchLogError("unsafe_log_path", "Hermes log path is not a regular file")

    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise SafeResearchLogError("log_unreadable", f"Hermes log stat failed: {exc}") from exc
    if size > max_bytes:
        raise SafeResearchLogError("log_too_large", "Hermes log file exceeds the 10 MiB limit")

    try:
        data = resolved.read_bytes()
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SafeResearchLogError("log_unreadable", "Hermes log is not valid UTF-8") from exc
    except OSError as exc:
        raise SafeResearchLogError("log_unreadable", f"Hermes log read failed: {exc}") from exc

    return SafeResearchLog(path=resolved, text=text, data=data)


def has_ordered_stdout_markers(text: str) -> bool:
    """Return true when the Hermes wrapper log contains a complete stdout block."""
    start = text.find(STDOUT_START_MARKER)
    if start == -1:
        return False
    end = text.find(STDOUT_END_MARKER, start + len(STDOUT_START_MARKER))
    return end != -1
