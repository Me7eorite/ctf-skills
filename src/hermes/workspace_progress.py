"""Workspace-local progress spool generation and live import."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from core.jsonio import read_json
from hermes.workspace import ExecutionWorkspace

LOG = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 0.2
_SHIM = r'''#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--challenge", required=True)
parser.add_argument("--stage", required=True, choices=("design", "implement", "build", "document"))
parser.add_argument("--status", required=True, choices=("pending", "running", "passed", "failed"))
parser.add_argument("--message", required=True)
args = parser.parse_args()
path = Path(__file__).resolve().parent.parent / "logs" / "progress-events.jsonl"
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "challenge": args.challenge,
        "stage": args.stage,
        "status": args.status,
        "message": args.message,
    }, ensure_ascii=False, separators=(",", ":")) + "\n")
'''


def materialize_progress_shim(workspace: ExecutionWorkspace) -> Path:
    path = workspace.root / "bin" / "progress"
    path.write_text(_SHIM, encoding="utf-8")
    path.chmod(0o755)
    return path


class WorkspaceProgressTailer:
    """Live-tail JSONL events and import them through the runner ProgressStore."""

    def __init__(
        self,
        workspace: ExecutionWorkspace,
        record: Callable[..., dict],
        *,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        manifest = read_json(workspace.manifest, {})
        self._shard = str(manifest.get("original_shard_basename", ""))
        self._worker = str(manifest.get("worker", ""))
        payload = read_json(workspace.input / "shard.json", {})
        self._challenge_ids = {
            str(item.get("id"))
            for item in payload.get("challenges", [])
            if isinstance(item, dict) and item.get("id")
        }
        self._path = workspace.logs / "progress-events.jsonl"
        self._record = record
        self._poll_interval = poll_interval
        # 从文件当前末尾开始读，避免 repair 等二次启动 tailer 时重放上一轮已发布的事件
        # （之前 _offset=0 + flush() 会把整个 jsonl 重新导入 ProgressStore，
        #  造成 UI 上 design/build/document 进度瞬间被复读一遍）。
        try:
            self._offset = self._path.stat().st_size
        except FileNotFoundError:
            self._offset = 0
        self._buffer = b""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop_and_flush(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self._poll_interval * 4))
        self.flush()

    def flush(self) -> None:
        try:
            with self._path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
        except FileNotFoundError:
            return
        except OSError as exc:
            LOG.warning("progress spool read failed: %s", exc)
            return
        self._offset += len(chunk)
        data = self._buffer + chunk
        lines = data.split(b"\n")
        self._buffer = lines.pop()
        for raw in lines:
            if raw:
                self._import_line(raw)

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            self.flush()

    def _import_line(self, raw: bytes) -> None:
        try:
            event: Any = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            LOG.warning("invalid workspace progress event skipped: %s", exc)
            return
        if not isinstance(event, dict):
            LOG.warning("non-object workspace progress event skipped")
            return
        challenge = event.get("challenge")
        stage = event.get("stage")
        status = event.get("status")
        message = event.get("message")
        if (
            challenge not in self._challenge_ids
            or stage not in {"design", "implement", "build", "document"}
            or status not in {"pending", "running", "passed", "failed"}
            or not isinstance(message, str)
        ):
            LOG.warning("out-of-contract workspace progress event skipped")
            return
        self._record(
            shard=self._shard,
            challenge_id=challenge,
            worker=self._worker,
            stage=stage,
            status=status,
            message=message,
        )
