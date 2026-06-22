"""Dashboard read model and local task management."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import UUID

from core.execution_config import execution_minting_enabled, lease_ttl_seconds
from core.jsonio import read_json
from core.paths import ProjectPaths
from core.queue import ShardQueue
from core.state import InMemoryProgressStore, ProgressStore
from domain.seeds import SeedStore
from persistence.repositories import ExecutionsRepository
from persistence.session import transaction


def relative_time(timestamp: float) -> str:
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


class TaskManager:
    """Runs at most one local background CLI task."""

    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._kind: str | None = None
        self._started_at: str | None = None
        self._log: str | None = None

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
        )

    def start_worker(
        self,
        *,
        category: str,
        build_attempt_id: UUID,
    ) -> tuple[bool, str]:
        self._mark_execution_running(build_attempt_id, worker="dashboard-01")
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
            ],
            require_pending=False,
            on_started=lambda: self._mark_execution_running(
                build_attempt_id,
                worker="dashboard-01",
            ),
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
        return self._start(
            "sequential-worker",
            command,
            require_pending=False,
            on_started=lambda: [
                self._mark_execution_running(
                    attempt_id,
                    worker="dashboard-sequential-01",
                )
                for attempt_id in build_attempt_ids
            ],
        )

    def _start(
        self,
        kind: str,
        command: list[str],
        *,
        require_pending: bool,
        on_started=None,
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
            self.paths.logs.mkdir(parents=True, exist_ok=True)
            self._log = f"dashboard-{kind}.log"
            with (self.paths.logs / self._log).open("w", encoding="utf-8") as output:
                self._process = subprocess.Popen(
                    command,
                    cwd=self.paths.root,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            self._kind = kind
            self._started_at = time.strftime("%Y-%m-%d %H:%M:%S")
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

    @staticmethod
    def _mark_execution_running(attempt_id: UUID, *, worker: str) -> None:
        if not execution_minting_enabled():
            return
        with transaction() as session:
            repo = ExecutionsRepository(session)
            latest = repo.latest_for_attempt(attempt_id)
            if latest is None:
                return
            if latest.status == "queued":
                _, token = repo.claim_queued(
                    attempt_id,
                    worker_id=worker,
                    lease_ttl_seconds=lease_ttl_seconds(),
                )
                repo.update_to_running(latest.id, claim_token=token)

    def state(self) -> dict:
        with self._lock:
            if self._process is None:
                return {"running": False}
            return {
                "running": self._process.poll() is None,
                "kind": self._kind,
                "started_at": self._started_at,
                "returncode": self._process.poll(),
                "log": self._log,
                "message": self._process_message(),
            }

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
        path = self.paths.logs / self._log
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
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
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
