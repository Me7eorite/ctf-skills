"""UTC 时间工具。

提供唯一权威的 `utcnow()`，避免各模块各自实现导致行为分歧或测试桩注入麻烦。
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """返回当前 UTC 时区的 datetime。"""
    return datetime.now(timezone.utc)
