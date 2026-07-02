"""Dashboard read model and local task management."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from core.clock import beijing_isoformat_seconds
from core.jsonio import read_json
from core.paths import ProjectPaths
from core.queue import ShardQueue
from core.state import InMemoryProgressStore, ProgressStore
from hermes.runner import validation_repair_timeout_cap
from domain.seeds import SeedStore


def relative_time(timestamp: float) -> str:
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def beijing_now_display() -> str:
    return beijing_isoformat_seconds(datetime.now(timezone.utc)) or ""


class TaskManager:
    """Runs at most one local background CLI task."""

    _DEFAULT_REPAIR_SAFETY_TIMEOUT = 60

    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._kind: str | None = None
        self._started_at: str | None = None
        self._log: str | None = None
        self._worker_ids: set[str] = set()
        self._lane_pools: dict[str, LanePool] = {}

    def start(self, kind: str) -> tuple[bool, str]:
        cli_script = Path(__file__).resolve().parents[1] / "cli.py"
        commands = {
            "worker": [
                sys.executable,
                str(cli_script),
                "run",
                "--worker",
                "dashboard-01",
                "--loop",
            ],
            "validate": [
                sys.executable,
                str(cli_script),
                "validate",
            ],
        }
        if kind not in commands:
            return False, "unknown action"
        return self._start(
            kind,
            commands[kind],
            require_pending=kind == "worker",
            worker_ids={"dashboard-01"} if kind == "worker" else set(),
        )

    def start_worker(
        self,
        *,
        category: str,
        build_attempt_id: UUID,
    ) -> tuple[bool, str]:
        cli_script = Path(__file__).resolve().parents[1] / "cli.py"
        return self._start(
            "worker",
            [
                sys.executable,
                str(cli_script),
                "run",
                "--worker",
                "dashboard-01",
                "--category",
                category,
                "--build-attempt",
                str(build_attempt_id),
                "--allow-failed-attempts-exit-zero",
            ],
            require_pending=False,
            worker_ids={"dashboard-01"},
        )

    def start_sequential_worker(
        self,
        *,
        build_attempt_ids: list[UUID],
    ) -> tuple[bool, str]:
        """Run an explicit build-attempt list in the supplied order."""
        if not build_attempt_ids:
            return False, "顺序队列至少需要一个构建任务"
        for attempt_id in build_attempt_ids:
            pass
        cli_script = Path(__file__).resolve().parents[1] / "cli.py"
        command = [
            sys.executable,
            str(cli_script),
            "run",
            "--worker",
            "dashboard-sequential-01",
        ]
        for attempt_id in build_attempt_ids:
            command.extend(["--build-attempt-sequence", str(attempt_id)])
        command.append("--allow-failed-attempts-exit-zero")
        # Do NOT eagerly mark the whole batch running here: the CLI sequence
        # driver claims and leases each attempt only when its turn comes
        # (`_mark_attempt_running`), and heartbeats just the active one. Leasing
        # every attempt up front gave the waiters a frozen lease that the reaper
        # expired as `lost` while an earlier attempt was still building.
        return self._start(
            "sequential-worker",
            command,
            require_pending=False,
            worker_ids={"dashboard-sequential-01"},
        )

    def start_sequential_lanes(
        self,
        *,
        lanes: list[list[UUID]],
    ) -> tuple[bool, str, dict]:
        """Run multiple independent ordered build-attempt lanes."""
        lane_batches = [lane for lane in lanes if lane]
        if not lane_batches:
            return False, "多队列至少需要一个构建任务", {}
        pool_id = uuid4().hex[:12]
        started_at = beijing_now_display()
        cli_script = Path(__file__).resolve().parents[1] / "cli.py"
        pool = LanePool(
            id=pool_id,
            started_at=started_at,
            lanes=[],
        )
        started_lanes: list[LaneProcess] = []

        with self._lock:
            if self._process and self._process.poll() is None:
                return False, "another task is already running", {}
            if self._active_lane_count_unlocked():
                return False, "another lane pool is already running", {}
            self.paths.logs.mkdir(parents=True, exist_ok=True)
            try:
                for index, attempt_ids in enumerate(lane_batches, start=1):
                    worker = f"dashboard-lane-{index:02d}-{pool_id[:8]}"
                    log = f"dashboard-lane-{pool_id}-{index:02d}.log"
                    command = [
                        sys.executable,
                        str(cli_script),
                        "run",
                        "--worker",
                        worker,
                    ]
                    for attempt_id in attempt_ids:
                        command.extend(["--build-attempt-sequence", str(attempt_id)])
                    command.append("--allow-failed-attempts-exit-zero")
                    with (self.paths.logs / log).open("w", encoding="utf-8") as output:
                        process = subprocess.Popen(
                            command,
                            cwd=self.paths.root,
                            stdout=output,
                            stderr=subprocess.STDOUT,
                            text=True,
                            start_new_session=True,
                        )
                    lane = LaneProcess(
                        lane=index,
                        worker=worker,
                        build_attempt_ids=attempt_ids,
                        log=log,
                        process=process,
                    )
                    pool.lanes.append(lane)
                    started_lanes.append(lane)
                self._lane_pools[pool_id] = pool
            except Exception:
                for lane in started_lanes:
                    if lane.process.poll() is None:
                        lane.process.terminate()
                raise

        time.sleep(0.2)
        failed = [lane for lane in started_lanes if lane.process.poll() is not None]
        if failed:
            for lane in started_lanes:
                if lane.process.poll() is None:
                    lane.process.terminate()
            failed_lanes = ", ".join(str(lane.lane) for lane in failed)
            return (
                False,
                f"lane pool 启动后有队列立即退出：lane {failed_lanes}",
                self._lane_pool_state(pool),
            )
        return (
            True,
            f"多队列已启动 · {len(started_lanes)} 条 lane",
            self._lane_pool_state(pool),
        )

    def _start(
        self,
        kind: str,
        command: list[str],
        *,
        require_pending: bool,
        on_started=None,
        worker_ids: set[str] | None = None,
    ) -> tuple[bool, str]:
        if require_pending and not any(
            (self.paths.shards / "pending").glob("*.json")
        ):
            running = len(
                [
                    path
                    for path in (self.paths.shards / "running").glob("*.json")
                    if not path.name.endswith(".claim.json")
                ]
            )
            if running:
                return False, (
                    f"没有 pending 分片；发现 {running} 个 running 分片，"
                    "请确认原 Worker 已停止后将其重新入队"
                )
            return False, "没有待处理分片，请先执行 split 或重试失败分片"

        with self._lock:
            if self._process and self._process.poll() is None:
                return False, "another task is already running"
            if self._active_lane_count_unlocked():
                return False, "another lane pool is already running"
            self.paths.logs.mkdir(parents=True, exist_ok=True)
            self._log = f"dashboard-{kind}.log"
            with (self.paths.logs / self._log).open("w", encoding="utf-8") as output:
                self._process = subprocess.Popen(
                    command,
                    cwd=self.paths.root,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            self._kind = kind
            self._started_at = beijing_now_display()
            self._worker_ids = set(worker_ids or ())
            process = self._process

        if on_started is not None:
            on_started()

        time.sleep(0.2)
        returncode = process.poll()
        if returncode is not None:
            detail = self._log_tail()
            message = f"{kind} 启动后立即退出，退出码 {returncode}"
            if detail:
                message = f"{message}: {detail}"
            return False, message
        return True, f"{kind} 已启动"

    def stop(self) -> tuple[bool, str]:
        """Terminate the active dashboard build worker or lane pool."""
        with self._lock:
            single = self._process if self._process and self._process.poll() is None else None
            single_kind = self._kind
            lanes = [
                lane
                for pool in self._lane_pools.values()
                for lane in pool.lanes
                if lane.process.poll() is None
            ]
        if single is None and not lanes:
            return False, "没有正在运行的构建任务"

        stopped = 0
        killed = 0
        if single is not None:
            was_killed = _terminate_process(
                single,
                timeout=self._terminate_timeout_seconds(),
            )
            stopped += 1
            killed += 1 if was_killed else 0
        for lane in lanes:
            was_killed = _terminate_process(
                lane.process,
                timeout=self._terminate_timeout_seconds(),
            )
            stopped += 1
            killed += 1 if was_killed else 0

        label = single_kind or ("lane pool" if lanes else "worker")
        if killed:
            return True, f"已结束 {label}（{stopped} 个进程，{killed} 个强制结束）"
        return True, f"已结束 {label}（{stopped} 个进程）"

    @staticmethod
    def _terminate_timeout_seconds() -> float:
        configured = _env_positive_int("DASHBOARD_WORKER_TERMINATE_TIMEOUT")
        if configured is not None:
            return float(configured)
        return float(_repair_timeout_budget_seconds())

    def state(self) -> dict:
        with self._lock:
            lane_pools = self._lane_pools_state_unlocked()
            if self._process is None:
                return {"running": False, "lane_pools": lane_pools}
            return {
                "running": self._process.poll() is None,
                "kind": self._kind,
                "started_at": self._started_at,
                "returncode": self._process.poll(),
                "log": self._log,
                "message": self._process_message(),
                "lane_pools": lane_pools,
            }

    def lane_pools_state(self) -> list[dict]:
        with self._lock:
            return self._lane_pools_state_unlocked()

    def finished_build_workers(self) -> list[dict]:
        """Return dashboard-owned build workers whose process has exited."""
        records: list[dict] = []
        with self._lock:
            if self._process is not None and self._worker_ids:
                returncode = self._process.poll()
                if returncode is not None:
                    records.append(
                        {
                            "kind": self._kind,
                            "worker_ids": sorted(self._worker_ids),
                            "returncode": returncode,
                        }
                    )
            for pool in self._lane_pools.values():
                for lane in pool.lanes:
                    returncode = lane.process.poll()
                    if returncode is None:
                        continue
                    records.append(
                        {
                            "kind": "lane",
                            "worker_ids": [lane.worker],
                            "returncode": returncode,
                            "build_attempt_ids": [
                                str(item) for item in lane.build_attempt_ids
                            ],
                        }
                    )
        return records

    def _process_message(self) -> str:
        if self._process is None:
            return ""
        returncode = self._process.poll()
        if returncode is None:
            return f"{self._kind} 正在运行"
        if returncode == 0:
            return f"{self._kind} 已完成"
        return f"{self._kind} 失败，退出码 {returncode}: {self._log_tail()}"

    def _log_tail(self) -> str:
        if not self._log:
            return ""
        return self._log_tail_for(self._log)

    def _log_tail_for(self, log: str) -> str:
        path = self.paths.logs / log
        try:
            lines = [
                line.strip()
                for line in path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if line.strip()
            ]
        except OSError:
            return ""
        return " | ".join(lines[-3:])[-500:]

    def _active_lane_count_unlocked(self) -> int:
        return sum(
            1
            for pool in self._lane_pools.values()
            for lane in pool.lanes
            if lane.process.poll() is None
        )

    def _lane_pools_state_unlocked(self) -> list[dict]:
        return [self._lane_pool_state(pool) for pool in self._lane_pools.values()]

    def _lane_pool_state(self, pool: "LanePool") -> dict:
        lanes = [self._lane_state(lane) for lane in pool.lanes]
        running = any(lane["running"] for lane in lanes)
        return {
            "id": pool.id,
            "started_at": pool.started_at,
            "running": running,
            "lane_count": len(lanes),
            "active_lanes": sum(1 for lane in lanes if lane["running"]),
            "total_attempts": sum(lane["queue_length"] for lane in lanes),
            "succeeded_lanes": sum(1 for lane in lanes if lane["returncode"] == 0),
            "failed_lanes": sum(
                1
                for lane in lanes
                if lane["returncode"] is not None and lane["returncode"] != 0
            ),
            "lanes": lanes,
        }

    def _lane_state(self, lane: "LaneProcess") -> dict:
        returncode = lane.process.poll()
        running = returncode is None
        if running:
            message = f"{lane.worker} 正在运行"
        elif returncode == 0:
            message = f"{lane.worker} 已完成"
        else:
            message = f"{lane.worker} 失败，退出码 {returncode}: {self._log_tail_for(lane.log)}"
        return {
            "lane": lane.lane,
            "worker": lane.worker,
            "build_attempt_ids": [str(item) for item in lane.build_attempt_ids],
            "queue_length": len(lane.build_attempt_ids),
            "running": running,
            "returncode": returncode,
            "log": lane.log,
            "message": message,
        }


@dataclass
class LaneProcess:
    lane: int
    worker: str
    build_attempt_ids: list[UUID]
    log: str
    process: subprocess.Popen


@dataclass
class LanePool:
    id: str
    started_at: str
    lanes: list[LaneProcess]


def _terminate_process(process: subprocess.Popen, *, timeout: float = 5.0) -> bool:
    if process.poll() is not None:
        return False
    _signal_process_tree(process, signal.SIGTERM)
    try:
        process.wait(timeout=timeout)
        return False
    except subprocess.TimeoutExpired:
        _signal_process_tree(process, signal.SIGKILL)
        process.wait(timeout=timeout)
        return True


def _repair_timeout_budget_seconds() -> int:
    """Budget a dashboard worker enough time to finish a repair/finalize cycle.

    We use the repair-round cap as the basic unit, then leave room for the
    finalization write and one more retry window. The point is to keep the
    worker alive long enough for the repair flow to either succeed or fail
    deterministically, rather than getting marked lost mid-iteration.
    """
    round_cap = validation_repair_timeout_cap()
    return max(
        TaskManager._DEFAULT_REPAIR_SAFETY_TIMEOUT,
        int(round_cap * 2 + 120),
    )


def _env_positive_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _signal_process_tree(process: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return
    except OSError:
        if signum == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()


class DashboardService:
    def __init__(
        self,
        paths: ProjectPaths,
        tasks: TaskManager | None = None,
        progress: ProgressStore | None = None,
    ):
        self.paths = paths
        self.queue = ShardQueue(paths)
        self.seeds = SeedStore(paths)
        self.store = progress or InMemoryProgressStore()
        self.tasks = tasks or TaskManager(paths)

    def state(self) -> dict:
        shard_counts, shards = self._shards()
        challenges = self._challenges()
        return {
            "summary": {
                "challenges": len(challenges),
                "validated": sum(
                    item["solve_status"] == "passed" for item in challenges
                ),
                "built": sum(
                    item["build_status"] == "passed" for item in challenges
                ),
                "queue": shard_counts,
                "categories": {
                    category: sum(
                        item["category"] == category for item in challenges
                    )
                    for category in ("web", "pwn", "re")
                },
            },
            "challenges": challenges,
            "seeds": self.seeds.list(),
            "shards": shards,
            "logs": self._logs(),
            "validation": read_json(self.paths.reports / "validation.json", {}),
            "process": self.tasks.state(),
            "progress": self.store.dashboard(),
            "updated_at": beijing_now_display(),
        }

    def ui_state(self) -> dict:
        """Return the minimal dashboard shell state for fast initial paint."""
        return {
            "process": self.tasks.state(),
            "build_readiness": None,
            "sequential_worker_result": None,
            "updated_at": beijing_now_display(),
        }

    def read_log(self, name: str) -> str:
        path = self.paths.logs / Path(name).name
        if not path.exists():
            raise FileNotFoundError(name)
        return path.read_text(encoding="utf-8", errors="replace")[-30000:]

    def requeue_shard(self, name: str, state: str) -> Path:
        if state == "running" and self.tasks.state().get("running"):
            raise RuntimeError("cannot requeue while local task is running")
        return self.queue.requeue(name, state)

    def save_seed(self, seed: object) -> dict:
        return self.seeds.save(seed)

    def delete_seed(self, challenge_id: str) -> None:
        self.seeds.delete(challenge_id)

    def enqueue_seeds(self, size: int) -> list[Path]:
        return self.seeds.enqueue(size)

    def _shards(self) -> tuple[dict[str, int], list[dict]]:
        counts = {}
        rows = []
        for state in ("pending", "running", "done", "failed"):
            directory = self.paths.shards / state
            files = (
                sorted(
                    path
                    for path in directory.glob("*.json")
                    if not path.name.endswith(".claim.json")
                )
                if directory.exists()
                else []
            )
            counts[state] = len(files)
            for path in files:
                payload = read_json(path, {})
                challenges = payload.get("challenges", [])
                rows.append(
                    {
                        "name": path.name,
                        "state": state,
                        "count": len(challenges),
                        "categories": sorted(
                            {
                                item.get("category", "unknown")
                                for item in challenges
                                if isinstance(item, dict)
                            }
                        ),
                        "updated": relative_time(path.stat().st_mtime),
                    }
                )
        return counts, rows

    def _challenges(self) -> list[dict]:
        rows = []
        for metadata_path in sorted(
            self.paths.challenges.glob("*/*/metadata.json")
        ):
            metadata = read_json(metadata_path)
            if not isinstance(metadata, dict):
                continue
            directory = metadata_path.parent
            rows.append(
                {
                    "id": metadata.get("id", directory.name),
                    "title": metadata.get("title", directory.name),
                    "category": metadata.get("category", directory.parent.name),
                    "difficulty": metadata.get("difficulty", "unknown"),
                    "runtime": metadata.get("runtime")
                    or metadata.get("language")
                    or "-",
                    "framework": metadata.get("framework")
                    or metadata.get("target_format")
                    or "-",
                    "build_status": metadata.get("build_status", "unknown"),
                    "solve_status": metadata.get("solve_status", "unknown"),
                    "path": str(directory.relative_to(self.paths.root)).replace(
                        "\\", "/"
                    ),
                    "updated": relative_time(metadata_path.stat().st_mtime),
                }
            )
        return rows

    def _logs(self) -> list[dict]:
        if not self.paths.logs.exists():
            return []
        files = sorted(
            self.paths.logs.glob("*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "updated": relative_time(path.stat().st_mtime),
            }
            for path in files
        ]
