"""用于 Dashboard 的持久化题目种子配置。

种子（seed）是人工编写的题目原型数据，包含 id、标题、难度、类别等基本信息。
AI 在种子的基础上展开完整设计。SeedStore 提供种子的 CRUD 操作和入队功能。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.jsonio import read_json, write_json
from core.paths import ProjectPaths
from core.queue import SUPPORTED_CATEGORIES, split_challenges

# 允许的难度值
DIFFICULTIES = {"easy", "medium", "hard", "expert"}
# 种子 ID 格式: 类别-小写字母数字组合（如 web-0001、pwn-sqli-basic）
SEED_ID = re.compile(r"^(web|pwn|re)-[a-z0-9][a-z0-9-]*$")
# 必须为非空字符串的文本字段
REQUIRED_TEXT_FIELDS = (
    "id",                  # 题目 ID
    "title",               # 标题
    "category",            # 类别
    "difficulty",          # 难度
    "primary_technique",   # 核心技术
    "learning_objective",  # 学习目标
)


class SeedStore:
    """种子数据的校验、持久化和管理。

    种子存储在单个 JSON 文件 challenge-seeds.json 中，
    使用原子写入（先写 .tmp 再 rename）避免文件损坏。
    """

    def __init__(self, paths: ProjectPaths):
        self.paths = paths

    def list(self) -> list[dict[str, Any]]:
        """列出所有种子（按 id 排序）。"""
        payload = read_json(self.paths.challenge_seeds, {"seeds": []})
        seeds = payload.get("seeds", []) if isinstance(payload, dict) else []
        return sorted(
            (item for item in seeds if isinstance(item, dict)),
            key=lambda item: str(item.get("id", "")),
        )

    def save(self, seed: Any) -> dict[str, Any]:
        """保存一个种子（新增或更新）。

        如果种子 id 已存在则更新，否则追加。
        返回规范化后的种子数据。
        """
        normalized = validate_seed(seed)
        seeds = self.list()
        # 按 id 查找并更新，或追加新种子
        for index, current in enumerate(seeds):
            if current.get("id") == normalized["id"]:
                seeds[index] = normalized
                break
        else:
            seeds.append(normalized)
        self._write(seeds)
        return normalized

    def delete(self, challenge_id: str) -> None:
        """按 challenge_id 删除种子。

        参数中的 challenge_id 会先做路径消毒（只取文件名），
        防止路径遍历攻击。
        """
        safe_id = Path(challenge_id).name
        seeds = self.list()
        remaining = [item for item in seeds if item.get("id") != safe_id]
        if len(remaining) == len(seeds):
            # 没有找到匹配的种子 → 报错
            raise FileNotFoundError(safe_id)
        self._write(remaining)

    def enqueue(self, size: int = 5) -> list[Path]:
        """将所有种子拆分为分片并放入 pending 队列。

        调用前会重新校验所有种子，确保数据一致性。
        使用 overwrite=False 避免意外覆盖已有分片。

        参数:
            size: 每个分片的题目数量（默认 5）

        返回:
            生成的分片文件路径列表
        """
        seeds = self.list()
        if not seeds:
            raise ValueError("请先保存至少一个题目种子")
        # 重新校验并使用规范化后的种子，确保外部编辑过的文件不会绕过规范化。
        seeds = [validate_seed(seed) for seed in seeds]
        return split_challenges(
            seeds,
            self.paths.shards / "pending",
            size,
            overwrite=False,
        )

    def _write(self, seeds: list[dict[str, Any]]) -> None:
        """原子写入种子文件。

        先写临时文件，再用 rename 替换目标文件。
        这样可以避免写入过程中断导致的文件损坏。
        """
        destination = self.paths.challenge_seeds
        temporary = destination.with_suffix(".json.tmp")
        write_json(temporary, {"seeds": sorted(seeds, key=lambda item: item["id"])})
        temporary.replace(destination)


def validate_seed(seed: Any) -> dict[str, Any]:
    """校验并规范化单个种子数据。

    校验规则:
      - 必须是 dict
      - 必填文本字段不能为空
      - category 必须是 web/pwn/re 之一
      - id 必须符合 "{类别}-xxx" 格式
      - difficulty 必须是 easy/medium/hard/expert
      - points 必须是正整数
      - web/pwn 必须指定有效端口（1-65535）

    规范化操作:
      - 文本字段做 strip
      - category 做 lower
      - id 做 lower 并校验前缀一致性

    返回:
        规范化后的种子 dict
    """
    if not isinstance(seed, dict):
        raise ValueError("种子必须是 JSON 对象")

    normalized = dict(seed)

    # 1. 必填文本字段校验 + strip
    for field in REQUIRED_TEXT_FIELDS:
        value = normalized.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} 不能为空")
        normalized[field] = value.strip()

    # 2. 类别校验
    category = normalized["category"].lower()
    normalized["category"] = category
    if category not in SUPPORTED_CATEGORIES:
        raise ValueError("category 必须是 web、pwn 或 re")

    # 3. ID 格式校验
    if not SEED_ID.fullmatch(normalized["id"].lower()):
        raise ValueError("id 必须类似 web-0001、pwn-0001 或 re-0001")
    normalized["id"] = normalized["id"].lower()
    # ID 前缀必须与 category 匹配
    if not normalized["id"].startswith(f"{category}-"):
        raise ValueError("id 前缀必须与 category 一致")

    # 4. 难度校验
    difficulty = normalized["difficulty"].lower()
    if difficulty not in DIFFICULTIES:
        raise ValueError("difficulty 必须是 easy、medium、hard 或 expert")
    normalized["difficulty"] = difficulty

    # 5. 分值校验
    try:
        points = int(normalized.get("points", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("points 必须是正整数") from exc
    if points < 1:
        raise ValueError("points 必须是正整数")
    normalized["points"] = points

    # 6. 端口校验（仅 web/pwn 需要）
    if category in {"web", "pwn"}:
        try:
            port = int(normalized.get("port", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Web/Pwn 种子必须配置有效端口（1-65535）") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Web/Pwn 种子必须配置有效端口（1-65535）")
        normalized["port"] = port

    return normalized
