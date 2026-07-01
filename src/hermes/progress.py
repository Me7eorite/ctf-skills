"""Hermes 执行过程的进度和报告辅助函数。

提供分片执行完成时的进度事件写入和 JSON 报告文件管理。
"""

from __future__ import annotations

from pathlib import Path

from core.clock import beijing_now_isoformat
from core.jsonio import read_json, write_json
from core.state import ProgressStore


def record_final(
    state: ProgressStore,
    shard: str,
    challenge_ids: list[str],
    worker: str,
    status: str,
    message: str,
) -> None:
    """记录分片最终的完成事件。

    对每个 challenge 分别写入一条 complete 事件，
    再为整个分片写入一条分片级别的 complete 事件。
    """
    # 每个题目一条 complete 事件
    for challenge_id in challenge_ids:
        state.record(
            shard=shard,
            challenge_id=challenge_id,
            worker=worker,
            stage="complete",
            status=status,
            message=message,
        )
    # 分片级别的 complete 事件
    state.record(
        shard=shard,
        worker=worker,
        stage="complete",
        status=status,
        message=message,
    )


def ensure_report(path: Path, shard: Path, worker: str, status: str, returncode: int) -> None:
    """确保报告文件存在。

    如果报告文件已存在则跳过（不覆盖已有数据）；
    否则创建包含基本信息的初始报告。

    这是幂等操作，支持断点恢复场景。
    """
    if path.exists():
        return
    write_json(
        path,
        {
            "shard": str(shard),
            "status": status,
            "worker": worker,
            "returncode": returncode,
            "updated_at": beijing_now_isoformat(),
        },
    )


def update_report(path: Path, status: str, error: str | None = None) -> None:
    """更新报告文件中的运行器状态字段。

    保留原有数据，仅更新 runner_status、runner_error 和 runner_updated_at 字段。

    参数:
        path: 报告文件路径
        status: 运行器状态（如 "passed" / "failed"）
        error: 可选的错误描述
    """
    report = read_json(path, {})
    report.update(
        {
            "runner_status": status,
            "runner_error": error,
            "runner_updated_at": beijing_now_isoformat(),
        }
    )
    write_json(path, report)
