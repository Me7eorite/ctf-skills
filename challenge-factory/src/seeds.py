"""Persistent challenge seed configuration for the dashboard."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jsonio import read_json, write_json
from paths import ProjectPaths
from shards import SUPPORTED_CATEGORIES, split_challenges

DIFFICULTIES = {"easy", "medium", "hard", "expert"}
SEED_ID = re.compile(r"^(web|pwn|re)-[a-z0-9][a-z0-9-]*$")
REQUIRED_TEXT_FIELDS = (
    "id",
    "title",
    "category",
    "difficulty",
    "primary_technique",
    "learning_objective",
)


class SeedStore:
    """Validates and persists matrix-compatible challenge seed rows."""

    def __init__(self, paths: ProjectPaths):
        self.paths = paths

    def list(self) -> list[dict[str, Any]]:
        payload = read_json(self.paths.challenge_seeds, {"seeds": []})
        seeds = payload.get("seeds", []) if isinstance(payload, dict) else []
        return sorted(
            (item for item in seeds if isinstance(item, dict)),
            key=lambda item: str(item.get("id", "")),
        )

    def save(self, seed: Any) -> dict[str, Any]:
        normalized = validate_seed(seed)
        seeds = self.list()
        for index, current in enumerate(seeds):
            if current.get("id") == normalized["id"]:
                seeds[index] = normalized
                break
        else:
            seeds.append(normalized)
        self._write(seeds)
        return normalized

    def delete(self, challenge_id: str) -> None:
        safe_id = Path(challenge_id).name
        seeds = self.list()
        remaining = [item for item in seeds if item.get("id") != safe_id]
        if len(remaining) == len(seeds):
            raise FileNotFoundError(safe_id)
        self._write(remaining)

    def enqueue(self, size: int = 5) -> list[Path]:
        seeds = self.list()
        if not seeds:
            raise ValueError("请先保存至少一个题目种子")
        for seed in seeds:
            validate_seed(seed)
        return split_challenges(
            seeds,
            self.paths.shards / "pending",
            size,
            overwrite=False,
        )

    def _write(self, seeds: list[dict[str, Any]]) -> None:
        destination = self.paths.challenge_seeds
        temporary = destination.with_suffix(".json.tmp")
        write_json(temporary, {"seeds": sorted(seeds, key=lambda item: item["id"])})
        temporary.replace(destination)


def validate_seed(seed: Any) -> dict[str, Any]:
    if not isinstance(seed, dict):
        raise ValueError("种子必须是 JSON 对象")

    normalized = dict(seed)
    for field in REQUIRED_TEXT_FIELDS:
        value = normalized.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} 不能为空")
        normalized[field] = value.strip()

    category = normalized["category"].lower()
    normalized["category"] = category
    if category not in SUPPORTED_CATEGORIES:
        raise ValueError("category 必须是 web、pwn 或 re")
    if not SEED_ID.fullmatch(normalized["id"].lower()):
        raise ValueError("id 必须类似 web-0001、pwn-0001 或 re-0001")
    normalized["id"] = normalized["id"].lower()
    if not normalized["id"].startswith(f"{category}-"):
        raise ValueError("id 前缀必须与 category 一致")

    difficulty = normalized["difficulty"].lower()
    if difficulty not in DIFFICULTIES:
        raise ValueError("difficulty 必须是 easy、medium、hard 或 expert")
    normalized["difficulty"] = difficulty

    try:
        points = int(normalized.get("points", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("points 必须是正整数") from exc
    if points < 1:
        raise ValueError("points 必须是正整数")
    normalized["points"] = points

    if category in {"web", "pwn"}:
        try:
            port = int(normalized.get("port", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Web/Pwn 种子必须配置有效端口（1-65535）") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Web/Pwn 种子必须配置有效端口（1-65535）")
        normalized["port"] = port

    return normalized
