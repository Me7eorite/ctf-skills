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
import re
import shutil
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from core.build_timeout import shard_timeout_policy
from core.docker import image_exists as default_image_exists
from core.execution_config import execution_minting_enabled
from core.jsonio import read_json, write_json
from core.paths import ProjectPaths, category_of
from core.queue import ShardQueue
from core.state import InMemoryProgressStore, ProgressEventInput, ProgressStore
from domain.build_failure_taxonomy import BuildFailureCategory, classify_hermes_exit
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
from hermes.build_publisher import (
    PublicationContract,
    WorkspaceValidationSet,
    prepare_publication_contract,
    prepare_workspace_validation,
    publish_workspace_output,
    record_workspace_terminal,
)
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
    ExecutionWorkspace,
    WorkspacePreflightError,
    WorkspacePromotionError,
    import_workspace_report,
    materialize_resume_outputs,
    preflight_workspace,
    prepare_workspace,
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


def _validation_failure_message(results: list[dict[str, Any]]) -> str:
    failures: list[str] = []
    for result in results:
        if result.get("solve_status") != "failed":
            continue
        challenge_id = str(result.get("challenge_id", "unknown"))
        status = str(result.get("validation_status", "failed"))
        error = str(result.get("validation_error") or "").strip()
        detail = f"{challenge_id}: {status}"
        if error:
            detail += f" ({error[:300]})"
        failures.append(detail)
    return "; ".join(failures) or "challenge validation failed"


_ITERATION_RE = re.compile(r"\.iter-(\d+)\.")


def _iteration_from_shard_name(original_shard_name: str | None) -> int:
    """Parse the iteration from a per-iteration shard basename, default 1.

    Minting submits stage shards as ``{build_attempt_id}.iter-NNN.json`` so the
    runner can name the workspace archive; legacy basenames lack the suffix.
    """
    if not original_shard_name:
        return 1
    match = _ITERATION_RE.search(original_shard_name)
    return int(match.group(1)) if match else 1


_LOGGER = logging.getLogger(__name__)
DEFAULT_VALIDATION_REPAIR_ATTEMPTS = 2
_VALIDATION_REPAIR_TIMEOUT_ENV = "HERMES_VALIDATION_REPAIR_TIMEOUT"


