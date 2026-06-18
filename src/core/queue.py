"""基于文件系统的分片队列。

使用目录结构 + 原子文件重命名实现的多 Worker 并发安全队列，
无需额外依赖（如 Redis 或数据库锁）。
"""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

from core.jsonio import read_json, read_jsonl, write_json
from core.paths import ProjectPaths

# 当前支持的题目类别。只有这三种类别的题目会被自动分组到分片中。
SUPPORTED_CATEGORIES = {"web", "pwn", "re"}


def split_matrix(matrix: Path, output: Path, size: int) -> list[Path]:
    """从 JSONL 矩阵文件拆分为分片文件。

    这是一个便利函数，内部调用 split_challenges，将 JSONL 文件按行读取后拆分。

    参数:
        matrix: JSONL 格式的题目矩阵文件路径
        output: 分片文件输出目录
        size: 每个分片包含的题目数量

    返回:
        生成的分片文件路径列表
    """
    return split_challenges(read_jsonl(matrix), output, size)


def split_challenges(
    rows: list[dict],
    output: Path,
    size: int,
    *,
    overwrite: bool = True,
) -> list[Path]:
    """将题目列表按类别拆分为分片。

    拆分规则:
      1. 按 category 字段分组（只处理 SUPPORTED_CATEGORIES 中的类别）
      2. 每组内按 id 排序
      3. 每 size 个题目切一片
      4. 分片文件名格式: {类别}-{起始id}-{结束id}.json

    例如: web 类别的题目 web-0001 到 web-0005 会生成文件 "web-0001-0005.json"

    参数:
        rows: 题目字典列表，每个字典至少包含 category 和 id 字段
        output: 输出目录
        size: 每个分片的题目数（必须 >= 1）
        overwrite: 是否覆盖已存在的分片（默认 True）

    返回:
        生成的分片文件路径列表
    """
    # 分片大小校验
    if size < 1:
        raise ValueError("shard size must be at least 1")

    # 第一步：按支持的类别分组
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        category = row.get("category")
        if category in SUPPORTED_CATEGORIES:
            grouped[category].append(row)

    # 确保输出目录存在
    output.mkdir(parents=True, exist_ok=True)

    # 第二步：每组内排序并切片
    planned: list[tuple[Path, list[dict]]] = []
    for rows in grouped.values():
        # 按 id 排序，确保分片顺序可预测
        rows.sort(key=lambda item: item["id"])
        for index in range(0, len(rows), size):
            chunk = rows[index : index + size]
            category = chunk[0]["category"]
            # 从 id 中提取序号部分（如 "web-0001" → "0001"）
            start = chunk[0]["id"].split("-", 1)[1]
            end = chunk[-1]["id"].split("-", 1)[1]
            path = output / f"{category}-{start}-{end}.json"
            # 如果不允许覆盖且文件已存在，则报错
            if path.exists() and not overwrite:
                raise FileExistsError(f"分片已存在: {path.name}")
            planned.append((path, chunk))

    # 第三步：写入分片文件
    for path, chunk in planned:
        write_json(path, {"challenges": chunk})

    return [path for path, _ in planned]


