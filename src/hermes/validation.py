"""生成题目的校验编排。

协调以下步骤完成一个分片中所有题目的校验工作:
  1. 检查断点恢复计划中的跳过标志
  2. 执行质量门（validate_gate）检查
  3. 运行确定性的 ChallengeValidator
  4. 写入 validate 阶段的进度事件
  5. 记录每个题目的最终完成状态
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.paths import ProjectPaths, category_of
from core.state import ProgressStore
from domain.resume import (
    ChallengeResumePlan,
    build_evidence,
    design_evidence,
    document_evidence,
    find_challenge_directory,
    implement_evidence,
    validator_message,
)
from domain.validation import ChallengeValidator


def run_validation(
    *,
    state: ProgressStore,
    validator: ChallengeValidator,
    paths: ProjectPaths,
    image_exists: Callable[[str], bool],
    original_shard_name: str,
    worker: str,
    challenge_ids: list[str],
    plan_by_id: dict[str, ChallengeResumePlan],
) -> list[dict[str, Any]]:
    """对分片中的所有题目执行校验。

    参数:
        state: 进度存储实例
        validator: 题目校验器
        paths: 项目路径
        image_exists: Docker 镜像检查函数
        original_shard_name: 分片名
        worker: Worker 名
        challenge_ids: 要校验的题目 ID 列表
        plan_by_id: 每个题目的恢复计划（key=challenge_id）

    返回:
        每个题目的校验结果列表，每项包含 challenge_id、solve_status、validation_status 等。

    校验流程（对每个题目）:
      1. 如果恢复计划标记 validate 已跳过 → 直接返回 passed
      2. 执行质量门检查 → 不通过则记录失败并跳过
      3. 写入 validate running 事件
      4. 执行 ChallengeValidator.validate_challenge()
      5. 根据结果写入 passed/failed 事件
    """
    results: list[dict[str, Any]] = []
    for challenge_id in challenge_ids:
        plan = _refresh_missing_directory(paths, plan_by_id.get(challenge_id))

        # 情况 1: 断点恢复中 validate 已完成
        if plan is not None and "validate" in plan.skipped_stages:
            results.append(
                {
                    "challenge_id": challenge_id,
                    "solve_status": "passed",
                    "validation_status": "skipped_resume",
                }
            )
            continue

        # 情况 2: 质量门检查（不通过则直接记录失败）
        gate_error = validate_gate(challenge_id, plan, paths, image_exists)
        if gate_error is not None:
            state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="validate",
                status="failed",
                message=validator_message(status="contract_failed", error=gate_error),
            )
            results.append(
                {
                    "challenge_id": challenge_id,
                    "solve_status": "failed",
                    "validation_status": "contract_failed",
                    "validation_error": gate_error,
                }
            )
            continue

        # 情况 3: 质量门通过 → 执行正式校验
        state.record(
            shard=original_shard_name,
            challenge_id=challenge_id,
            worker=worker,
            stage="validate",
            status="running",
            message=validator_message(status="running"),
        )
        outcome = validator.validate_challenge(challenge_id)
        elapsed = outcome.get("elapsed")

        if outcome.get("status") == "passed":
            # 校验通过
            state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="validate",
                status="passed",
                message=validator_message(
                    status="passed", elapsed=elapsed, flag_matched=True
                ),
            )
            results.append(
                {
                    "challenge_id": challenge_id,
                    "solve_status": "passed",
                    "validation_status": "passed",
                    "validation_elapsed": elapsed,
                }
            )
        else:
            # 校验失败（状态可能是 nonzero_exit / flag_mismatch / timeout 等）
            status = str(outcome.get("status", "failed"))
            error = outcome.get("error")
            state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="validate",
                status="failed",
                message=validator_message(status=status, elapsed=elapsed, error=error),
            )
            results.append(
                {
                    "challenge_id": challenge_id,
                    "solve_status": "failed",
                    "validation_status": status,
                    "validation_elapsed": elapsed,
                    "validation_error": error,
                }
            )
    return results


def _refresh_missing_directory(
    paths: ProjectPaths,
    plan: ChallengeResumePlan | None,
) -> ChallengeResumePlan | None:
    if plan is None or plan.directory is not None:
        return plan

    lookup = find_challenge_directory(paths, plan.challenge_id)
    return ChallengeResumePlan(
        challenge_id=plan.challenge_id,
        directory=lookup.directory,
        lookup_status=lookup.status,
        skipped_stages=plan.skipped_stages,
        first_pending_stage=plan.first_pending_stage,
        stage_sources=plan.stage_sources,
    )


def validate_gate(
    challenge_id: str,
    plan: ChallengeResumePlan | None,
    paths: ProjectPaths,
    image_exists: Callable[[str], bool],
) -> str | None:
    """执行质量门检查，验证题目的磁盘证据是否齐全。

    检查项（按顺序）:
      1. 恢复计划条目是否存在
      2. 题目目录是否存在
      3. design / implement / build / document 各阶段证据是否完整
      4. validate.sh 和 writenup/exp.py 文件是否存在

    返回:
        None 表示检查通过；否则返回错误描述字符串。
    """
    if plan is None:
        return "no resume plan entry"
    if plan.directory is None:
        return plan.lookup_status

    category = category_of(plan.directory, paths)

    # 按阶段顺序检查证据
    if not design_evidence(plan.directory, challenge_id):
        return "design evidence incomplete"
    if not implement_evidence(plan.directory, category):
        return "implement evidence incomplete"
    if not build_evidence(plan.directory, category, image_exists):
        return "build evidence incomplete"
    if not document_evidence(plan.directory):
        return "document evidence incomplete"

    # 检查校验所需的前置文件
    if not (plan.directory / "validate.sh").is_file():
        return "validate.sh missing"
    if not (plan.directory / "writenup" / "exp.py").is_file():
        return "writenup/exp.py missing"

    return None  # 全部检查通过


def record_per_challenge_complete(
    state: ProgressStore,
    original_shard_name: str,
    worker: str,
    per_results: list[dict[str, Any]],
) -> None:
    """根据每个题目的校验结果，写入 complete 阶段的进度事件。

    对每个题目:
      - solve_status 为 "passed" → complete passed
      - 否则 → complete failed
    """
    for result in per_results:
        status = "passed" if result.get("solve_status") == "passed" else "failed"
        state.record(
            shard=original_shard_name,
            challenge_id=result["challenge_id"],
            worker=worker,
            stage="complete",
            status=status,
            message=str(result.get("validation_status", "")),
        )


