"""UTC 时间工具。

提供唯一权威的 `utcnow()`，避免各模块各自实现导致行为分歧或测试桩注入麻烦。
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def utcnow() -> datetime:
    """返回当前 UTC 时区的 datetime。"""
    return datetime.now(timezone.utc)


def as_beijing(value: datetime) -> datetime:
    """Convert an aware/naive datetime to Beijing time for operator display."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TZ)


def beijing_isoformat(value: datetime | None) -> str | None:
    """Return ISO-8601 text in Asia/Shanghai, preserving None."""
    return as_beijing(value).isoformat() if value is not None else None


def beijing_isoformat_seconds(value: datetime | None) -> str | None:
    """Return second-precision ISO-8601 text in Asia/Shanghai."""
    if value is None:
        return None
    return as_beijing(value).replace(microsecond=0).isoformat()


def beijing_isoformat_or_dash(value: datetime | None) -> str:
    """Return Beijing ISO text for CLI output."""
    return beijing_isoformat(value) or "-"


def beijing_now_isoformat() -> str:
    """Return current Beijing time as second-precision ISO-8601 text."""
    return beijing_isoformat_seconds(utcnow()) or ""
