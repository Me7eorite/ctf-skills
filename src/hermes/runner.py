"""Hermes Runner —— 分片执行的完整 7 阶段管线。

负责单个分片从认领到完成的完整生命周期:
  queued → design → implement → build → validate → document → complete

核心职责:
  1. 分片认领与断点恢复规划
  2. 进度事件写入（best-effort 模式，写入失败不中断执行）
  3. Hermes AI 子进程调用与超时恢复
  4. 校验编排与质量门检查
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from core.build_timeout import shard_timeout_policy
from core.docker import image_exists as default_image_exists
from core.jsonio import read_json
from core.paths import ProjectPaths, category_of
from core.queue import ShardQueue
from core.state import InMemoryProgressStore, ProgressEventInput, ProgressStore
from domain.resume import (
    ChallengeResumePlan,
    ShardResumePlan,
    build_evidence,
    carry_forward_message,
    compute_resume_plan,
    design_evidence,
    document_evidence,
    implement_evidence,
)
from domain.validation import ChallengeValidator
from hermes import process as hermes_process
from hermes.process import (
    DEFAULT_HERMES_COMMAND,
    DEFAULT_HERMES_TIMEOUT,
    HERMES_TIMEOUT_RETURNCODE,
)
from hermes.progress import ensure_report, update_report
from hermes.prompt import render_prompt, render_validation_repair_prompt
from hermes.report import merge_validation_into_report
from hermes.validation import (
    record_per_challenge_complete,
    run_validation,
    validate_gate,
)
from hermes.workspace import (
    WorkspacePreflightError,
    WorkspacePromotionError,
    import_workspace_report,
    materialize_resume_outputs,
    preflight_workspace,
    prepare_workspace,
    promote_claimed_outputs,
    record_effective_timeout,
)
from hermes.workspace_progress import WorkspaceProgressTailer, materialize_progress_shim

__all__ = [
    "DEFAULT_HERMES_COMMAND",
    "DEFAULT_HERMES_TIMEOUT",
    "HERMES_TIMEOUT_RETURNCODE",
    "HermesRunner",
    "merge_validation_into_report",
]


def _carry_forward_pending_message(stage: str) -> str:
    """生成断点恢复中的待处理阶段消息。"""
    return f"Waiting for {stage} stage execution"


_LOGGER = logging.getLogger(__name__)
DEFAULT_VALIDATION_REPAIR_ATTEMPTS = 2


class _BestEffortProgressStore:
    """进度存储的代理包装器，支持"尽力而为"模式。

    当底层进度存储抛出指定类型的异常时，记录警告日志但不中断执行。
    这对于以下场景至关重要:
      - 数据库暂时不可达时 Worker 仍能继续工作
      - 非关键进度写入失败不应阻塞分片执行

    参数:
        store: 底层进度存储
        suppress_exceptions: 不中断执行的异常类型（如 PersistenceConnectionError）
    """

    def __init__(
        self,
        store: ProgressStore,
        suppress_exceptions: tuple[type[Exception], ...],
    ) -> None:
        self._store = store
        self._suppress_exceptions = suppress_exceptions

    def record(self, **kwargs: Any) -> dict:
        """记录单条进度事件（尽力而为）。

        如果写入失败且异常在 suppress_exceptions 中，返回占位结果而不抛异常。
        """
        try:
            return self._store.record(**kwargs)
        except Exception as exc:
            if not self._suppress_exceptions or not isinstance(
                exc, self._suppress_exceptions
            ):
                raise
            _LOGGER.warning("progress write skipped: %s", exc)
            return {
                "event_id": None,
                "shard": kwargs.get("shard", ""),
                "challenge_id": kwargs.get("challenge_id", ""),
                "worker": kwargs.get("worker", ""),
                "stage": kwargs.get("stage", ""),
                "status": kwargs.get("status", ""),
                "percent": 0,
                "message": kwargs.get("message", ""),
                "updated_at": "",
            }

    def record_batch(self, events: list[ProgressEventInput]) -> list[dict]:
        """批量记录进度事件（尽力而为）。"""
        try:
            return self._store.record_batch(events)
        except Exception as exc:
            if not self._suppress_exceptions or not isinstance(
                exc, self._suppress_exceptions
            ):
                raise
            _LOGGER.warning("progress batch write skipped: %s", exc)
            return []


class HermesRunner:
    """分片执行的完整管线控制器。

    持有分片队列、进度存储、校验器和 Docker 检查函数的引用，
    并提供 run() / process_one() 等公有入口。

    参数:
        paths: 项目路径管理
        progress: 可选的外部进度存储（默认内存存储，测试用）
        progress_write_exceptions: 进度写入失败时忽略的异常类型
        image_exists: Docker 镜像检查函数（可注入，方便测试）
    """

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        progress: ProgressStore | None = None,
        progress_write_exceptions: tuple[type[Exception], ...] = (),
        image_exists: Callable[[str], bool] | None = None,
        profile_exists: Callable[[str], bool] | None = None,
        validation_repair_attempts: int | None = None,
    ):
        self.paths = paths
        # 分片队列（管理 pending/running/done/failed 目录）
        self.queue = ShardQueue(paths)
        # 进度存储（默认内存，生产环境应注入 PostgresProgressStore）
        self.state = progress or InMemoryProgressStore()
        # 包装为 BestEffort 模式：数据库写入失败不阻断执行
        self._progress = _BestEffortProgressStore(
            self.state,
            progress_write_exceptions,
        )
        self.validator = ChallengeValidator(paths)
        self._image_exists = image_exists or default_image_exists
        self._profile_exists = profile_exists or hermes_process.profile_exists
        configured_repairs = validation_repair_attempts
        if configured_repairs is None:
            raw_repairs = os.environ.get(
                "HERMES_VALIDATION_REPAIR_ATTEMPTS",
                str(DEFAULT_VALIDATION_REPAIR_ATTEMPTS),
            )
            try:
                configured_repairs = int(raw_repairs)
            except ValueError:
                configured_repairs = DEFAULT_VALIDATION_REPAIR_ATTEMPTS
        if configured_repairs < 0:
            raise ValueError("validation_repair_attempts must be non-negative")
        self.validation_repair_attempts = configured_repairs

    # ----------------------------------------------------------------
    # 公有入口方法
    # ----------------------------------------------------------------

    def render_prompt(
        self,
        shard: Path,
        report: Path,
        worker: str,
        *,
        report_runtime_path: str | None = None,
        workspace_relative: bool = False,
        original_shard_name: str | None = None,
        resume_plan: ShardResumePlan | None = None,
    ) -> str:
        """渲染送给 Hermes 的完整 prompt（含断点恢复计划）。"""
        return render_prompt(
            self.paths,
            shard,
            report,
            worker,
            report_runtime_path=report_runtime_path,
            workspace_relative=workspace_relative,
            original_shard_name=original_shard_name,
            resume_plan=resume_plan,
        )

    def run(
        self,
        worker: str,
        *,
        loop: bool = False,
        dry_run: bool = False,
        max_shards: int = 0,
        timeout: int | None = None,
        timeout_source: str | None = None,
        category: str | None = None,
        build_attempt_id: UUID | str | None = None,
        require_build_attempt: bool = False,
    ) -> dict:
        """批量处理待处理分片的主循环。

        参数:
            worker: Worker 标识
            loop: True 时持续处理直到队列为空
            dry_run: True 时只渲染 prompt 不执行 Hermes
            max_shards: 最多处理的分片数（0 表示无限制）
            timeout: Hermes 执行超时秒数

        返回:
            {"processed": N, "failed": N, "outcomes": [...]}
        """
        self.paths.initialize()  # 确保工作目录就位
        processed = 0
        failed = 0
        outcomes: list[dict] = []

        while True:
            outcome = self.process_one(
                worker,
                dry_run=dry_run,
                timeout=timeout,
                timeout_source=timeout_source,
                category=category,
                build_attempt_id=build_attempt_id,
                require_build_attempt=require_build_attempt,
            )
            if outcome["status"] == "empty":
                break  # 队列已空
            outcomes.append(outcome)
            processed += 1
            if outcome["status"] == "failed":
                failed += 1
            # 单次执行或达到上限 → 退出循环
            if not loop or (max_shards and processed >= max_shards):
                break

        return {"processed": processed, "failed": failed, "outcomes": outcomes}

    def process_one(
        self,
        worker: str,
        *,
        dry_run: bool,
        timeout: int | None = None,
        timeout_source: str | None = None,
        category: str | None = None,
        build_attempt_id: UUID | str | None = None,
        require_build_attempt: bool = False,
    ) -> dict:
        """处理一个待处理分片。

        流程:
          1. 从 pending 队列认领一个分片
          2. 获取分片中的题目列表
          3. 根据 dry_run 标志分支到模拟执行或真实执行
        """
        # 认领分片（原子操作）
        shard = self.queue.claim(
            worker,
            category=category,
            build_attempt_id=build_attempt_id,
            require_build_attempt=require_build_attempt,
        )
        if shard is None:
            return {"status": "empty"}

        # 获取分片元数据
        original_shard_name = self.queue.original_name(shard)
        resume_source_shard_name = _resume_source_shard_name(shard, original_shard_name)
        report = self.paths.reports / f"{shard.stem}.report.json"
        log = self.paths.logs / f"{shard.stem}.log"
        challenge_ids = self.queue.challenge_ids(shard)

        if dry_run:
            # 模拟执行：只计算计划和渲染 prompt，不执行 Hermes
            return self._process_dry_run(
                shard,
                original_shard_name,
                resume_source_shard_name,
                worker,
                report,
                log,
                challenge_ids,
            )

        # 真实执行：完整 7 阶段管线
        return self._process_real(
            shard,
            original_shard_name,
            resume_source_shard_name,
            worker,
            report,
            log,
            challenge_ids,
            timeout=timeout,
            timeout_source=timeout_source,
        )

    # ----------------------------------------------------------------
    # 模拟执行路径：claim → plan → render → requeue。不写进度事件。
    # ----------------------------------------------------------------

    def _process_dry_run(
        self,
        shard: Path,
        original_shard_name: str,
        resume_source_shard_name: str,
        worker: str,
        report: Path,
        log: Path,
        challenge_ids: list[str],
    ) -> dict:
        """模拟执行：计算恢复计划、渲染 prompt、写入日志、退还分片。

        不执行 Hermes、不写进度事件。用于预览和调试。
        """
        try:
            # 计算恢复计划
            plan = compute_resume_plan(
                state=self.state,
                paths=self.paths,
                shard=resume_source_shard_name,
                challenge_ids=challenge_ids,
                image_exists=self._image_exists,
            )
            # 渲染 prompt
            prompt = self.render_prompt(
                shard,
                report,
                worker,
                workspace_relative=True,
                original_shard_name=original_shard_name,
                resume_plan=plan,
            )
            # 写入日志文件（prompt 内容）
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(prompt + "\n", encoding="utf-8")
            return {"status": "dry_run", "shard": original_shard_name}
        finally:
            # 无论成功与否，都要把分片退还给 pending 队列
            try:
                self.queue.requeue(shard.name, "running")
            except FileNotFoundError:
                pass  # 已被其他操作移动，忽略

    # ----------------------------------------------------------------
    # 真实执行路径: 完整 7 阶段管线
    # ----------------------------------------------------------------

    def _process_real(
        self,
        shard: Path,
        original_shard_name: str,
        resume_source_shard_name: str,
        worker: str,
        report: Path,
        log: Path,
        challenge_ids: list[str],
        *,
        timeout: int | None,
        timeout_source: str | None,
    ) -> dict:
        """执行完整的分片处理管线。

        步骤:
          1. 计算断点恢复计划（此时还不要写本轮的 queued 事件！）
          2. 重置看板快照（事件保持只追加）
          3. 写入本轮的 queued running 认领事件
          4. 写入断点恢复携带的前向阶段事件
          5. 如果所有题目都已完成 → 全跳捷径
          6. 写入每个题目第一个待处理阶段的 pending 事件
          7. 渲染 prompt 并调用 Hermes
          8. Hermes 返回后执行 validate 校验
          9. 合并校验结果到报告
          10. 根据校验结果标记 shard 为 done 或 failed
        """
        # 步骤 1: 从历史窗口中计算恢复计划
        # 【重要】必须在写入本轮 queued 事件之前计算！
        plan = compute_resume_plan(
            state=self.state,
            paths=self.paths,
            shard=resume_source_shard_name,
            challenge_ids=challenge_ids,
            image_exists=self._image_exists,
        )

        # 步骤 2: 重置快照（事件保持追加）
        self.state.reset_snapshots(original_shard_name)

        # 步骤 3: 写入本轮认领事件（新的时间窗口起点）
        self._progress.record(
            shard=original_shard_name,
            worker=worker,
            stage="queued",
            status="running",
            message=f"Worker claimed {len(challenge_ids)} challenge(s)",
        )

        # 步骤 4: 写入断点恢复携带的阶段事件
        plan_by_id: dict[str, ChallengeResumePlan] = {
            cp.challenge_id: cp for cp in plan.challenges
        }
        carry_forward_events: list[ProgressEventInput] = []
        for cp in plan.challenges:
            for stage in cp.skipped_stages:
                source_id = cp.stage_sources.get(stage, 0)
                carry_forward_events.append(
                    ProgressEventInput(
                        shard=original_shard_name,
                        challenge_id=cp.challenge_id,
                        worker=worker,
                        stage=stage,
                        status="passed",
                        message=carry_forward_message(stage, source_id),
                    )
                )
        self._progress.record_batch(carry_forward_events)

        # 步骤 5: 全跳捷径 —— 所有题目都已完成，不需要调 Hermes
        if plan.all_challenges_fully_skipped:
            return self._shortcircuit_all_skipped(
                shard, original_shard_name, worker, report, challenge_ids
            )

        # 步骤 6: 写入每个题目的第一个待处理阶段 pending 事件
        for cp in plan.challenges:
            if cp.first_pending_stage is not None:
                self._progress.record(
                    shard=original_shard_name,
                    challenge_id=cp.challenge_id,
                    worker=worker,
                    stage=cp.first_pending_stage,
                    status="pending",
                    message=_carry_forward_pending_message(cp.first_pending_stage),
                )

        # 步骤 7: 渲染 prompt 并调用 Hermes AI
        try:
            workspace = prepare_workspace(
                self.paths,
                shard=shard,
                original_shard_name=original_shard_name,
                worker=worker,
            )
        except (OSError, ValueError) as exc:
            message = f"Workspace preparation failed: {exc}"
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
            )
            return {
                "status": "failed",
                "failure_type": "infrastructure",
                "shard": original_shard_name,
                "returncode": 1,
                "error": message,
            }
        manifest = read_json(workspace.manifest, {})
        category = manifest.get("category") if isinstance(manifest, dict) else None
        profile_name = f"cf-{category}"
        # 中文注释：shim 是 preflight 的检查项之一，必须在 preflight 之前 materialize；
        # 否则 preflight 通过、prompt 渲染后 Hermes 才发现 ./bin/progress 不存在，违反 fail-closed 契约。
        try:
            materialize_progress_shim(workspace)
        except OSError as exc:
            message = f"Workspace shim materialization failed: {exc}"
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
            )
            return {
                "status": "failed",
                "failure_type": "infrastructure",
                "shard": original_shard_name,
                "returncode": 1,
                "error": message,
            }
        try:
            payload = preflight_workspace(
                workspace,
                profile_name=profile_name,
                profile_exists=self._profile_exists,
            )
        except WorkspacePreflightError as exc:
            message = f"Workspace preflight failed: {exc}"
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
            )
            return {
                "status": "failed",
                "failure_type": "infrastructure",
                "shard": original_shard_name,
                "returncode": 1,
                "error": message,
            }
        try:
            materialize_resume_outputs(self.paths, workspace, payload)
        except (OSError, WorkspacePromotionError, ValueError) as exc:
            message = f"Workspace materialization failed: {exc}"
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
            )
            return {
                "status": "failed",
                "failure_type": "infrastructure",
                "shard": original_shard_name,
                "returncode": 1,
                "error": message,
            }
        if timeout is not None:
            if timeout <= 0:
                raise ValueError("timeout must be positive")
            effective_timeout = timeout
            effective_timeout_source = timeout_source or "cli"
        else:
            effective_timeout = shard_timeout_policy(payload)
            effective_timeout_source = "shard_policy"
        record_effective_timeout(
            workspace,
            seconds=effective_timeout,
            source=effective_timeout_source,
        )
        log = workspace.hermes_log
        prompt = self.render_prompt(
            workspace.input / "shard.json",
            report,
            worker,
            report_runtime_path="./logs/report.json",
            workspace_relative=True,
            original_shard_name=original_shard_name,
            resume_plan=plan,
        )
        tailer = WorkspaceProgressTailer(workspace, self._progress.record)
        tailer.start()
        try:
            returncode = self._invoke(
                prompt,
                log,
                dry_run=False,
                timeout=effective_timeout,
                workspace=workspace,
                profile_name=profile_name,
            )
        except KeyboardInterrupt:
            # 被用户中断 → 记录失败并重新抛出
            import_workspace_report(workspace, report)
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                "Runner interrupted",
                130,  # 标准 POSIX 返回码：被信号中断
            )
            raise
        finally:
            tailer.stop_and_flush()

        import_workspace_report(workspace, report)

        if returncode == 0 or returncode == HERMES_TIMEOUT_RETURNCODE:
            try:
                promote_claimed_outputs(self.paths, workspace, payload)
            except (OSError, WorkspacePromotionError, ValueError) as exc:
                message = f"Workspace output promotion failed: {exc}"
                self._mark_shard_failed(
                    shard,
                    original_shard_name,
                    worker,
                    challenge_ids,
                    report,
                    message,
                    returncode,
                )
                return {
                    "status": "failed",
                    "failure_type": "infrastructure",
                    "shard": original_shard_name,
                    "returncode": returncode,
                    "error": message,
                }

        # Hermes 返回非零 → 检查是否为超时 + 超时恢复通过
        if returncode != 0:
            timed_out = returncode == HERMES_TIMEOUT_RETURNCODE
            if not timed_out or not self._timeout_recovery_complete(
                original_shard_name, challenge_ids
            ):
                # 非超时错误，或超时恢复失败 → 直接标记失败
                self._mark_shard_failed(
                    shard,
                    original_shard_name,
                    worker,
                    challenge_ids,
                    report,
                    f"Hermes exited with {returncode}",
                    returncode,
                )
                return {
                    "status": "failed",
                    "failure_type": "infrastructure",
                    "shard": original_shard_name,
                    "returncode": returncode,
                }
            # 超时但恢复通过 → 继续执行后续步骤（Hermes 已生成了足够的内容）

        # 步骤 8: 确保报告文件存在
        ensure_report(report, shard, worker, "completed_by_runner", returncode)

        # 步骤 9: 执行强制校验
        per_results = self._run_validation(
            original_shard_name, worker, challenge_ids, plan_by_id
        )
        merge_validation_into_report(report, per_results)

        # Host validation is authoritative, but a generated exploit frequently needs
        # runtime feedback (container logs, traceback, leak parsing, libc offsets) that
        # was unavailable during the initial authoring pass. Feed deterministic failure
        # diagnostics back to Hermes and revalidate a bounded number of times.
        for repair_attempt in range(1, self.validation_repair_attempts + 1):
            if not any(
                result.get("solve_status") == "failed" for result in per_results
            ):
                break
            self._progress.record(
                shard=original_shard_name,
                worker=worker,
                stage="validate",
                status="running",
                message=(
                    "validation repair: sending host diagnostics to Hermes "
                    f"({repair_attempt}/{self.validation_repair_attempts})"
                ),
            )
            repair_prompt = render_validation_repair_prompt(
                attempt=repair_attempt,
                max_attempts=self.validation_repair_attempts,
                validation_results=per_results,
            )
            repair_log = workspace.logs / f"hermes-validation-repair-{repair_attempt}.log"
            pre_signature = _output_signature(workspace.output)
            repair_tailer = WorkspaceProgressTailer(workspace, self._progress.record)
            repair_tailer.start()
            try:
                repair_returncode = self._invoke(
                    repair_prompt,
                    repair_log,
                    dry_run=False,
                    timeout=effective_timeout,
                    workspace=workspace,
                    profile_name=profile_name,
                )
            finally:
                repair_tailer.stop_and_flush()
            import_workspace_report(workspace, report)
            if repair_returncode != 0:
                _LOGGER.warning(
                    "validation repair attempt %s exited with %s",
                    repair_attempt,
                    repair_returncode,
                )
                break
            post_signature = _output_signature(workspace.output)
            if pre_signature == post_signature:
                _LOGGER.warning(
                    "validation repair attempt %s made no changes under output/; "
                    "aborting further repair attempts",
                    repair_attempt,
                )
                self._progress.record(
                    shard=original_shard_name,
                    worker=worker,
                    stage="validate",
                    status="running",
                    message=(
                        f"validation repair attempt {repair_attempt}: "
                        "no changes detected, aborting further attempts"
                    ),
                )
                break
            try:
                promote_claimed_outputs(self.paths, workspace, payload)
            except (OSError, WorkspacePromotionError, ValueError) as exc:
                _LOGGER.warning(
                    "validation repair attempt %s promotion failed: %s",
                    repair_attempt,
                    exc,
                )
                break
            per_results = self._run_validation(
                original_shard_name, worker, challenge_ids, plan_by_id
            )
            merge_validation_into_report(report, per_results)

        # 步骤 10: 根据校验结果判定最终状态
        any_failed = any(
            result.get("solve_status") == "failed" for result in per_results
        )
        if any_failed:
            # 有题目校验失败 → 标记分片为 failed
            self._record_per_challenge_complete(
                original_shard_name, worker, per_results
            )
            self._progress.record(
                shard=original_shard_name,
                worker=worker,
                stage="complete",
                status="failed",
                message="One or more challenges failed validation",
            )
            update_report(report, "failed", "challenge validation failed")
            self.queue.complete(shard, "failed")
            return {"status": "failed", "shard": original_shard_name}

        # 所有题目校验通过 → 完成!
        self._record_per_challenge_complete(
            original_shard_name, worker, per_results
        )
        self._progress.record(
            shard=original_shard_name,
            worker=worker,
            stage="complete",
            status="passed",
            message="Generation completed",
        )
        update_report(report, "passed")
        self.queue.complete(shard, "done")
        return {"status": "done", "shard": original_shard_name}

    # ------------------------------------------------------------------
    # Short-circuit and recovery helpers
    # ------------------------------------------------------------------

    def _shortcircuit_all_skipped(
        self,
        shard: Path,
        original_shard_name: str,
        worker: str,
        report: Path,
        challenge_ids: list[str],
    ) -> dict:
        per_results = [
            {
                "challenge_id": challenge_id,
                "solve_status": "passed",
                "validation_status": "skipped_resume",
            }
            for challenge_id in challenge_ids
        ]
        merge_validation_into_report(
            report, per_results, shard=shard, worker=worker, runner_status="passed"
        )
        for challenge_id in challenge_ids:
            self._progress.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="complete",
                status="passed",
                message="carry-forward: all stages already complete",
            )
        self._progress.record(
            shard=original_shard_name,
            worker=worker,
            stage="complete",
            status="passed",
            message="resumed: all challenges fully complete",
        )
        update_report(report, "passed")
        self.queue.complete(shard, "done")
        return {"status": "done", "shard": original_shard_name, "short_circuit": True}

    def _timeout_recovery_complete(
        self, original_shard_name: str, challenge_ids: list[str]
    ) -> bool:
        """Return True when every challenge passes design/implement/build/document evidence."""
        recovery_plan = compute_resume_plan(
            state=self.state,
            paths=self.paths,
            shard=original_shard_name,
            challenge_ids=challenge_ids,
            image_exists=self._image_exists,
        )
        for cp in recovery_plan.challenges:
            if cp.directory is None:
                return False
            category = category_of(cp.directory, self.paths)
            if not design_evidence(cp.directory, cp.challenge_id):
                return False
            if not implement_evidence(cp.directory, category):
                return False
            build_ok, _ = build_evidence(
                cp.directory, category, self._image_exists
            )
            if not build_ok:
                return False
            if not document_evidence(cp.directory):
                return False
        return True

    # ------------------------------------------------------------------
    # Validation orchestration
    # ------------------------------------------------------------------

    def _run_validation(
        self,
        original_shard_name: str,
        worker: str,
        challenge_ids: list[str],
        plan_by_id: dict[str, ChallengeResumePlan],
    ) -> list[dict[str, Any]]:
        return run_validation(
            state=self._progress,
            validator=self.validator,
            paths=self.paths,
            image_exists=self._image_exists,
            original_shard_name=original_shard_name,
            worker=worker,
            challenge_ids=challenge_ids,
            plan_by_id=plan_by_id,
        )

    def _validate_gate(
        self, challenge_id: str, plan: ChallengeResumePlan | None
    ) -> str | None:
        return validate_gate(challenge_id, plan, self.paths, self._image_exists)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _record_per_challenge_complete(
        self,
        original_shard_name: str,
        worker: str,
        per_results: list[dict[str, Any]],
    ) -> None:
        record_per_challenge_complete(
            self._progress,
            original_shard_name,
            worker,
            per_results,
        )

    def _mark_shard_failed(
        self,
        shard: Path,
        original_shard_name: str,
        worker: str,
        challenge_ids: list[str],
        report: Path,
        message: str,
        returncode: int,
    ) -> None:
        for challenge_id in challenge_ids:
            self._progress.record(
                shard=original_shard_name,
                challenge_id=challenge_id,
                worker=worker,
                stage="complete",
                status="failed",
                message=message,
            )
        self._progress.record(
            shard=original_shard_name,
            worker=worker,
            stage="complete",
            status="failed",
            message=message,
        )
        ensure_report(report, shard, worker, "failed", returncode)
        self.queue.complete(shard, "failed")

    # ------------------------------------------------------------------
    # Hermes subprocess invocation
    # ------------------------------------------------------------------

    def _invoke(
        self,
        prompt: str,
        log: Path,
        dry_run: bool,
        *,
        timeout: int | None = None,
        workspace=None,
        profile_name: str | None = None,
    ) -> int:
        if dry_run:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(prompt + "\n", encoding="utf-8")
            return 0

        arguments = (
            hermes_process.inject_profile_argument(profile_name)
            if profile_name is not None
            else self._hermes_arguments()
        )
        environment = os.environ.copy()
        if self.paths.hermes_home.exists() and not environment.get("HERMES_HOME"):
            environment["HERMES_HOME"] = str(self.paths.hermes_home)
        if self._apply_legacy_custom_provider(environment):
            self._remove_conflicting_custom_pool()
            query_index = (
                arguments.index("-q") if "-q" in arguments else len(arguments)
            )
            arguments[query_index:query_index] = ["--provider", "custom"]

        effective_timeout = timeout if timeout is not None else DEFAULT_HERMES_TIMEOUT
        return hermes_process.invoke(
            prompt,
            arguments=arguments,
            log_path=log,
            cwd=workspace.root if workspace is not None else self.paths.root,
            environment=environment,
            timeout=effective_timeout,
        )

    def _apply_legacy_custom_provider(self, environment: dict[str, str]) -> bool:
        return hermes_process.apply_legacy_custom_provider(
            self.paths.hermes_home, environment
        )

    def _remove_conflicting_custom_pool(self) -> bool:
        return hermes_process.remove_conflicting_custom_pool(self.paths.hermes_home)

    @staticmethod
    def _hermes_arguments() -> list[str]:
        return hermes_process.hermes_arguments()


def _output_signature(output_dir: Path) -> tuple[int, int, int]:
    """对 workspace.output 下所有文件采样的轻量指纹，用于检测 repair 是否真正改了产物。

    返回 ``(file_count, total_size, max_mtime_ns)``。任何一项变化即视为有改动；
    采样在文件粒度而非内容级，足够 detect 上层文件的写入/新建/删除而不付出哈希成本。
    """
    if not output_dir.exists():
        return (0, 0, 0)
    file_count = 0
    total_size = 0
    max_mtime = 0
    for path in output_dir.rglob("*"):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        file_count += 1
        total_size += stat.st_size
        if stat.st_mtime_ns > max_mtime:
            max_mtime = stat.st_mtime_ns
    return (file_count, total_size, max_mtime)


def _resume_source_shard_name(shard: Path, current_original_name: str) -> str:
    payload = read_json(shard, {})
    if not isinstance(payload, dict):
        return current_original_name
    value = payload.get("resume_from_shard_basename")
    if value is None:
        return current_original_name
    if not isinstance(value, str):
        raise ValueError("resume_from_shard_basename must be a shard basename")
    source = Path(value)
    if source.name != value or source.suffix != ".json" or not source.stem:
        raise ValueError("resume_from_shard_basename must be a safe .json basename")
    return value
