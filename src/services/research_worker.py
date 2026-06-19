"""Research queue worker。"""

from __future__ import annotations

import signal
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TextIO
from uuid import UUID

from core.paths import ProjectPaths
from services.research_agent_executor import ResearchAgentExecutor
from services.research_job_service import ResearchJobService, StaleClaimError


class ResearchWorker:
    """从 research queue claim 任务，并交给 executor 执行。"""

    def __init__(
        self,
        paths: ProjectPaths,
        job_service: ResearchJobService,
        agent_executor: ResearchAgentExecutor,
        *,
        log_stream: TextIO | None = None,
    ) -> None:
        self.paths = paths
        self.job_service = job_service
        self.agent_executor = agent_executor
        # 中文注释：默认写 stderr；测试可注入 StringIO 以便断言每一行 transition。
        self._log_stream = log_stream if log_stream is not None else sys.stderr

    def run(
        self,
        agent_id: str,
        *,
        loop: bool,
        max_jobs: int = 0,
        poll_interval_seconds: float = 5.0,
        lease_seconds: int = 900,
        hermes_timeout_seconds: int = 810,
        generation_request_id: UUID | None = None,
    ) -> dict[str, Any]:
        """运行 worker 主循环。Hermes 超时必须短于租约，保证 executor 有机会在租约内写入终态。"""
        self._validate_config(
            agent_id=agent_id,
            poll_interval_seconds=poll_interval_seconds,
            lease_seconds=lease_seconds,
            hermes_timeout_seconds=hermes_timeout_seconds,
        )
        self.paths.initialize()
        self._log(
            f"[research-worker {agent_id}] started "
            f"(loop={loop} max_jobs={max_jobs} lease={lease_seconds}s "
            f"hermes_timeout={hermes_timeout_seconds}s)"
        )

        processed_count = 0
        current_run = None
        try:
            with _sigterm_as_keyboard_interrupt():
                # 主循环：不断 claim 任务并执行，直到达到 max_jobs 或者收到 SIGTERM（转换成 KeyboardInterrupt）。
                # 每次 claim 失败后等待一段时间，避免频繁查询数据库。
                while True:
                    research_run = self.job_service.claim_next_run(
                        agent_id,
                        lease_seconds,
                        generation_request_id=generation_request_id,
                    )
                    if research_run is None:
                        if not loop:
                            self._log(
                                f"[research-worker {agent_id}] queue empty; exit "
                                f"(processed={processed_count})"
                            )
                            break
                        self._log(
                            f"[research-worker {agent_id}] queue empty; "
                            f"sleeping {poll_interval_seconds}s"
                        )
                        time.sleep(poll_interval_seconds)
                        continue

                    self._log(
                        f"[research-worker {agent_id}] claimed run "
                        f"{research_run.id} (attempt={research_run.attempt})"
                    )
                    current_run = research_run
                    self.agent_executor.execute(
                        research_run,
                        agent_id,
                        lease_seconds,
                        hermes_timeout_seconds,
                    )
                    current_run = None
                    processed_count += 1
                    self._log(
                        f"[research-worker {agent_id}] finished run "
                        f"{research_run.id} (processed={processed_count})"
                    )
                    if max_jobs and processed_count >= max_jobs:
                        self._log(
                            f"[research-worker {agent_id}] reached "
                            f"max_jobs={max_jobs}; exit"
                        )
                        break
        except KeyboardInterrupt:
            if current_run is not None:
                self._cancel_current_run(current_run, agent_id)
            self._log(
                f"[research-worker {agent_id}] interrupted "
                f"(processed={processed_count}); current run cancellation attempted"
            )
            return {
                "processed": processed_count,
                "agent_id": agent_id,
                "interrupted": True,
            }

        return {"processed": processed_count, "agent_id": agent_id}

    def _cancel_current_run(self, run, agent_id: str) -> None:
        if run.claim_token is None:
            return
        try:
            self.job_service.mark_run_failed(
                run.id,
                agent_id,
                run.claim_token,
                "cancelled by operator",
                log_path=self.paths.research_logs / f"{run.id}.log",
            )
            self._log(
                f"[research-worker {agent_id}] cancelled run {run.id} after interrupt"
            )
        except StaleClaimError:
            self._log(
                f"[research-worker {agent_id}] skipped cancel for stale run {run.id}"
            )

    def _log(self, message: str) -> None:
        """写一条 transition 日志到配置的流。"""
        # 中文注释：单行写出 + flush，便于 tail / journalctl 实时观察。
        print(message, file=self._log_stream, flush=True)

    @staticmethod
    def _validate_config(
        *,
        agent_id: str,
        poll_interval_seconds: float,
        lease_seconds: int,
        hermes_timeout_seconds: int,
    ) -> None:
        """校验 worker 运行参数。所有配置错误都在 claim 数据库任务之前暴露，避免留下半启动状态。"""
        if not agent_id:
            raise ValueError("agent_id is required")
        if poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be positive, got {poll_interval_seconds}"
            )
        if lease_seconds <= 0:
            raise ValueError(f"lease_seconds must be positive, got {lease_seconds}")
        if hermes_timeout_seconds <= 0:
            raise ValueError(
                f"hermes_timeout_seconds must be positive, got {hermes_timeout_seconds}"
            )
        if hermes_timeout_seconds >= lease_seconds:
            raise ValueError(
                "hermes_timeout_seconds must be less than lease_seconds "
                f"(got {hermes_timeout_seconds} >= {lease_seconds})"
            )


@contextmanager
def _sigterm_as_keyboard_interrupt() -> Iterator[None]:
    """把 SIGTERM 临时转换成 KeyboardInterrupt。"""
    if threading.current_thread() is not threading.main_thread():
        # signal 只能在主线程安装；测试或嵌入式线程运行时跳过安装。
        yield
        return

    previous_handler = signal.getsignal(signal.SIGTERM)
    # 先保存原有的 SIGTERM 处理器，退出时恢复它，避免影响其他代码。

    def raise_keyboard_interrupt(_signum, _frame):
        # 定义新的 SIGTERM 处理器，直接抛出 KeyboardInterrupt。这样在主循环里就能捕获到 SIGTERM，并优雅退出。
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, raise_keyboard_interrupt)
    # 安装新的 SIGTERM 处理器，把它转换成 KeyboardInterrupt，方便在主循环捕获并优雅退出。
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        # 恢复原有的 SIGTERM 处理器，避免影响其他代码。
