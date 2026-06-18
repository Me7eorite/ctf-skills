"""简单、可预测的 JSON 文件读写工具。

统一了项目中所有 JSON 文件的编码格式（UTF-8）、缩进风格（2 空格）
和错误处理策略，避免各处代码重复。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    """读取 JSON 文件。

    参数:
        path: JSON 文件路径
        default: 读取失败时的默认返回值（默认 None）

    返回:
        解析后的 Python 对象（dict/list/str 等），
        如果文件不存在或 JSON 格式非法则返回 default。

    不抛出异常的设计理念：
      项目中的 JSON 文件多为运行时产物，可能被中途删除或损坏。
      与其崩溃，不如返回默认值让调用方优雅降级。
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 文件不存在 / 无权限 / JSON 格式错误 → 返回默认值
        return default


def write_json(path: Path, payload: Any) -> None:
    """写入 JSON 文件。

    参数:
        path: 输出文件路径（父目录不存在时自动创建）
        payload: 要序列化的 Python 对象

    输出格式:
      - UTF-8 编码
      - 2 空格缩进
      - 不转义 Unicode 字符（ensure_ascii=False，中文直接可见）
      - 末尾带换行符（符合 POSIX 惯例）
    """
    # 自动创建父目录（parent 是目录名，mkdir 需要 exist_ok 来避免并发创建冲突）
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    """读取 JSONL 文件（JSON Lines 格式，每行一个独立 JSON 对象）。

    参数:
        path: JSONL 文件路径

    返回:
        dict 列表，每个元素对应文件中一行 JSON。

    异常:
        ValueError: 任意行 JSON 解析失败时抛出，包含文件名和行号。
        OSError: 文件无法打开时由 Python 运行时抛出。

    注意：
      空行会被自动跳过（常见的手工编辑遗留），
      但非空的非法 JSON 行会立即报错（开箱即用的质量保证）。
    """
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            # 跳过空行，避免 json.loads 对空行报错
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                # 报错时附上文件名和行号，方便快速定位问题
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows
