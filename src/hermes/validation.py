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
from pathlib import Path
from typing import Any, Protocol

from core.jsonio import read_json
from core.paths import ProjectPaths, category_of
from domain.resume import (
    ChallengeResumePlan,
    build_evidence,
    design_evidence,
    document_evidence_reason,
    find_challenge_directory,
    implement_evidence_reason,
    validator_message,
)
from domain.validation import (
    ChallengeValidator,
    classify_validation_failure,
    pwn_solver_evidence_failures,
    validation_failure_detail,
)
from domain.validation_failure_governance import annotate_validation_result


class ProgressRecorder(Protocol):
    def record(
        self,
        *,
        shard: str,
        stage: str,
        status: str,
        challenge_id: str = "",
        worker: str = "",
        message: str = "",
    ) -> dict: ...


def run_validation(
    *,
    state: ProgressRecorder,
    validator: ChallengeValidator,
    paths: ProjectPaths,
    image_exists: Callable[[str], bool],
    original_shard_name: str,
    worker: str,
    challenge_ids: list[str],
    plan_by_id: dict[str, ChallengeResumePlan],
    validation_targets: dict[str, Path] | None = None,
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
        if validation_targets is not None:
            target = validation_targets.get(challenge_id)
            plan = _bind_validation_target(plan, challenge_id, target)

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
        gate_failure = validate_gate(challenge_id, plan, paths, image_exists)
        if gate_failure is not None:
            gate_status, gate_error, failure_details = _normalize_gate_failure(gate_failure)
            state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="validate",
                status="failed",
                message=validator_message(status=gate_status, error=gate_error),
            )
            results.append(
                annotate_validation_result(
                    {
                        "challenge_id": challenge_id,
                        "solve_status": "failed",
                        "validation_status": gate_status,
                        "validation_error": gate_error,
                        "validation_contract_errors": [gate_error]
                        if gate_status == "contract_failed"
                        else None,
                        "validation_failure_details": failure_details,
                    }
                )
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
        if validation_targets is None:
            outcome = validator.validate_challenge(challenge_id)
        else:
            target = validation_targets.get(challenge_id)
            if target is None:
                outcome = {
                    "challenge_id": challenge_id,
                    "status": "missing_challenge",
                    "error": "claimed workspace output is missing",
                }
            else:
                outcome = validator.validate_path(
                    target,
                    expected_challenge_id=challenge_id,
                )
        elapsed = outcome.get("elapsed")

        if outcome.get("status") == "passed" and outcome.get("final_flag_candidate"):
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
                    "validation_returncode": outcome.get("returncode"),
                    "validation_command": outcome.get("command"),
                    "validation_stdout_tail": outcome.get("stdout_tail"),
                    "validation_stderr_tail": outcome.get("stderr_tail"),
                    "validation_final_flag_candidate": outcome.get("final_flag_candidate"),
                }
            )
        else:
            # 校验失败（状态可能是 nonzero_exit / flag_mismatch / timeout 等）
            status = str(outcome.get("status", "failed"))
            error = outcome.get("error")
            if outcome.get("status") == "passed" and not outcome.get("final_flag_candidate"):
                status = "pending_validation"
                error = (
                    "validator reported passed without a flag candidate from "
                    "validate.sh stdout"
                )
            contract_errors = outcome.get("contract_errors")
            if not error and isinstance(contract_errors, list):
                error = "; ".join(str(item) for item in contract_errors if item)
            raw_failure_details = outcome.get("failure_details")
            if isinstance(raw_failure_details, list):
                failure_details = [
                    item for item in raw_failure_details if isinstance(item, dict)
                ]
            else:
                failure_details = classify_validation_failure(
                    status=status,
                    error=str(error) if error else None,
                    stderr=outcome.get("stderr_tail"),
                    contract_errors=contract_errors
                    if isinstance(contract_errors, list)
                    else None,
                )
            failed_result = annotate_validation_result(
                {
                    "challenge_id": challenge_id,
                    "solve_status": "failed",
                    "validation_status": status,
                    "validation_elapsed": elapsed,
                    "validation_error": error,
                    "validation_returncode": outcome.get("returncode"),
                    "validation_command": outcome.get("command"),
                    "validation_stdout_tail": outcome.get("stdout_tail"),
                    "validation_stderr_tail": outcome.get("stderr_tail"),
                    "validation_final_flag_candidate": outcome.get("final_flag_candidate"),
                    "validation_diagnostic_unavailable": outcome.get("diagnostic_unavailable"),
                    "missing_solver_output": outcome.get("missing_solver_output"),
                    "validation_contract_errors": contract_errors,
                    "validation_failure_details": failure_details,
                    "pwn_failure_stage": outcome.get("pwn_failure_stage"),
                    "pwn_debug_result_path": outcome.get("pwn_debug_result_path"),
                    "pwn_debug_result_sha256": outcome.get("pwn_debug_result_sha256"),
                    "pwn_debug_actionable_summary": outcome.get("pwn_debug_actionable_summary"),
                    "pwn_debug_status": outcome.get("pwn_debug_status"),
                    "pwn_debug_error": outcome.get("pwn_debug_error"),
                }
            )
            state.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="validate",
                status="failed",
                message=validator_message(
                    status=status,
                    elapsed=elapsed,
                    error=error,
                    validation_failure_class=failed_result.get("validation_failure_class"),
                    validation_failure_signature=failed_result.get("validation_failure_signature"),
                ),
            )
            results.append(failed_result)
    return results


