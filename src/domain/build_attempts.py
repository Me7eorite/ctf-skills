"""构建尝试的领域 DTO 和状态值集合。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

# 与其他领域模块一致，状态集使用可供成员校验的字符串 tuple。
BuildAttemptStatus: tuple[str, ...] = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "lost",
)


@dataclass(frozen=True)
class BuildAttempt:
    """一次由操作员发起的题目构建记录。"""

    id: UUID
    design_task_id: UUID
    attempt_no: int
    status: str
    shard_basename: str
    worker: str | None
    resulting_challenge_dir: str | None
    artifact_status: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