def _resolve_validation_repair_timeout(effective_timeout: int) -> int:
    raw_timeout = os.environ.get(_VALIDATION_REPAIR_TIMEOUT_ENV)
    if raw_timeout is None or not raw_timeout.strip():
        return min(effective_timeout, 600)
    try:
        configured = int(raw_timeout)
    except ValueError:
        _LOGGER.warning(
            "invalid %s=%r; using capped default",
            _VALIDATION_REPAIR_TIMEOUT_ENV,
            raw_timeout,
        )
        return min(effective_timeout, 600)
    if configured <= 0:
        _LOGGER.warning(
            "invalid %s=%r; using capped default",
            _VALIDATION_REPAIR_TIMEOUT_ENV,
            raw_timeout,
        )
        return min(effective_timeout, 600)
    return min(configured, effective_timeout)


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
            if not self._suppress_exceptions or not isinstance(exc, self._suppress_exceptions):
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
            if not self._suppress_exceptions or not isinstance(exc, self._suppress_exceptions):
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
        resume_output_targets: Mapping[str, str] | None = None,
        repair_requested: bool = False,
        repair_context: Mapping[str, Any] | None = None,
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
            resume_output_targets=resume_output_targets,
            repair_requested=repair_requested,
            repair_context=repair_context,
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
            shard_payload = read_json(shard, {})
            repair_requested = isinstance(shard_payload, dict) and bool(shard_payload.get("repair_requested"))
            repair_context = shard_payload.get("repair_context") if isinstance(shard_payload, dict) else None
            plan = None if repair_requested else compute_resume_plan(
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
                repair_requested=repair_requested,
                repair_context=repair_context if isinstance(repair_context, Mapping) else None,
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
        process_started = time.monotonic()

        def elapsed() -> float:
            return max(0.0, time.monotonic() - process_started)

        def fail_outcome(
            *,
            hermes_phase: BuildFailureCategory,
            returncode: int,
            error: str | None = None,
            failure_type: str = "infrastructure",
            elapsed_seconds: float | None = None,
            workspace: ExecutionWorkspace | None = None,
            publisher_phase: str | None = None,
        ) -> dict[str, Any]:
            outcome: dict[str, Any] = {
                "status": "failed",
                "failure_type": failure_type,
                "hermes_phase": hermes_phase,
                "elapsed_seconds": elapsed() if elapsed_seconds is None else max(0.0, elapsed_seconds),
                "shard": original_shard_name,
                "returncode": returncode,
            }
            if error is not None:
                outcome["error"] = error
            if publisher_phase:
                outcome["publisher_phase"] = publisher_phase
            outcome.update(self._timeout_metadata(workspace))
            return outcome

        # 步骤 1: 从历史窗口中计算恢复计划
        # 【重要】必须在写入本轮 queued 事件之前计算！
        shard_payload = read_json(shard, {})
        if isinstance(shard_payload, Mapping) and shard_payload.get("execution_mode") == "clean":
            plan = ShardResumePlan(
                shard=resume_source_shard_name,
                previous_claim_event_id=None,
                challenges=tuple(
                    ChallengeResumePlan(
                        challenge_id=challenge_id,
                        directory=None,
                        lookup_status="missing_challenge",
                    )
                    for challenge_id in challenge_ids
                ),
            )
        else:
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
        plan_by_id: dict[str, ChallengeResumePlan] = {cp.challenge_id: cp for cp in plan.challenges}
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
            return self._shortcircuit_all_skipped(shard, original_shard_name, worker, report, challenge_ids)

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
                two_layer=execution_minting_enabled(),
                iteration_no=_iteration_from_shard_name(original_shard_name),
            )
        except (OSError, ValueError) as exc:
            message = f"Workspace preparation failed: {exc}"
            failure_elapsed = elapsed()
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
                hermes_phase="materialize",
                elapsed_seconds=failure_elapsed,
            )
            return fail_outcome(
                hermes_phase="materialize",
                returncode=1,
                error=message,
                elapsed_seconds=failure_elapsed,
            )
        manifest = read_json(workspace.manifest, {})
        category = manifest.get("category") if isinstance(manifest, dict) else None
        profile_name = f"cf-{category}"
        # 中文注释：shim 是 preflight 的检查项之一，必须在 preflight 之前 materialize；
        # 否则 preflight 通过、prompt 渲染后 Hermes 才发现 ./bin/progress 不存在，违反 fail-closed 契约。
        try:
            materialize_progress_shim(workspace)
        except OSError as exc:
            message = f"Workspace shim materialization failed: {exc}"
            failure_elapsed = elapsed()
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
                hermes_phase="materialize",
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
            )
            return fail_outcome(
                hermes_phase="materialize",
                returncode=1,
                error=message,
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
            )
        try:
            payload = preflight_workspace(
                workspace,
                profile_name=profile_name,
                profile_exists=self._profile_exists,
                terminal_backend=hermes_process.effective_terminal_backend(
                    self.paths.hermes_home,
                    profile_name=profile_name,
                ),
            )
        except WorkspacePreflightError as exc:
            message = f"Workspace preflight failed: {exc}"
            failure_elapsed = elapsed()
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
                hermes_phase="preflight_workspace",
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
            )
            return fail_outcome(
                hermes_phase="preflight_workspace",
                returncode=1,
                error=message,
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
            )
        try:
            execution_mode_raw = payload.get("execution_mode")
            resume_source = payload.get("resume_from_shard_basename")
            if execution_mode_raw is None:
                # Compatibility: legacy payloads without execution_mode keep
                # the materialize-resume-outputs behavior. The spec maps such
                # payloads to "clean" semantically, but the publisher's
                # contract-level decision is what enforces clean's "no prior
                # artifact" guarantee for explicit clean-rebuild submissions.
                resolved_mode = "resume" if resume_source else "implicit"
            elif execution_mode_raw in {"resume", "clean"}:
                resolved_mode = execution_mode_raw
            else:
                raise ValueError(f"unsupported execution_mode: {execution_mode_raw!r}")
            if resolved_mode == "resume" and not isinstance(resume_source, str):
                raise ValueError("explicit resume requires resume_from_shard_basename")
            if resolved_mode == "clean" and resume_source is not None:
                raise ValueError("explicit clean forbids resume_from_shard_basename")
            if resolved_mode == "clean":
                resume_targets: dict[str, str] = {}
            else:
                resume_targets = materialize_resume_outputs(self.paths, workspace, payload)
            if resume_targets:
                manifest = read_json(workspace.manifest, {}) or {}
                if not isinstance(manifest, dict):
                    manifest = {}
                manifest["resume_output_targets"] = resume_targets
                write_json(workspace.manifest, manifest)
        except (OSError, WorkspacePromotionError, ValueError) as exc:
            message = f"Workspace materialization failed: {exc}"
            failure_elapsed = elapsed()
            publisher_phase = getattr(exc, "phase", None)
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
                hermes_phase="materialize",
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
                publisher_phase=publisher_phase,
            )
            return fail_outcome(
                hermes_phase="materialize",
                returncode=1,
                error=message,
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
                publisher_phase=publisher_phase,
            )
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
        try:
            publication_contract = prepare_publication_contract(
                self.paths,
                workspace,
                payload,
            )
        except (OSError, WorkspacePromotionError, ValueError) as exc:
            message = f"Publication contract preparation failed: {exc}"
            failure_elapsed = elapsed()
            publisher_phase = getattr(exc, "phase", None)
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
                hermes_phase="contract_prepare",
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
                publisher_phase=publisher_phase,
            )
            return fail_outcome(
                hermes_phase="contract_prepare",
                returncode=1,
                error=message,
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
                publisher_phase=publisher_phase,
            )
        log = workspace.hermes_log
        repair_requested = bool(payload.get("repair_requested"))
        repair_context = payload.get("repair_context") if isinstance(payload.get("repair_context"), Mapping) else None
        prompt = self.render_prompt(
            workspace.input / "shard.json",
            report,
            worker,
            report_runtime_path="./logs/report.json",
            workspace_relative=True,
            original_shard_name=original_shard_name,
            resume_plan=None if repair_requested else plan,
            resume_output_targets=resume_targets,
            repair_requested=repair_requested,
            repair_context=repair_context,
        )
        root_output_snapshot = _project_root_output_snapshot(self.paths.root)
        tailer = WorkspaceProgressTailer(workspace, self._progress.record)
        tailer.start()
        invoke_started = time.monotonic()
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
            failure_elapsed = max(0.0, time.monotonic() - invoke_started)
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                "Runner interrupted",
                -2,
                hermes_phase="hermes_cancelled",
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
            )
            raise
        finally:
            tailer.stop_and_flush()
        invoke_elapsed = max(0.0, time.monotonic() - invoke_started)

        import_workspace_report(workspace, report)
        root_output_leaks = _project_root_output_leaks(self.paths.root, root_output_snapshot)
        if root_output_leaks:
            leak_list = ", ".join(root_output_leaks[:5])
            if len(root_output_leaks) > 5:
                leak_list += f", ... (+{len(root_output_leaks) - 5} more)"
            message = (
                "Hermes wrote output outside the execution workspace under "
                f"project root: {leak_list}"
            )
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
                hermes_phase="workspace_output_leak",
                elapsed_seconds=invoke_elapsed,
                workspace=workspace,
            )
            return fail_outcome(
                hermes_phase="workspace_output_leak",
                returncode=1,
                error=message,
                elapsed_seconds=invoke_elapsed,
                workspace=workspace,
            )

        # Hermes 返回非零：普通进程错误立即失败；超时产物交给同一套
        # workspace-bound host validation 判断是否已经完整，禁止为验证而提前发布。
        timed_out = returncode == HERMES_TIMEOUT_RETURNCODE
        if returncode != 0:
            if not timed_out:
                hermes_phase = classify_hermes_exit(
                    returncode,
                    self._read_tail(log, 4096),
                    invoke_elapsed,
                    self._read_error_marker(log),
                )
                self._mark_shard_failed(
                    shard,
                    original_shard_name,
                    worker,
                    challenge_ids,
                    report,
                    f"Hermes exited with {returncode}",
                    returncode,
                    hermes_phase=hermes_phase,
                    elapsed_seconds=invoke_elapsed,
                    workspace=workspace,
                )
                return fail_outcome(
                    hermes_phase=hermes_phase,
                    returncode=returncode,
                    elapsed_seconds=invoke_elapsed,
                    workspace=workspace,
                )
            # 超时继续进入 workspace validation；不完整产物会形成确定性诊断。

        # 步骤 8: 确保报告文件存在
        ensure_report(report, shard, worker, "completed_by_runner", returncode)

        # 步骤 9: 执行强制校验
        per_results, validated_set = self._run_workspace_validation(
            original_shard_name,
            worker,
            challenge_ids,
            plan_by_id,
            workspace,
            publication_contract,
        )
        self._record_validation_round(workspace, 0, per_results, validated_set)
        merge_validation_into_report(report, per_results)

        # Host validation is authoritative, but a generated exploit frequently needs
        # runtime feedback (container logs, traceback, leak parsing, libc offsets) that
        # was unavailable during the initial authoring pass. Feed deterministic failure
        # diagnostics back to Hermes and revalidate a bounded number of times.
        # Union of every contract violation seen across repair rounds, replayed
        # into each prompt as a non-regression list so the agent does not satisfy
        # the latest diagnostic by reintroducing an earlier one.
        seen_contract_errors: list[str] = []
        repair_budget = self.validation_repair_attempts
        repair_timeout = _resolve_validation_repair_timeout(effective_timeout)
        for repair_attempt in range(1, repair_budget + 1):
            if not any(result.get("solve_status") == "failed" for result in per_results):
                break
            for result in per_results:
                for contract_error in result.get("validation_contract_errors") or []:
                    text = str(contract_error)
                    if text and text not in seen_contract_errors:
                        seen_contract_errors.append(text)
            self._progress.record(
                shard=original_shard_name,
                worker=worker,
                stage="validate",
                status="running",
                message=(
                    "validation repair: sending host diagnostics to Hermes "
                    f"({repair_attempt}/{repair_budget})"
                ),
            )
            repair_prompt = render_validation_repair_prompt(
                attempt=repair_attempt,
                max_attempts=repair_budget,
                validation_results=per_results,
                prior_contract_errors=seen_contract_errors,
            )
            repair_log = workspace.logs / f"hermes-validation-repair-{repair_attempt}.log"
            pre_signature = _output_signature(workspace.output)
            repair_root_output_snapshot = _project_root_output_snapshot(self.paths.root)
            repair_tailer = WorkspaceProgressTailer(workspace, self._progress.record)
            repair_tailer.start()
            try:
                repair_returncode = self._invoke(
                    repair_prompt,
                    repair_log,
                    dry_run=False,
                    timeout=repair_timeout,
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
            repair_root_output_leaks = _project_root_output_leaks(
                self.paths.root,
                repair_root_output_snapshot,
            )
            if repair_root_output_leaks:
                error = (
                    "workspace output leak during repair: "
                    + ", ".join(repair_root_output_leaks[:5])
                )
                per_results = [
                    {
                        "challenge_id": challenge_id,
                        "solve_status": "failed",
                        "validation_status": "contract_failed",
                        "validation_error": error,
                        "validation_contract_errors": [error],
                    }
                    for challenge_id in challenge_ids
                ]
                merge_validation_into_report(report, per_results)
                break
            post_signature = _output_signature(workspace.output)
            if pre_signature == post_signature:
                _LOGGER.warning(
                    "validation repair attempt %s made no changes under output/; aborting further repair attempts",
                    repair_attempt,
                )
                self._progress.record(
                    shard=original_shard_name,
                    worker=worker,
                    stage="validate",
                    status="running",
                    message=(
                        f"validation repair attempt {repair_attempt}: no changes detected, aborting further attempts"
                    ),
                )
                break
            per_results, validated_set = self._run_workspace_validation(
                original_shard_name,
                worker,
                challenge_ids,
                plan_by_id,
                workspace,
                publication_contract,
            )
            self._record_validation_round(
                workspace, repair_attempt, per_results, validated_set
            )
            merge_validation_into_report(report, per_results)

        # 步骤 10: 根据校验结果判定最终状态
        any_failed = any(result.get("solve_status") == "failed" for result in per_results)
        if any_failed:
            failure_summary = _validation_failure_message(per_results)
            # 有题目校验失败 → 标记分片为 failed
            self._record_per_challenge_complete(original_shard_name, worker, per_results)
            self._progress.record(
                shard=original_shard_name,
                worker=worker,
                stage="complete",
                status="failed",
                message=failure_summary,
            )
            update_report(report, "failed", failure_summary)
            validation_elapsed = elapsed()
            self._augment_failure_report(
                report,
                hermes_phase="validation",
                elapsed_seconds=validation_elapsed,
                workspace=workspace,
            )
            record_workspace_terminal(
                self.paths,
                workspace,
                status="failed",
                output_hash=(
                    validated_set.output_manifest_hash if validated_set else None
                ),
            )
            self.queue.complete(shard, "failed")
            return fail_outcome(
                hermes_phase="validation",
                returncode=returncode if timed_out else 0,
                failure_type="validation",
                error=failure_summary,
                elapsed_seconds=validation_elapsed,
                workspace=workspace,
            )

        # 校验通过后重新捕获精确候选及 hash；任何校验后的修改都会阻止发布。
        try:
            publish_validation_set = prepare_workspace_validation(
                workspace,
                contract=publication_contract,
            )
            if (
                validated_set is None
                or publish_validation_set.output_manifest_hash
                != validated_set.output_manifest_hash
            ):
                raise WorkspacePromotionError(
                    "workspace output changed after successful validation"
                )
            publish_workspace_output(
                self.paths,
                workspace,
                contract=publication_contract,
            )
        except (OSError, WorkspacePromotionError, ValueError) as exc:
            message = f"Validated workspace publication failed: {exc}"
            failure_elapsed = elapsed()
            publisher_phase = getattr(exc, "phase", None)
            self._mark_shard_failed(
                shard,
                original_shard_name,
                worker,
                challenge_ids,
                report,
                message,
                1,
                hermes_phase="materialize",
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
                publisher_phase=publisher_phase,
            )
            return fail_outcome(
                hermes_phase="materialize",
                returncode=1,
                error=message,
                elapsed_seconds=failure_elapsed,
                workspace=workspace,
                publisher_phase=publisher_phase,
            )

        # 所有题目校验并发布成功 → 完成!
        self._record_per_challenge_complete(original_shard_name, worker, per_results)
        self._progress.record(
            shard=original_shard_name,
            worker=worker,
            stage="complete",
            status="passed",
            message="Generation completed",
        )
        update_report(report, "passed")
        self.queue.complete(shard, "done")
        _clear_terminal_staging(workspace)
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
        merge_validation_into_report(report, per_results, shard=shard, worker=worker, runner_status="passed")
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

    def _timeout_recovery_complete(self, original_shard_name: str, challenge_ids: list[str]) -> bool:
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
            build_ok, _ = build_evidence(cp.directory, category, self._image_exists)
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
        validation_targets: dict[str, Path] | None = None,
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
            validation_targets=validation_targets,
        )

    def _run_workspace_validation(
        self,
        original_shard_name: str,
        worker: str,
        challenge_ids: list[str],
        plan_by_id: dict[str, ChallengeResumePlan],
        workspace: ExecutionWorkspace,
        contract: PublicationContract,
    ) -> tuple[list[dict[str, Any]], WorkspaceValidationSet | None]:
        """Validate only exact, allowlisted output under this execution."""
        try:
            validation_set = prepare_workspace_validation(
                workspace,
                contract=contract,
            )
        except (OSError, WorkspacePromotionError, ValueError) as exc:
            claimed_id = getattr(exc, "claimed_id", None)
            phase = getattr(exc, "phase", "allowlist")
            error = f"workspace output {phase} failed: {exc}"
            results = []
            for challenge_id in challenge_ids:
                if claimed_id is not None and challenge_id != claimed_id:
                    continue
                self._progress.record(
                    shard=original_shard_name,
                    challenge_id=challenge_id,
                    worker=worker,
                    stage="validate",
                    status="failed",
                    message=f"validator: status=contract_failed error={error}",
                )
                results.append(
                    {
                        "challenge_id": challenge_id,
                        "solve_status": "failed",
                        "validation_status": "contract_failed",
                        "validation_error": error,
                        "validation_contract_errors": [error],
                    }
                )
            if not results:
                results = [
                    {
                        "challenge_id": challenge_id,
                        "solve_status": "failed",
                        "validation_status": "contract_failed",
                        "validation_error": error,
                        "validation_contract_errors": [error],
                    }
                    for challenge_id in challenge_ids
                ]
            return results, None
        results = self._run_validation(
            original_shard_name,
            worker,
            challenge_ids,
            plan_by_id,
            validation_targets=dict(validation_set.candidates),
        )
        # ChallengeValidator records solve_status/solve_note in metadata.json.
        # Capture the approved hash after those host-owned mutations so the
        # final publish fence compares against the actual validated tree.
        post_validation_set = prepare_workspace_validation(
            workspace,
            contract=contract,
        )
        return results, post_validation_set

    @staticmethod
    def _record_validation_round(
        workspace: ExecutionWorkspace,
        round_no: int,
        results: list[dict[str, Any]],
        validation_set: WorkspaceValidationSet | None,
    ) -> None:
        """Persist first-failure evidence and append bounded repair diagnostics."""
        entry = {
            "round": round_no,
            "output_manifest_hash": (
                validation_set.output_manifest_hash if validation_set else None
            ),
            "results": results,
        }
        history_path = workspace.state / "validation-history.json"
        history = read_json(history_path, [])
        if not isinstance(history, list):
            history = []
        history.append(entry)
        write_json(history_path, history)
        if any(result.get("solve_status") == "failed" for result in results):
            first_path = workspace.state / "first-validation-failure.json"
            if not first_path.exists():
                write_json(first_path, entry)
        else:
            write_json(workspace.state / "validated-output.json", entry)

    def _validate_gate(self, challenge_id: str, plan: ChallengeResumePlan | None) -> str | None:
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
        *,
        hermes_phase: BuildFailureCategory,
        elapsed_seconds: float,
        workspace: ExecutionWorkspace | None = None,
        publisher_phase: str | None = None,
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
        self._augment_failure_report(
            report,
            hermes_phase=hermes_phase,
            elapsed_seconds=elapsed_seconds,
            workspace=workspace,
            publisher_phase=publisher_phase,
        )
        self.queue.complete(shard, "failed")

    @staticmethod
    def _timeout_metadata(workspace: ExecutionWorkspace | None) -> dict[str, Any]:
        if workspace is None:
            return {}
        manifest = read_json(workspace.manifest, {})
        if not isinstance(manifest, dict):
            return {}
        metadata: dict[str, Any] = {}
        effective_timeout = manifest.get("effective_timeout_seconds")
        if isinstance(effective_timeout, int | float):
            metadata["effective_timeout_seconds"] = effective_timeout
        timeout_source = manifest.get("timeout_source")
        if isinstance(timeout_source, str):
            metadata["timeout_source"] = timeout_source
        return metadata

    def _augment_failure_report(
        self,
        report: Path,
        *,
        hermes_phase: BuildFailureCategory,
        elapsed_seconds: float,
        workspace: ExecutionWorkspace | None = None,
        publisher_phase: str | None = None,
    ) -> None:
        raw = read_json(report, {})
        if not isinstance(raw, dict):
            raw = {}
        raw["hermes_phase"] = hermes_phase
        raw["elapsed_seconds"] = max(0.0, elapsed_seconds)
        if publisher_phase:
            raw["publisher_phase"] = publisher_phase
        raw.update(self._timeout_metadata(workspace))
        write_json(report, raw)

    @staticmethod
    def _read_tail(path: Path, limit: int) -> str:
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - limit))
                data = handle.read()
        except OSError:
            return ""
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _read_error_marker(log_path: Path) -> Mapping[str, Any] | None:
        marker = read_json(log_path.with_name(log_path.name + ".error_marker.json"), None)
        return marker if isinstance(marker, Mapping) else None

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
        if (
            hermes_process.project_hermes_home_is_configured(self.paths.hermes_home)
            and not environment.get("HERMES_HOME")
        ):
            environment["HERMES_HOME"] = str(self.paths.hermes_home)
        if self._apply_legacy_custom_provider(environment):
            self._remove_conflicting_custom_pool()
            query_index = arguments.index("-q") if "-q" in arguments else len(arguments)
            arguments[query_index:query_index] = ["--provider", "custom"]

        effective_timeout = timeout if timeout is not None else DEFAULT_HERMES_TIMEOUT
        return hermes_process.invoke(
            prompt,
            arguments=arguments,
            log_path=log,
            cwd=workspace.active if workspace is not None else self.paths.root,
            environment=environment,
            timeout=effective_timeout,
        )

    def _apply_legacy_custom_provider(self, environment: dict[str, str]) -> bool:
        return hermes_process.apply_legacy_custom_provider(self.paths.hermes_home, environment)

    def _remove_conflicting_custom_pool(self) -> bool:
        return hermes_process.remove_conflicting_custom_pool(self.paths.hermes_home)

    @staticmethod
    def _hermes_arguments() -> list[str]:
        return hermes_process.hermes_arguments()


