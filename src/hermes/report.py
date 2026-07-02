"""Hermes 执行报告合并工具。

将逐题校验结果合并到分片报告中，并处理 Hermes 生成的异常报告格式。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.jsonio import read_json, write_json


def merge_validation_into_report(
    report: Path,
    per_results: list[dict[str, Any]],
    *,
    shard: Path | None = None,
    worker: str | None = None,
    runner_status: str | None = None,
) -> None:
    """将每个题目的校验结果合并到分片报告 JSON 文件中。

    设计思路:
      - 读-改-写模式：读取现有报告 → 合并校验结果 → 写回
      - 修复 Hermes 可能产生的畸形报告结构（如 challenges 不是 list）
      - 通过 challenge_id 匹配对齐校验结果

    参数:
        report: 报告 JSON 文件路径
        per_results: 每个题目的校验结果列表
        shard: 分片路径（仅在报告不存在时使用）
        worker: Worker 名称（仅在报告不存在时使用）
        runner_status: 运行器状态（有失败则覆盖为 "failed"）
    """
    # 读取现有报告（容错处理畸形数据）
    raw = read_json(report, {})
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("challenges"), list):
        raw["challenges"] = []

    challenges_list = raw["challenges"]

    # 建立 challenge_id → 条目 的索引
    by_id: dict[str, dict[str, Any]] = {}
    for entry in challenges_list:
        if isinstance(entry, dict):
            challenge_id = entry.get("id") or entry.get("challenge_id")
            if isinstance(challenge_id, str):
                by_id[challenge_id] = entry

    # 合并校验结果
    any_failed = False
    for result in per_results:
        challenge_id = result["challenge_id"]
        target = by_id.get(challenge_id)
        if target is None:
            # 新条目：报告中没有这个 challenge
            target = {"id": challenge_id}
            challenges_list.append(target)
        target.setdefault("id", challenge_id)
        target["solve_status"] = result.get("solve_status", "failed")
        target["validation_status"] = result.get(
            "validation_status", target.get("validation_status", "")
        )
        for field in (
            "validation_elapsed",
            "validation_error",
            "validation_returncode",
            "validation_stdout_tail",
            "validation_stderr_tail",
            "validation_contract_errors",
        ):
            if field in result and result[field] is not None:
                target[field] = result[field]
        if target["solve_status"] == "failed":
            any_failed = True

    summary = raw.get("execution_summary")
    if isinstance(summary, dict):
        total = len([entry for entry in challenges_list if isinstance(entry, dict)])
        passed = sum(
            1
            for entry in challenges_list
            if isinstance(entry, dict) and entry.get("solve_status") == "passed"
        )
        failed = sum(
            1
            for entry in challenges_list
            if isinstance(entry, dict) and entry.get("solve_status") == "failed"
        )
        pending = max(total - passed - failed, 0)
        summary["total_challenges"] = total
        summary["passed"] = passed
        summary["failed"] = failed
        summary["pending_validation"] = pending

    # 设置报告级别的元数据（仅在不存在时设置，不覆盖已有值）
    if shard is not None:
        raw.setdefault("shard", str(shard))
    if worker is not None:
        raw.setdefault("worker", worker)
    if runner_status is not None:
        raw["runner_status"] = "failed" if any_failed else runner_status

    write_json(report, raw)