class ShardQueue:
    """基于目录原子操作的分片队列。

    设计原理:
      通过目录结构表示分片的状态（pending → running → done/failed），
      用 Path.replace() 实现原子状态转换。
      多个 Worker 并发 claim 时，操作系统的 rename 保证只有一个成功。
      不需要额外的分布式锁。

    目录结构:
      work/shards/
        ├── pending/      # 等待处理的 .json 分片
        ├── running/      # 正在处理的分片（带 worker 标识）
        ├── done/         # 处理完成的分片
        └── failed/       # 处理失败的分片
    """

    def __init__(self, paths: ProjectPaths):
        self.paths = paths

    def claim(self, worker: str) -> Path | None:
        """认领一个待处理分片。

        原子操作流程:
          1. 遍历 pending 目录下的 .json 文件（按名称排序）
          2. 将文件原子移动到 running/ 目录（rename 操作）
          3. 如果 rename 成功，写入 claim 文件记录认领信息
          4. 如果 rename 失败（被其他 Worker 抢先），继续尝试下一个

        参数:
            worker: Worker 标识符（如主机名）

        返回:
            已认领的分片路径（位于 running/ 目录），
            如果没有可认领的分片则返回 None。
        """
        pending = self.paths.shards / "pending"
        running = self.paths.shards / "running"
        running.mkdir(parents=True, exist_ok=True)

        # 按文件名排序遍历，保证多个 Worker 有相同的处理顺序
        for shard in sorted(pending.glob("*.json")):
            # 目标路径包含 worker 名，这样可以看到是哪个 worker 在处理
            target = running / f"{shard.stem}.{worker}.json"
            try:
                # 原子 rename：成功则分片归当前 worker 所有
                shard.replace(target)
            except FileNotFoundError:
                # 另一个 Worker 抢走了这个分片（已经在 replace 之前移走了）
                continue
            # 写入认领信息文件（claim token）
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

        # 没有待处理的分片了
        return None

    def complete(self, shard: Path, state: str) -> Path:
        """标记分片处理完成。

        参数:
            shard: 当前在 running/ 下的分片路径
            state: 最终状态，必须是 "done" 或 "failed"

        返回:
            分片在目标目录下的新路径

        异常:
            ValueError: state 不是 "done" 或 "failed"
        """
        if state not in {"done", "failed"}:
            raise ValueError(f"invalid final shard state: {state}")

        destination_dir = self.paths.shards / state
        destination_dir.mkdir(parents=True, exist_ok=True)
        # 恢复原始文件名（去掉 worker 后缀）
        destination = destination_dir / self.original_name(shard)
        shard.replace(destination)

        # 同步移动 claim 文件
        claim_path = self._claim_path(shard)
        if claim_path.exists():
            claim_path.replace(self._claim_path(destination))
        return destination

    def retry(self, name: str) -> Path:
        """重新处理失败的分片（快捷方法，调用 requeue）。"""
        return self.requeue(name, "failed")

    def requeue(self, name: str, state: str) -> Path:
        """将分片移回 pending 队列重新等待处理。

        参数:
            name: 分片文件名
            state: 分片当前所在状态（"failed" 或 "running"）

        返回:
            分片在 pending/ 下的新路径

        异常:
            ValueError: state 不是 "failed" 或 "running"
            FileNotFoundError: 源文件不存在或目标已存在
        """
        if state not in {"failed", "running"}:
            raise ValueError(f"invalid requeue state: {state}")

        # 文件名消毒：只取文件名部分，防止路径遍历攻击
        safe_name = Path(name).name
        source = self.paths.shards / state / safe_name

        # running 状态的文件包含 worker 后缀，需要恢复原始名称
        destination_name = (
            self.original_name(source) if state == "running" else safe_name
        )
        destination = self.paths.shards / "pending" / destination_name

        # 安全检查：源文件必须存在，目标路径不能已有文件（防止覆盖其他 Worker 的分片）
        if not source.exists() or destination.exists():
            raise FileNotFoundError("shard cannot be requeued")

        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)

        # 清理 claim 文件（认领信息在新的执行中不再有效）
        claim = self._claim_path(source)
        if claim.exists():
            claim.unlink()

        return destination

    def original_name(self, shard: Path) -> str:
        """获取分片的原始文件名（去掉 worker 后缀）。

        通过读取 claim 文件中的 source_name 字段获取原始文件名。
        如果 claim 文件不存在或字段缺失，回退到当前文件名。
        """
        claim = read_json(self._claim_path(shard), {})
        return claim.get("source_name", shard.name)

    @staticmethod
    def challenge_ids(shard: Path) -> list[str]:
        """从分片文件中提取所有题目的 id 列表。

        解析分片 JSON 文件中的 challenges 数组，提取每个题目的 id 字段。
        会跳过非 dict 类型的条目和没有 id 字段的条目。
        """
        payload = read_json(shard, {})
        return [
            item["id"]
            for item in payload.get("challenges", [])
            if isinstance(item, dict) and item.get("id")
        ]

    @staticmethod
    def _claim_path(shard: Path) -> Path:
        """生成 claim 文件的路径。

        claim 文件保存了分片的认领信息（worker、时间戳等），
        文件名 = 分片文件名 + .claim.json 后缀。
        例如: web-0001-0005.json → web-0001-0005.json.claim.json
        """
        return shard.with_suffix(shard.suffix + ".claim.json")
