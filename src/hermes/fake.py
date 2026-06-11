"""Demo-mode Hermes replay for the dashboard."""

from __future__ import annotations

import shutil
import threading
import time

from core.jsonio import write_json
from core.paths import ProjectPaths
from core.queue import ShardQueue, split_challenges
from core.state import STAGES, StateStore

DEMO_WORKER = "demo-01"
DEMO_ROWS = [
    {
        "id": "web-demo-0001",
        "title": "Token Mirror",
        "category": "web",
        "difficulty": "easy",
        "runtime": "node",
        "framework": "Express",
        "points": 100,
        "primary_technique": "auth bypass",
        "learning_objective": "Trace how misplaced trust leaks a token",
        "port": 3000,
    },
    {
        "id": "re-demo-0001",
        "title": "Signal Needle",
        "category": "re",
        "difficulty": "medium",
        "language": "c",
        "target_format": "elf",
        "points": 150,
        "primary_technique": "string transform",
        "learning_objective": "Recover a flag from a compiled transform",
    },
    {
        "id": "pwn-demo-0001",
        "title": "Stack Postcard",
        "category": "pwn",
        "difficulty": "easy",
        "runtime": "glibc",
        "target_format": "elf",
        "points": 120,
        "primary_technique": "ret2win",
        "learning_objective": "Connect a controlled overwrite to ret2win",
        "port": 31337,
    },
]


class FakeHermesRunner:
    """Replay a short deterministic run through the real state plane."""

    def __init__(self, paths: ProjectPaths, delay: float = 0.05):
        self.paths = paths
        self.delay = delay
        self.state = StateStore(paths)
        self.queue = ShardQueue(paths)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name="fake-hermes-demo", daemon=True)
        thread.start()
        return thread

    def run(self) -> None:
        self.paths.initialize()
        self._reset_demo_state()
        self._write_demo_challenges()
        created = split_challenges(DEMO_ROWS, self.paths.shards / "pending", 1)
        self.state.delete_events_for_shards([path.name for path in created])

        while True:
            shard = self.queue.claim(DEMO_WORKER)
            if shard is None:
                break
            original = self.queue.original_name(shard)
            challenge_ids = self.queue.challenge_ids(shard)
            self.state.record(
                shard=original,
                worker=DEMO_WORKER,
                stage="queued",
                status="running",
                message="Demo worker claimed shard",
            )
            for challenge_id in challenge_ids:
                self._replay_challenge(original, challenge_id)
            self.state.record(
                shard=original,
                worker=DEMO_WORKER,
                stage="complete",
                status="passed",
                message="Demo shard complete",
            )
            self.queue.complete(shard, "done")

    def _replay_challenge(self, shard: str, challenge_id: str) -> None:
        for stage in STAGES:
            status = "passed" if stage == "complete" else "running"
            self.state.record(
                shard=shard,
                challenge_id=challenge_id,
                worker=DEMO_WORKER,
                stage=stage,
                status=status,
                message=f"Demo {stage} stage for {challenge_id}",
            )
            time.sleep(self.delay)

    def _reset_demo_state(self) -> None:
        demo_shards = set()
        for state in ("pending", "running", "done", "failed"):
            directory = self.paths.shards / state
            if not directory.exists():
                continue
            for path in directory.glob("*demo*.json*"):
                if path.suffix == ".json":
                    demo_shards.add(path.name)
                path.unlink(missing_ok=True)
        self.state.delete_events_for_shards(sorted(demo_shards))

        for row in DEMO_ROWS:
            category_dir = self.paths.challenges / row["category"]
            for path in category_dir.glob(f"{row['id']}*"):
                if path.is_dir():
                    shutil.rmtree(path)

    def _write_demo_challenges(self) -> None:
        for row in DEMO_ROWS:
            challenge_dir = self.paths.challenges / row["category"] / f"{row['id']}-demo"
            metadata = {
                "id": row["id"],
                "title": row["title"],
                "category": row["category"],
                "difficulty": row["difficulty"],
                "build_status": "passed",
                "solve_status": "passed",
                "flag": f"flag{{{row['id'].replace('-', '_')}}}",
                "primary_technique": row["primary_technique"],
                "learning_objective": row["learning_objective"],
            }
            for key in ("runtime", "framework", "language", "target_format", "port"):
                if key in row:
                    metadata[key] = row[key]
            write_json(challenge_dir / "metadata.json", metadata)
