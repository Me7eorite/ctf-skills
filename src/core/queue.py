"""File-backed shard queue operations."""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

from core.jsonio import read_json, read_jsonl, write_json
from core.paths import ProjectPaths

SUPPORTED_CATEGORIES = {"web", "pwn", "re"}


def split_matrix(matrix: Path, output: Path, size: int) -> list[Path]:
    return split_challenges(read_jsonl(matrix), output, size)


def split_challenges(
    rows: list[dict],
    output: Path,
    size: int,
    *,
    overwrite: bool = True,
) -> list[Path]:
    if size < 1:
        raise ValueError("shard size must be at least 1")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        category = row.get("category")
        if category in SUPPORTED_CATEGORIES:
            grouped[category].append(row)

    output.mkdir(parents=True, exist_ok=True)
    planned: list[tuple[Path, list[dict]]] = []
    for rows in grouped.values():
        rows.sort(key=lambda item: item["id"])
        for index in range(0, len(rows), size):
            chunk = rows[index : index + size]
            category = chunk[0]["category"]
            start = chunk[0]["id"].split("-", 1)[1]
            end = chunk[-1]["id"].split("-", 1)[1]
            path = output / f"{category}-{start}-{end}.json"
            if path.exists() and not overwrite:
                raise FileExistsError(f"分片已存在: {path.name}")
            planned.append((path, chunk))
    for path, chunk in planned:
        write_json(path, {"challenges": chunk})
    return [path for path, _ in planned]


class ShardQueue:
    """Atomic directory-based queue for parallel workers."""

    def __init__(self, paths: ProjectPaths):
        self.paths = paths

    def claim(self, worker: str) -> Path | None:
        pending = self.paths.shards / "pending"
        running = self.paths.shards / "running"
        running.mkdir(parents=True, exist_ok=True)

        for shard in sorted(pending.glob("*.json")):
            target = running / f"{shard.stem}.{worker}.json"
            try:
                shard.replace(target)
            except FileNotFoundError:
                continue
            write_json(
                self._claim_path(target),
                {
                    "worker": worker,
                    "claimed_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                    "source_name": shard.name,
                },
            )
            return target
        return None

    def complete(self, shard: Path, state: str) -> Path:
        if state not in {"done", "failed"}:
            raise ValueError(f"invalid final shard state: {state}")

        destination_dir = self.paths.shards / state
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / self.original_name(shard)
        shard.replace(destination)

        claim_path = self._claim_path(shard)
        if claim_path.exists():
            claim_path.replace(self._claim_path(destination))
        return destination

    def retry(self, name: str) -> Path:
        return self.requeue(name, "failed")

    def requeue(self, name: str, state: str) -> Path:
        if state not in {"failed", "running"}:
            raise ValueError(f"invalid requeue state: {state}")
        safe_name = Path(name).name
        source = self.paths.shards / state / safe_name
        destination_name = (
            self.original_name(source) if state == "running" else safe_name
        )
        destination = self.paths.shards / "pending" / destination_name
        if not source.exists() or destination.exists():
            raise FileNotFoundError("shard cannot be requeued")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        claim = self._claim_path(source)
        if claim.exists():
            claim.unlink()
        return destination

    def original_name(self, shard: Path) -> str:
        claim = read_json(self._claim_path(shard), {})
        return claim.get("source_name", shard.name)

    @staticmethod
    def challenge_ids(shard: Path) -> list[str]:
        payload = read_json(shard, {})
        return [
            item["id"]
            for item in payload.get("challenges", [])
            if isinstance(item, dict) and item.get("id")
        ]

    @staticmethod
    def _claim_path(shard: Path) -> Path:
        return shard.with_suffix(shard.suffix + ".claim.json")