def _bind_validation_target(
    plan: ChallengeResumePlan | None,
    challenge_id: str,
    target: Path | None,
) -> ChallengeResumePlan:
    """Use the execution-bound output for evidence checks and validation."""
    return ChallengeResumePlan(
        challenge_id=challenge_id,
        directory=target,
        lookup_status="ok" if target is not None else "missing_challenge",
        skipped_stages=plan.skipped_stages if plan is not None else (),
        first_pending_stage=plan.first_pending_stage if plan is not None else "validate",
        stage_sources=plan.stage_sources if plan is not None else {},
    )


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
) -> str | dict[str, str] | None:
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
    if not category:
        # Execution-bound candidates live under
        # current/output/challenges/<category>/<challenge-dir>, outside the
        # canonical paths.challenges tree. Their immediate parent is still the
        # authoritative category directory.
        category = plan.directory.parent.name

    metadata_contract = _metadata_contract_gate(plan.directory)
    if metadata_contract is not None:
        return metadata_contract

    # 按阶段顺序检查证据
    if not design_evidence(plan.directory, challenge_id):
        return "design evidence incomplete"
    implement_reason = implement_evidence_reason(plan.directory, category)
    if implement_reason is not None:
        return f"implement evidence incomplete: {implement_reason}"
    light_contract = _light_contract_gate(plan.directory, category)
    if light_contract is not None:
        return light_contract
    build_ok, build_reason = build_evidence(plan.directory, category, image_exists)
    if not build_ok:
        return f"build evidence incomplete: {build_reason}"
    document_reason = document_evidence_reason(plan.directory)
    if document_reason is not None:
        return f"document evidence incomplete: {document_reason}"

    return None  # 全部检查通过


def _normalize_gate_failure(
    gate_failure: str | dict[str, str],
) -> tuple[str, str, list[dict[str, str]]]:
    if isinstance(gate_failure, dict):
        status = gate_failure.get("status") or "contract_failed"
        message = gate_failure.get("message") or gate_failure.get("code") or status
        return status, message, [gate_failure]
    details = classify_validation_failure(
        status="contract_failed",
        contract_errors=[gate_failure],
    )
    return "contract_failed", gate_failure, details


def pre_build_contract_gate(challenge_dir: Path, category: str) -> dict[str, str] | None:
    """Return a lightweight contract failure before expensive Docker build."""
    return _light_contract_gate(challenge_dir, category)


def _light_contract_gate(challenge_dir: Path, category: str) -> dict[str, str] | None:
    """Fail fast before host Docker build or validate.sh execution."""
    metadata = read_json(challenge_dir / "metadata.json", None)
    if not isinstance(metadata, dict):
        return _metadata_contract_gate(challenge_dir)
    required_files = (
        ("validate.sh", "missing_validation"),
        ("writenup/exp.py", "missing_solver"),
        ("writenup/wp.md", "missing_document"),
    )
    for relative, code in required_files:
        if not (challenge_dir / relative).is_file():
            return validation_failure_detail(
                phase="contract",
                code=code,
                status="contract_failed",
                message=f"{relative} missing",
                path=relative,
                hint=f"Create {relative} at the challenge root before validation.",
            )
    if not _is_executable_file(challenge_dir / "validate.sh"):
        return validation_failure_detail(
            phase="contract",
            code="validation_not_executable",
            status="contract_failed",
            message="validate.sh is not executable",
            path="validate.sh",
            hint="Make validate.sh executable with mode 0755 before validation.",
        )
    if category == "pwn":
        artifact = metadata.get("artifact")
        if not isinstance(artifact, str) or not artifact.startswith("attachments/") or ".." in Path(artifact).parts:
            return validation_failure_detail(
                phase="contract",
                code="artifact_path_mismatch",
                status="contract_failed",
                message="pwn metadata.artifact must point to the final player ELF under attachments/",
                path="metadata.json",
                hint="Set metadata.artifact to the final player ELF under attachments/.",
            )
        artifact_path = challenge_dir / artifact
        if not artifact_path.is_file():
            return validation_failure_detail(
                phase="contract",
                code="missing_artifact",
                status="contract_failed",
                message=f"pwn final attachment {artifact} missing",
                path=artifact,
                hint="Copy the final host-built player ELF to metadata.artifact before validation.",
            )
        stale_details = pwn_solver_evidence_failures(challenge_dir, metadata)
        if stale_details:
            return stale_details[0]
    elif category == "re":
        artifact = metadata.get("artifact")
        if not isinstance(artifact, str) or not artifact.startswith("attachments/"):
            return validation_failure_detail(
                phase="contract",
                code="artifact_missing",
                status="contract_failed",
                message="metadata.artifact missing or not under attachments/",
                path="metadata.json",
                hint="Point metadata.artifact at the primary executable under attachments/.",
            )
        if not (challenge_dir / artifact).is_file():
            return validation_failure_detail(
                phase="contract",
                code="missing_artifact",
                status="contract_failed",
                message=f"artifact file {artifact!r} missing under attachments/",
                path="attachments/",
                hint="Ensure metadata.artifact points to an existing file under attachments/.",
            )
    return None


def _metadata_contract_gate(challenge_dir: Path) -> dict[str, str] | None:
    metadata = read_json(challenge_dir / "metadata.json", None)
    if isinstance(metadata, dict):
        return None
    return validation_failure_detail(
        phase="contract",
        code="missing_metadata",
        status="contract_failed",
        message="metadata.json missing",
        path="metadata.json",
        hint="Create metadata.json at the challenge root before validation.",
    )


def _is_executable_file(path: Path) -> bool:
    try:
        return path.is_file() and bool(path.stat().st_mode & 0o111)
    except OSError:
        return False


def record_per_challenge_complete(
    state: ProgressRecorder,
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
