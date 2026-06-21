"""断点恢复（resume）规划。

在 Worker 认领分片后、写入新的 queued 事件前，
分析上一轮执行窗口中的进度事件和磁盘证据，
生成恢复计划，决定哪些阶段可以跳过、从哪个阶段开始执行。

本模块禁止直接 import subprocess。
唯一的 Docker 检查通过 core.docker.image_exists 委托执行。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.docker import image_exists as default_image_exists
from core.paths import ProjectPaths
from core.state import EXECUTION_STAGES as STAGE_ORDER, ProgressStore

# 文档检查参数：最小字节数、最小标题数
_DOCUMENT_HEADING_PREFIX = "## "
_DOCUMENT_MIN_BYTES = 500
_DOCUMENT_MIN_HEADINGS = 2

# 文档类文件扩展名（不算业务代码）
_DOC_EXTENSIONS = {".md", ".rst", ".txt"}
# 构建类文件名（不算业务代码）
_BUILD_FILENAMES = {
    "Makefile",
    "makefile",
    "GNUmakefile",
    "CMakeLists.txt",
    "build.gradle",
    "build.gradle.kts",
    "build.sh",
}


# ========== 数据结构 ==========

@dataclass(frozen=True)
class ChallengeLookup:
    """按 challenge_id 查找题目目录的结果。"""

    challenge_id: str
    directory: Path | None     # 找到的目录路径（None 表示未找到）
    status: str                # "ok" | "missing_challenge" | "ambiguous_challenge"


@dataclass(frozen=True)
class ChallengeResumePlan:
    """单个题目的恢复计划。

    属性:
        skipped_stages: 可以跳过的阶段列表（已完成的连续前缀，按顺序排列）
        first_pending_stage: 第一个需要执行的阶段（None 表示所有阶段都已跳过）
        stage_sources: 每个跳过阶段对应的历史事件 ID（用于生成 carry-forward 消息）
    """

    challenge_id: str
    directory: Path | None
    lookup_status: str
    skipped_stages: tuple[str, ...] = ()
    first_pending_stage: str | None = "design"
    stage_sources: dict[str, int] = field(default_factory=dict)

    @property
    def all_skipped(self) -> bool:
        """是否所有阶段都已跳过（该题目的生成完全完成）。"""
        return len(self.skipped_stages) == len(STAGE_ORDER)


@dataclass(frozen=True)
class ShardResumePlan:
    """整个分片的恢复计划。"""

    shard: str
    previous_claim_event_id: int | None          # 上一轮的认领事件 ID
    challenges: tuple[ChallengeResumePlan, ...]   # 各题目的恢复计划

    @property
    def all_challenges_fully_skipped(self) -> bool:
        """分片中所有题目是否都已完成（可以跳过整个分片）。"""
        return bool(self.challenges) and all(
            plan.all_skipped for plan in self.challenges
        )


# ========== 题目目录查找 ==========

def find_challenge_directory(
    paths: ProjectPaths, challenge_id: str
) -> ChallengeLookup:
    """在 work/challenges/ 下查找匹配 challenge_id 的唯一目录。

    目录命名模式: work/challenges/<category>/<id>-<slug>/
    匹配规则: 目录名以 challenge_id 开头或完全等于 challenge_id。

    返回:
        "ok": 找到唯一目录
        "missing_challenge": 没有匹配的目录
        "ambiguous_challenge": 有多个匹配的目录
    """
    matches: list[Path] = []
    for path in paths.challenges.glob("*/*"):
        if not path.is_dir():
            continue
        name = path.name
        if name == challenge_id or name.startswith(f"{challenge_id}-"):
            matches.append(path)

    if not matches:
        return ChallengeLookup(challenge_id, None, "missing_challenge")
    if len(matches) > 1:
        return ChallengeLookup(challenge_id, None, "ambiguous_challenge")
    return ChallengeLookup(challenge_id, matches[0], "ok")


# ========== 内部工具函数 ==========

def _read_metadata(challenge_dir: Path) -> dict[str, Any] | None:
    """读取题目目录下的 metadata.json（容错读取）。"""
    metadata_path = challenge_dir / "metadata.json"
    if not metadata_path.is_file():
        return None
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_business_source(path: Path) -> bool:
    """判断文件是否为业务源代码（非文档、非构建文件）。

    排除:
      - 空文件
      - 文档类文件（.md/.rst/.txt）
      - 构建类文件（Makefile/build.gradle 等）
      - 以 build/compile 开头的 shell 脚本
    """
    if not path.is_file():
        return False
    try:
        if path.stat().st_size == 0:
            return False
    except OSError:
        return False
    if path.suffix.lower() in _DOC_EXTENSIONS:
        return False
    if path.name in _BUILD_FILENAMES:
        return False
    lowered = path.name.lower()
    if lowered.endswith(".sh") and (
        lowered.startswith("build") or lowered.startswith("compile")
    ):
        return False
    return True


def _any_business_source(root: Path) -> bool:
    """递归检查目录下是否存在至少一个业务源代码文件。"""
    if not root.is_dir():
        return False
    for entry in root.rglob("*"):
        if _is_business_source(entry):
            return True
    return False


# ========== 各阶段的证据检查函数 ==========

def design_evidence(challenge_dir: Path, challenge_id: str) -> bool:
    """检查 design 阶段是否完成: metadata.json 中的 id 字段必须匹配。"""
    metadata = _read_metadata(challenge_dir)
    if metadata is None:
        return False
    return metadata.get("id") == challenge_id


def implement_evidence(challenge_dir: Path, category: str) -> bool:
    """检查 implement 阶段是否完成。

    web/pwn: deploy/src 目录存在 + Dockerfile + docker-compose.yml + 有业务代码
    re: src 目录下存在业务代码
    """
    if category in {"web", "pwn"}:
        deploy = challenge_dir / "deploy"
        if not (deploy / "src").is_dir():
            return False
        if not (deploy / "Dockerfile").is_file():
            return False
        if not (deploy / "docker-compose.yml").is_file():
            return False
        return _any_business_source(deploy / "src")
    if category == "re":
        return _any_business_source(challenge_dir / "src")
    return False


def _sha256_of_file(path: Path) -> str | None:
    """计算文件的 SHA-256 哈希值（分块读取，避免大文件占满内存）。"""
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            # 每次读 64KB，处理任意大小的文件
            for chunk in iter(lambda: handle.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


def _safe_artifact_path(challenge_dir: Path, artifact: str) -> Path | None:
    """安全解析产物路径，确保不会逃逸出题目目录。

    安全检查:
      - 拒绝绝对路径
      - 拒绝包含 .. 的路径（防止目录遍历）
      - 解析后必须在 dist/ 子目录下
    """
    candidate = Path(artifact)
    if candidate.is_absolute():
        return None
    if any(part == ".." for part in candidate.parts):
        return None
    base = challenge_dir.resolve()
    resolved = (challenge_dir / candidate).resolve()
    try:
        relative = resolved.relative_to(base)
    except ValueError:
        return None
    # 必须位于 dist/ 子目录下
    if not relative.parts or relative.parts[0] != "dist":
        return None
    return resolved


def build_evidence(
    challenge_dir: Path,
    category: str,
    image_exists: Callable[[str], bool],
) -> bool:
    """检查 build 阶段是否完成。

    web/pwn: Docker 镜像必须存在且可访问
    re: 产物文件必须存在，且 SHA-256 哈希与 metadata 一致
    """
    metadata = _read_metadata(challenge_dir)
    if metadata is None:
        return False
    if metadata.get("build_status") != "passed":
        return False
    build_command = metadata.get("build_command")
    if not isinstance(build_command, str) or not build_command.strip():
        return False

    if category in {"web", "pwn"}:
        # 容器化题目：检查 Docker 镜像
        docker_image = metadata.get("docker_image")
        if not isinstance(docker_image, str) or not docker_image.strip():
            return False
        return image_exists(docker_image)

    if category == "re":
        # 非容器化题目：检查产物文件哈希
        artifact = metadata.get("artifact")
        expected_sha = metadata.get("artifact_sha256")
        if (
            not isinstance(artifact, str)
            or not artifact.strip()
            or not isinstance(expected_sha, str)
            or not expected_sha.strip()
        ):
            return False
        resolved = _safe_artifact_path(challenge_dir, artifact.strip())
        if resolved is None or not resolved.is_file():
            return False
        actual = _sha256_of_file(resolved)
        return actual is not None and actual == expected_sha.strip()

    return False


def validate_resume_evidence(
    challenge_dir: Path,
    challenge_events: Iterable[dict[str, Any]],
) -> bool:
    """检查 validate 阶段是否完成。

    条件:
      - validate.sh 文件存在
      - writenup/exp.py（解题脚本）文件存在
      - metadata 中 solve_status == "passed"
      - 进度事件中有 validate passed 记录
    """
    if not (challenge_dir / "validate.sh").is_file():
        return False
    if not (challenge_dir / "writenup" / "exp.py").is_file():
        return False
    metadata = _read_metadata(challenge_dir)
    if metadata is None or metadata.get("solve_status") != "passed":
        return False
    return any(
        event.get("stage") == "validate" and event.get("status") == "passed"
        for event in challenge_events
    )


def document_evidence(challenge_dir: Path) -> bool:
    """检查 document 阶段是否完成。

    条件:
      - writenup/wp.md 和 README.md 都存在
      - 文件大小 >= 500 字节
      - 至少包含 2 个 ## 标题
    """
    for relative in ("writenup/wp.md", "README.md"):
        path = challenge_dir / relative
        if not path.is_file():
            return False
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size <= _DOCUMENT_MIN_BYTES:
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        heading_count = sum(
            1 for line in text.splitlines() if line.startswith(_DOCUMENT_HEADING_PREFIX)
        )
        if heading_count < _DOCUMENT_MIN_HEADINGS:
            return False
    return True


# ========== 恢复计划计算 ==========

def _latest_stage_event(
    events: list[dict[str, Any]], stage: str
) -> dict[str, Any] | None:
    """从事件列表中查找指定阶段的最后一条事件。"""
    for event in reversed(events):
        if event.get("stage") == stage:
            return event
    return None


def _stage_evidence_ok(
    stage: str,
    challenge_dir: Path,
    category: str,
    image_exists: Callable[[str], bool],
    events: list[dict[str, Any]],
    challenge_id: str,
) -> bool:
    """检查指定阶段的磁盘证据是否仍然有效。"""
    if stage == "design":
        return design_evidence(challenge_dir, challenge_id)
    if stage == "implement":
        return implement_evidence(challenge_dir, category)
    if stage == "build":
        return build_evidence(challenge_dir, category, image_exists)
    if stage == "validate":
        return validate_resume_evidence(challenge_dir, events)
    if stage == "document":
        return document_evidence(challenge_dir)
    return False


def _category_from_dir(challenge_dir: Path, paths: ProjectPaths) -> str:
    """从题目目录路径推断类别（通过父目录名）。"""
    try:
        relative = challenge_dir.resolve().relative_to(paths.challenges.resolve())
    except ValueError:
        return ""
    return relative.parts[0] if relative.parts else ""


def compute_resume_plan(
    *,
    state: ProgressStore,
    paths: ProjectPaths,
    shard: str,
    challenge_ids: list[str],
    image_exists: Callable[[str], bool] = default_image_exists,
) -> ShardResumePlan:
    """计算分片的恢复计划。

    【重要】调用方必须在本轮写入 queued/running 事件之前调用本函数。
    因为恢复计划通过查询最近的 queued/running 事件来确定时间窗口下界。

    恢复逻辑:
      对每个题目:
        1. 找到上一轮的进度事件（从 claim 事件之后）
        2. 按阶段顺序检查: 如果事件显示 passed 且磁盘证据有效 → 跳过该阶段
        3. 遇到第一个不满足条件的阶段 → 从这里开始执行

    参数:
        state: 进度存储实例
        paths: 项目路径管理
        shard: 分片名称
        challenge_ids: 分片中的所有题目 ID
        image_exists: Docker 镜像检查函数（可注入，方便测试）

    返回:
        包含每个题目恢复计划的 ShardResumePlan
    """
    previous_claim = state.latest_claim_event(shard)
    previous_id = previous_claim["id"] if previous_claim else None

    plans: list[ChallengeResumePlan] = []
    for challenge_id in challenge_ids:
        # 查找题目目录
        lookup = find_challenge_directory(paths, challenge_id)
        if lookup.directory is None:
            # 找不到目录 → 全新开始
            plans.append(
                ChallengeResumePlan(
                    challenge_id=challenge_id,
                    directory=None,
                    lookup_status=lookup.status,
                    skipped_stages=(),
                    first_pending_stage="design",
                    stage_sources={},
                )
            )
            continue

        # 获取类别和上一轮的事件
        category = _category_from_dir(lookup.directory, paths)
        events: list[dict[str, Any]] = []
        if previous_id is not None:
            events = state.events_for_challenge(
                shard, challenge_id, after_id=previous_id
            )

        # 按阶段顺序判断哪些可以跳过
        skipped: list[str] = []
        sources: dict[str, int] = {}
        for stage in STAGE_ORDER:
            latest = _latest_stage_event(events, stage)
            # 需要: 1) 有 passed 事件 2) 磁盘证据仍然有效
            if latest is None or latest.get("status") != "passed":
                break
            if not _stage_evidence_ok(
                stage,
                lookup.directory,
                category,
                image_exists,
                events,
                challenge_id,
            ):
                break
            skipped.append(stage)
            sources[stage] = int(latest["id"])

        # 确定第一个待处理阶段
        next_index = len(skipped)
        first_pending = (
            STAGE_ORDER[next_index] if next_index < len(STAGE_ORDER) else None
        )

        plans.append(
            ChallengeResumePlan(
                challenge_id=challenge_id,
                directory=lookup.directory,
                lookup_status=lookup.status,
                skipped_stages=tuple(skipped),
                first_pending_stage=first_pending,
                stage_sources=sources,
            )
        )

    return ShardResumePlan(
        shard=shard,
        previous_claim_event_id=previous_id,
        challenges=tuple(plans),
    )


# ========== 消息格式化 ==========

def carry_forward_message(stage: str, source_event_id: int) -> str:
    """生成断点恢复的 carry-forward 消息。

    格式: "carry-forward: skipping <stage> from historical event #<id>; evidence revalidated"
    """
    return (
        f"carry-forward: skipping {stage} from historical event "
        f"#{source_event_id}; evidence revalidated"
    )


def validator_message(
    *,
    status: str,
    elapsed: float | None = None,
    flag_matched: bool | None = None,
    error: str | None = None,
) -> str:
    """生成校验器的状态消息。

    格式: "validator: status=<status> elapsed=<seconds>s flag_matched=yes/no error=<msg>"
    """
    parts = [f"validator: status={status}"]
    if elapsed is not None:
        parts.append(f"elapsed={elapsed:.2f}s")
    if flag_matched is not None:
        parts.append(f"flag_matched={'yes' if flag_matched else 'no'}")
    if error:
        parts.append(f"error={error}")
    return " ".join(parts)