def _clear_terminal_staging(workspace: ExecutionWorkspace) -> None:
    """Remove model-writable staging only after terminal validation success."""
    for directory in (workspace.output, workspace.logs):
        if directory.is_dir():
            shutil.rmtree(directory)


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


_PROJECT_ROOT_LEAK_DIRS = ("output", "challenges", ".design_output")
_PROJECT_ROOT_LEAK_FILE_PREFIXES = (
    "challenge",
    "design",
    "re-",
    "web-",
    "pwn-",
)


def _project_root_output_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Snapshot likely misplaced model outputs under the project root.

    Existing dirty files are tolerated: only paths that are created or modified
    after the snapshot are treated as leaks for the current Hermes invocation.
    """
    snapshot: dict[str, tuple[int, int]] = {}
    for path in _iter_project_root_output_candidates(root):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        snapshot[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _project_root_output_leaks(
    root: Path,
    before: Mapping[str, tuple[int, int]],
) -> list[str]:
    leaks: list[str] = []
    for path in _iter_project_root_output_candidates(root):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        if before.get(relative) != (stat.st_size, stat.st_mtime_ns):
            leaks.append(relative)
    return sorted(leaks)


def _iter_project_root_output_candidates(root: Path) -> Iterable[Path]:
    for dirname in _PROJECT_ROOT_LEAK_DIRS:
        directory = root / dirname
        if directory.is_dir() and not directory.is_symlink():
            yield from directory.rglob("*")
    try:
        direct_children = list(root.iterdir())
    except OSError:
        return
    for path in direct_children:
        if not path.is_file() or path.suffix != ".json":
            continue
        if path.name.startswith(_PROJECT_ROOT_LEAK_FILE_PREFIXES):
            yield path


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
