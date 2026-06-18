"""结构化题目设计输出的校验器。

从 Hermes AI 代理的 JSON 输出中解析和校验题目设计方案。
核心职责:
  1. 从 Hermes stdout 中提取设计 JSON（处理 AI 偶尔的 markdown/代码块包装）
  2. 校验设计 payload 的字段完整性、类型正确性
  3. 质量门（quality gate）检查：是否符合质量门规范
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from domain.design_tasks import DesignTask
from domain.research import DIFFICULTY_LABELS

# ========== 常量定义 ==========

# Flag 默认格式
DEFAULT_FLAG_FORMAT = "flag{...}"

# 长度限制
MAX_SUMMARY_CHARS = 280           # 摘要最大字符数
MAX_IMPLEMENTATION_PLAN_CHARS = 4000  # 实现计划最大字符数
MAX_PLAN_STRING_CHARS = 500       # 计划中单个字符串最大字符数

# 通用产物文件（所有类别都需要）
COMMON_ARTIFACTS: tuple[str, ...] = (
    "README.md",
    "metadata.json",
    "validate.sh",
    "writenup/wp.md",
    "writenup/exp.py",
)

# 容器化题目额外需要的产物文件
CONTAINER_ARTIFACTS: tuple[str, ...] = (
    "deploy/Dockerfile",
    "deploy/docker-compose.yml",
    "deploy/src/app.py",
    "deploy/_files/start.sh",
)

# 已知的产物路径前缀（用于判断 artifact 是否为合法的相对路径）
KNOWN_ARTIFACT_PREFIXES: tuple[str, ...] = (
    "deploy/",
    "writenup/",
    "attachments/",
    "dist/",
)

# 禁止出现在设计输出中的实现级字段（设计阶段不应包含代码细节）
FORBIDDEN_IMPLEMENTATION_KEYS: frozenset[str] = frozenset(
    {
        "app_code",            # 应用代码
        "compose_spec",        # Docker Compose 配置
        "docker_compose",      # docker-compose 内容
        "dockerfile",          # Dockerfile 内容
        "dockerfile_snippet",  # Dockerfile 片段
        "exploit_code",        # 漏洞利用代码
        "exploit_sketch",      # 漏洞利用草图
        "files_content",       # 文件内容
        "init_sql",            # 初始化 SQL
        "readme_body",         # README 正文
        "source_code",         # 源代码
        "writeup_body",        # 解题文档正文
    }
)

# 计划字符串中不应出现的代码标记（用于检测 AI 是否在计划中夹带了代码）
PLAN_CODE_MARKERS: tuple[str, ...] = (
    "```",             # 代码块
    "#!/bin/bash",     # Shell 脚本
    "<?php",           # PHP 代码
    "CREATE TABLE",    # SQL 建表
    "FROM ",           # SQL 查询
    "RUN apt-get",     # Dockerfile 指令
    "import requests", # Python 代码
    "services:",       # Docker Compose 配置
)

# 题目必填文本字段
REQUIRED_CHALLENGE_TEXT_FIELDS: tuple[str, ...] = (
    "id",                  # 题目 ID
    "title",               # 标题
    "category",            # 类别
    "difficulty",          # 难度
    "deployment",          # 部署方式
    "primary_technique",   # 核心技术
    "learning_objective",  # 学习目标
    "prompt",              # 选手提示
    "flag_location",       # Flag 位置
    "validation",          # 校验方案
)

# URL 正则（用于检测 artifact 是否包含 URL）
URL_RE = re.compile(r"https?://", re.IGNORECASE)
HTTP_URL_RE = re.compile(r"https?://[^\s\"'<>`)\]}]+", re.IGNORECASE)

# 本地回环地址（允许出现在校验计划中）
LOCAL_HTTP_HOSTS: frozenset[str] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "host.docker.internal",
    }
)


# ========== 异常类 ==========

class ChallengeDesignValidationError(ValueError):
    """设计代理的 JSON 输出不合法时抛出的异常。"""


# ========== 数据类 ==========

@dataclass(frozen=True)
class ValidatedDesignPayload:
    """校验通过后的规范化设计 payload。"""
    payload: dict[str, Any]      # 完整 payload
    challenge: dict[str, Any]    # 单个 challenge 数据
    summary: str                 # 摘要
    flag_format: str             # Flag 格式
    validation_notes: str        # 校验备注


# ========== JSON 解析 ==========

def parse_design_output(stdout: str) -> dict[str, Any]:
    """从 Hermes stdout 中提取第一个设计格式的 JSON 对象。

    AI 的理想输出是纯 JSON 对象（Prompt 的 Output Contract 禁止额外内容），
    但实际上模型有时会:
      - 用 markdown 代码块包裹 JSON
      - 将 JSON 写入文件后只输出摘要
      - 无意中输出 flag{...} 风格的大括号

    本解析器的位置式扫描策略:
      1. 去掉 markdown 代码块标记
      2. 从每个 `{` 位置开始，尝试找到配对的 `}`
      3. 尝试 JSON.parse
      4. 检查是否有顶层 "event" 和 "challenges" 键
      5. 如果匹配失败则继续扫描下一个 `{`

    如果什么都没找到，错误消息会指明 Output Contract，方便排查。
    """
    if not isinstance(stdout, str) or not stdout.strip():
        raise ChallengeDesignValidationError("Hermes output is empty")

    # 第一步: 去掉可能的 markdown 代码块包装
    text = _strip_json_fences(stdout)
    saw_any_brace = False
    last_decode_error: str | None = None

    cursor = 0
    while True:
        # 找到下一个 `{` 的位置
        start = text.find("{", cursor)
        if start < 0:
            break
        saw_any_brace = True

        # 找到配对的 `}` 位置（处理嵌套和字符串中的括号）
        end = _find_balanced_json_object_end(text, start)
        if end is None:
            # 从这里开始括号不平衡，后面不可能再有合法 JSON 了
            break

        block = text[start : end + 1]
        # 跳过当前候选，防止噪声括号（如 flag{...}）无限重复匹配
        cursor = end + 1

        try:
            parsed = json.loads(block)
        except json.JSONDecodeError as exc:
            last_decode_error = exc.msg
            continue

        # 必须有 "event" 和 "challenges" 两个顶层键
        if isinstance(parsed, dict) and "event" in parsed and "challenges" in parsed:
            return parsed
        # JSON 解析成功但不是设计格式 → 继续扫描

    # 生成有意义的错误消息
    if not saw_any_brace:
        raise ChallengeDesignValidationError("Hermes output does not contain JSON")
    if last_decode_error is not None:
        raise ChallengeDesignValidationError(
            "Hermes output does not contain a JSON object with `event` and "
            f"`challenges` (last decode error: {last_decode_error})"
        )
    raise ChallengeDesignValidationError(
        "Hermes output does not contain a JSON object with `event` and "
        "`challenges`; the agent likely wrote the design to a file or replied "
        "with prose. The Output Contract requires the reply itself to be the "
        "JSON object."
    )


# ========== 设计 Payload 校验 ==========

def validate_design_payload(
    payload: Mapping[str, Any],
    parent_task: DesignTask,
) -> ValidatedDesignPayload:
    """校验并规范化一个设计 challenges JSON payload。

    校验流程:
      1. payload 必须是字典
      2. event 必须是字典，包含 flag_format
      3. challenges 必须恰好包含 1 个元素
      4. 检查禁止的实现级字段
      5. 检查 implementation_plan 的合法性
      6. 所有必填字段不能为空
      7. id/category/difficulty 必须与父任务一致
      8. points 必须是正整数且与父任务一致
      9. artifacts 路径合法性检查
      10. hints 必须恰好是 3 条
      11. web/pwn 的 deployment 必须提到 docker，port 必须一致
    """
    if not isinstance(payload, Mapping):
        raise ChallengeDesignValidationError("design payload must be an object")

    # 深拷贝避免修改传入的原始数据
    normalized = copy.deepcopy(dict(payload))

    # ---- event 校验 ----
    event = normalized.get("event")
    if not isinstance(event, dict):
        raise ChallengeDesignValidationError("event must be an object")
    flag_format = event.get("flag_format")
    if flag_format is None:
        # 没有指定 flag 格式 → 使用默认值
        event["flag_format"] = DEFAULT_FLAG_FORMAT
        flag_format = DEFAULT_FLAG_FORMAT
    if not isinstance(flag_format, str) or not flag_format.strip():
        raise ChallengeDesignValidationError("event.flag_format must be a non-empty string")

    # ---- challenges 校验 ----
    challenges = normalized.get("challenges")
    if not isinstance(challenges, list) or len(challenges) != 1:
        raise ChallengeDesignValidationError("challenges must be an array of length 1")

    # 规范化 SKILL.md 格式的字段（如 player_prompt → prompt）
    challenges[0] = _normalize_skill_fields(challenges[0])
    challenge = challenges[0]
    if not isinstance(challenge, dict):
        raise ChallengeDesignValidationError("challenges[0] must be an object")

    # 拒绝实现级字段
    _reject_implementation_payload(challenge)
    # 校验 implementation_plan
    _validate_implementation_plan(challenge.get("implementation_plan"))

    # ---- 必填字段校验 ----
    for field in REQUIRED_CHALLENGE_TEXT_FIELDS:
        _require_non_empty_string(challenge, field)

    # ---- 与父任务的一致性校验 ----
    _require_parent_equal(challenge, "id", parent_task.challenge_id)
    _require_parent_equal(challenge, "category", parent_task.category)
    _require_parent_equal(challenge, "difficulty", parent_task.difficulty)

    # ---- points 校验 ----
    points = challenge.get("points")
    if not isinstance(points, int) or isinstance(points, bool) or points <= 0:
        raise ChallengeDesignValidationError("points must be a positive integer")
    if points != parent_task.points:
        raise ChallengeDesignValidationError("points must equal parent design task points")

    # ---- difficulty 白名单检查 ----
    if challenge["difficulty"] not in DIFFICULTY_LABELS:
        raise ChallengeDesignValidationError("difficulty is not canonical")

    # ---- artifacts 校验 ----
    artifacts = challenge.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ChallengeDesignValidationError("artifacts must normalize to paths")
    for artifact in artifacts:
        if not isinstance(artifact, str) or not artifact.strip():
            raise ChallengeDesignValidationError("artifacts must contain non-empty strings")
        if _is_absolute_or_url_path(artifact):
            raise ChallengeDesignValidationError("artifacts must be relative paths")
        if not _is_artifact_path_like(artifact):
            raise ChallengeDesignValidationError(
                "artifacts must be local challenge-relative file paths"
            )

    # ---- hints 校验（必须恰好 3 条）----
    hints = challenge.get("hints")
    if not isinstance(hints, list) or len(hints) != 3:
        raise ChallengeDesignValidationError("hints must contain exactly 3 entries")
    for hint in hints:
        if not isinstance(hint, str) or not hint.strip():
            raise ChallengeDesignValidationError("hints must contain non-empty strings")

    # ---- validation 中不应包含外部 URL ----
    validation = challenge["validation"]
    if _contains_external_http_url(validation):
        raise ChallengeDesignValidationError("validation must not contain external HTTP URLs")

    # ---- 类别特定检查 ----
    if parent_task.category in {"web", "pwn"}:
        deployment = challenge["deployment"].lower()
        if "docker" not in deployment:
            raise ChallengeDesignValidationError("web/pwn deployment must mention docker")
        port = challenge.get("port")
        if port != parent_task.port:
            raise ChallengeDesignValidationError("port must equal parent design task port")
        _require_artifacts(
            artifacts,
            (*COMMON_ARTIFACTS, *CONTAINER_ARTIFACTS),
            "web/pwn artifacts",
        )
    else:
        _require_artifacts(artifacts, COMMON_ARTIFACTS, "artifacts")

    # ---- 生成摘要 ----
    summary = _make_summary(challenge)
    return ValidatedDesignPayload(
        payload=normalized,
        challenge=challenge,
        summary=summary,
        flag_format=flag_format.strip(),
        validation_notes=validation.strip(),
    )


# ========== 质量门 ==========

def run_quality_gate(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """运行确定性质量断言（quality gate）。

    检查项 (来自 quality-gate.md):
      - learning_objective 是否存在
      - validation plan 是否存在
      - hints 是否恰好 3 条
      - difficulty 是否在标准列表中
      - artifacts 是否为相对路径列表
      - web/pwn 的 deployment 是否容器化且指定了 port

    返回:
        (通过标志, 未通过项列表)
    """
    notes: list[str] = []
    try:
        challenge = _single_challenge(payload)
    except ChallengeDesignValidationError as exc:
        return False, [str(exc)]

    _note_if(
        notes,
        not isinstance(challenge.get("learning_objective"), str)
        or not challenge["learning_objective"].strip(),
        "learning objective is missing",
    )
    _note_if(
        notes,
        not isinstance(challenge.get("validation"), str) or not challenge["validation"].strip(),
        "validation plan is missing",
    )
    _note_if(
        notes,
        not isinstance(challenge.get("hints"), list) or len(challenge["hints"]) != 3,
        "hints are not staged as three entries",
    )
    _note_if(
        notes,
        challenge.get("difficulty") not in DIFFICULTY_LABELS,
        "difficulty is not canonical",
    )

    artifacts = challenge.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        notes.append("artifacts are missing")
    else:
        for artifact in artifacts:
            if not isinstance(artifact, str) or _is_absolute_or_url_path(artifact):
                notes.append("artifacts must be relative paths")
                break

    category = challenge.get("category")
    if category in {"web", "pwn"}:
        deployment = challenge.get("deployment")
        _note_if(
            notes,
            not isinstance(deployment, str) or "docker" not in deployment.lower(),
            "web/pwn deployment must be containerized",
        )
        _note_if(notes, "port" not in challenge, "web/pwn design must define a port")

    return not notes, notes


# ========== 字段规范化 ==========

def _normalize_skill_fields(challenge: Any) -> Any:
    """将 SKILL.md 的输出格式映射到校验器期望的扁平格式。

    skills/design-challenges/SKILL.md 的 Output Shape 比校验器最初定义的格式更丰富:
      - player_prompt → prompt
      - flag_plan.location → flag_location
      - validation (对象) → validation (字符串)
      - artifact 对象 → 相对路径列表
      - hint 对象 → 提示字符串列表

    本函数做兼容转换：如果字段已经是校验器期望的格式，不做处理；
    否则从 SKILL.md 格式中提取等价字段。
    """
    if not isinstance(challenge, dict):
        return challenge
    out = dict(challenge)

    # player_prompt → prompt
    if "prompt" not in out:
        player_prompt = out.get("player_prompt")
        if isinstance(player_prompt, str):
            out["prompt"] = player_prompt

    # flag_plan.location → flag_location
    if "flag_location" not in out:
        flag_plan = out.get("flag_plan")
        if isinstance(flag_plan, dict):
            location = flag_plan.get("location")
            if isinstance(location, str):
                out["flag_location"] = location

    # validation 对象 → 字符串
    validation = out.get("validation")
    if isinstance(validation, dict):
        parts: list[str] = []
        for key in ("reference_solve", "expected_result"):
            value = validation.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        regression = validation.get("regression_checks")
        if isinstance(regression, list):
            parts.extend(
                item.strip()
                for item in regression
                if isinstance(item, str) and item.strip()
            )
        if parts:
            out["validation"] = "\n".join(parts)

    # 规范化 artifacts 和 hints
    out["artifacts"] = _normalize_artifacts(out)
    out["hints"] = _normalize_hints(out.get("hints"), out)

    return out


# ========== Artifact 规范化 ==========

def _normalize_artifacts(challenge: Mapping[str, Any]) -> list[str]:
    """将各种格式的 artifact 定义规范化为统一的相对路径列表。"""
    category = str(challenge.get("category") or "").lower()
    paths: list[str] = []

    def add(value: Any, *, base: str | None = None) -> None:
        """添加一个 artifact 路径（带基础路径前缀处理）。"""
        if not isinstance(value, str):
            return
        if _is_absolute_or_url_path(value):
            paths.append(value.strip().replace("\\", "/"))
            return
        candidate = _normalize_artifact_path(value, base=base)
        if candidate is not None:
            paths.append(candidate)

    # 从 artifacts 字段提取
    artifacts = challenge.get("artifacts")
    if isinstance(artifacts, list):
        for item in artifacts:
            add(item)
    elif isinstance(artifacts, Mapping):
        _collect_artifact_mapping(artifacts, add)

    # 从 delivery_format / delivery_files 提取 deploy_tree
    for key in ("delivery_format", "delivery_files"):
        delivery = challenge.get(key)
        if isinstance(delivery, Mapping):
            deploy_tree = delivery.get("deploy_tree")
            if isinstance(deploy_tree, Mapping):
                _collect_deploy_tree(deploy_tree, add)
            elif isinstance(deploy_tree, str):
                for part in re.split(r"[,;]", deploy_tree):
                    add(part)

    # 追加默认产物列表
    defaults = [*COMMON_ARTIFACTS]
    if category in {"web", "pwn"} or _deployment_mentions_docker(challenge):
        defaults.extend(CONTAINER_ARTIFACTS)
    paths.extend(defaults)

    return _dedupe(paths)


def _collect_artifact_mapping(artifacts: Mapping[str, Any], add) -> None:
    """从 artifact 对象中收集文件路径。"""
    files = artifacts.get("files")
    if isinstance(files, list):
        for item in files:
            add(item, base="deploy/src")

    for key in ("source_code", "static_files", "docker_config"):
        add(artifacts.get(key))

    deploy_tree = artifacts.get("deploy_tree")
    if isinstance(deploy_tree, Mapping):
        _collect_deploy_tree(deploy_tree, add)


def _collect_deploy_tree(deploy_tree: Mapping[str, Any], add) -> None:
    """从 deploy_tree 中收集部署相关的文件路径。"""
    for key, value in deploy_tree.items():
        normalized_key = str(key).strip().strip("/")

        if normalized_key in {"src", "deploy/src"}:
            if isinstance(value, list):
                for item in value:
                    add(item, base="deploy/src")
            else:
                add("app.py", base="deploy/src")
        elif normalized_key in {"_files", "deploy/_files"}:
            if isinstance(value, list):
                for item in value:
                    add(item, base="deploy/_files")
            else:
                add("start.sh", base="deploy/_files")
        elif normalized_key in {"dockerfile", "Dockerfile", "deploy/Dockerfile"}:
            add("deploy/Dockerfile")
        elif normalized_key in {
            "docker_compose",
            "docker-compose.yml",
            "deploy/docker-compose.yml",
        }:
            add("deploy/docker-compose.yml")
        else:
            add(normalized_key)


def _normalize_artifact_path(value: str, *, base: str | None = None) -> str | None:
    """规范化单个 artifact 路径。

    处理:
      - 反斜杠转正斜杠
      - 旧版路径兼容映射（如 writeup/wp.md → writenup/wp.md）
      - 无前缀的路径自动添加基础前缀
    """
    candidate = value.strip().replace("\\", "/")
    if not candidate or _is_absolute_or_url_path(candidate):
        return None

    # 旧版路径兼容映射
    legacy_map = {
        "writeup/wp.md": "writenup/wp.md",
        "solve/solve.py": "writenup/exp.py",
        "solve.py": "writenup/exp.py",
        "exp.py": "writenup/exp.py",
        "wp.md": "writenup/wp.md",
    }
    if candidate in legacy_map:
        return legacy_map[candidate]

    if candidate.endswith("/"):
        return None

    # 如果路径没有已知前缀且提供了基础路径
    if base and not any(
        candidate.startswith(prefix) for prefix in (*KNOWN_ARTIFACT_PREFIXES, "README.md")
    ):
        candidate = f"{base.rstrip('/')}/{candidate.lstrip('/')}"

    if _is_artifact_path_like(candidate):
        return candidate
    return None


# ========== Hint 规范化 ==========

def _normalize_hints(value: Any, challenge: Mapping[str, Any]) -> list[str]:
    """将各种格式的 hint 定义规范化为 3 条提示字符串。

    支持格式:
      - 字符串 → 直接作为提示
      - 包含 content/hint/text 键的字典 → 提取文本
      - 嵌套列表 → 递归提取
      - 阶段性字典（stage_1/stage_2/stage_3 等键）

    如果不足 3 条，自动生成默认提示补齐。
    """
    hints: list[str] = []

    def add(item: Any) -> None:
        """递归添加提示文本。"""
        if isinstance(item, str):
            text = item.strip()
            if text:
                hints.append(text)
        elif isinstance(item, Mapping):
            content = item.get("content") or item.get("hint") or item.get("text")
            if isinstance(content, str):
                add(content)
        elif isinstance(item, list):
            for nested in item:
                add(nested)

    if isinstance(value, list):
        for item in value:
            add(item)
    elif isinstance(value, Mapping):
        # 按阶段性键的优选顺序提取
        preferred_keys = (
            "stage_1", "stage1", "first", "early", "gentle",
            "stage_2", "stage2", "second", "middle", "medium",
            "stage_3", "stage3", "third", "final", "reveal",
        )
        for key in preferred_keys:
            if key in value:
                add(value[key])
        # 再提取非阶段性键
        for key, item in value.items():
            if key not in preferred_keys:
                add(item)

    # 不足 3 条的用默认提示补齐
    defaults = _default_hints(challenge)
    while len(hints) < 3:
        hints.append(defaults[len(hints)])

    return hints[:3]


def _default_hints(challenge: Mapping[str, Any]) -> list[str]:
    """为缺少提示的题目生成默认的渐进式提示。"""
    technique = str(challenge.get("primary_technique") or "the core vulnerability").strip()
    objective = str(challenge.get("learning_objective") or "the intended solve path").strip()
    flag_location = str(challenge.get("flag_location") or "the configured flag location").strip()
    return [
        f"Start from the behavior tied to {objective}.",
        f"Focus on {technique} rather than unrelated surface area.",
        f"Use the intended primitive to reach {flag_location}.",
    ]


# ========== 辅助函数 ==========

def _deployment_mentions_docker(challenge: Mapping[str, Any]) -> bool:
    """判断题目的部署方式是否涉及 Docker。"""
    deployment = challenge.get("deployment")
    return isinstance(deployment, str) and "docker" in deployment.lower()


def _dedupe(values: list[str]) -> list[str]:
    """去重但保持顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _require_artifacts(
    artifacts: list[str],
    required: tuple[str, ...],
    label: str,
) -> None:
    """检查 artifacts 列表是否包含所有必须的文件。"""
    missing = [path for path in required if path not in artifacts]
    if missing:
        raise ChallengeDesignValidationError(
            f"{label} must include: {', '.join(missing)}"
        )


def _reject_implementation_payload(challenge: Mapping[str, Any]) -> None:
    """检查并拒绝包含实现级字段的设计输出。

    设计阶段不应包含具体代码，AI 有时会提前生成代码并混入设计输出。
    """
    present = sorted(key for key in FORBIDDEN_IMPLEMENTATION_KEYS if key in challenge)
    if present:
        raise ChallengeDesignValidationError(
            "design output includes implementation-level fields: "
            + ", ".join(present)
        )


def _validate_implementation_plan(plan: Any) -> None:
    """校验 implementation_plan 的合法性。

    规则:
      - 整体大小不超过 MAX_IMPLEMENTATION_PLAN_CHARS 字符
      - 单个字符串不超过 MAX_PLAN_STRING_CHARS 字符
      - 不能包含代码标记（如 ```, import requests 等）
      - 不能包含实现级字段名
      - 递归检查嵌套的 dict 和 list
    """
    if plan is None:
        return
    if not isinstance(plan, Mapping):
        raise ChallengeDesignValidationError("implementation_plan must be an object")

    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    if len(encoded) > MAX_IMPLEMENTATION_PLAN_CHARS:
        raise ChallengeDesignValidationError(
            "implementation_plan is too large; keep it intent-level"
        )

    _validate_plan_value(plan, path="implementation_plan")


def _validate_plan_value(value: Any, *, path: str) -> None:
    """递归校验 implementation_plan 中的值。

    参数:
        value: 当前要校验的值
        path: 当前路径（用于错误消息中的定位）
    """
    if isinstance(value, str):
        # 字符串长度限制
        if len(value) > MAX_PLAN_STRING_CHARS:
            raise ChallengeDesignValidationError(
                f"{path} contains a string longer than {MAX_PLAN_STRING_CHARS} characters"
            )
        # 不能包含代码标记
        if any(marker in value for marker in PLAN_CODE_MARKERS):
            raise ChallengeDesignValidationError(
                "implementation_plan must be intent-level, not file contents"
            )
        return

    if isinstance(value, Mapping):
        for key, item in value.items():
            # 不能是禁止的实现级字段
            if str(key) in FORBIDDEN_IMPLEMENTATION_KEYS:
                raise ChallengeDesignValidationError(
                    "implementation_plan contains implementation-level field: "
                    f"{key}"
                )
            _validate_plan_value(item, path=f"{path}.{key}")
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_plan_value(item, path=f"{path}[{index}]")


# ========== JSON 解析辅助 ==========

def _strip_json_fences(text: str) -> str:
    """去掉 markdown 代码块标记（```json ... ```）。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().lower() in {"```json", "```"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _find_balanced_json_object_end(text: str, start: int) -> int | None:
    """从 start 位置开始找配对的大括号。

    处理字符串、转义和嵌套括号，返回配对 `}` 的索引。
    如果找不到配对，返回 None。
    """
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True      # 下一个字符被转义
            elif char == '"':
                in_string = False  # 字符串结束
            continue
        if char == '"':
            in_string = True       # 进入字符串
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index       # 找到配对
    return None


# ========== 小型辅助函数 ==========

def _require_non_empty_string(challenge: Mapping[str, Any], field: str) -> None:
    """检查字段是否是非空字符串。"""
    value = challenge.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ChallengeDesignValidationError(f"{field} must be a non-empty string")


def _require_parent_equal(challenge: Mapping[str, Any], field: str, expected: Any) -> None:
    """检查字段值是否与父任务的值一致。"""
    if challenge.get(field) != expected:
        raise ChallengeDesignValidationError(f"{field} must equal parent design task value")


def _is_absolute_or_url_path(value: str) -> bool:
    """判断是否为绝对路径或 URL。"""
    stripped = value.strip()
    return (
        bool(URL_RE.search(stripped))
        or stripped.startswith("/")
        or stripped.startswith("\\")
        or bool(re.match(r"^[A-Za-z]:[\\/]", stripped))  # Windows 绝对路径（C:\）
    )


def _contains_external_http_url(value: str) -> bool:
    """检查文本中是否包含外部 HTTP URL（排除本地回环地址）。

    用于 validation 字段的检查，确保校验计划不依赖外部服务。
    """
    for match in HTTP_URL_RE.finditer(value):
        raw_url = match.group(0).rstrip(".,;:")
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            return True
        host = (parsed.hostname or "").lower()
        # 本地地址 + 127.x.x.x 网段除外
        if host not in LOCAL_HTTP_HOSTS and not host.startswith("127."):
            return True
    return False


def _is_artifact_path_like(value: str) -> bool:
    """判断字符串是否像一个合法的本地文件路径。

    合法的路径:
      - 不包含换行符/制表符
      - 在已知产物列表中
      - 以已知前缀开头且有文件扩展名
      - 是 README.md / metadata.json / validate.sh 之一
    """
    stripped = value.strip().replace("\\", "/")
    if not stripped or _is_absolute_or_url_path(stripped):
        return False
    if any(char in stripped for char in "\r\n\t"):
        return False
    if stripped in COMMON_ARTIFACTS or stripped in CONTAINER_ARTIFACTS:
        return True
    if stripped.startswith(KNOWN_ARTIFACT_PREFIXES):
        return bool(PurePosixPath(stripped).suffix)
    return stripped in {"README.md", "metadata.json", "validate.sh"}


def _single_challenge(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """从 payload 中提取唯一的 challenge（确保恰好一个）。"""
    challenges = payload.get("challenges")
    if not isinstance(challenges, list) or len(challenges) != 1:
        raise ChallengeDesignValidationError("challenges must contain exactly one entry")
    challenge = challenges[0]
    if not isinstance(challenge, Mapping):
        raise ChallengeDesignValidationError("challenge entry must be an object")
    return challenge


def _make_summary(challenge: Mapping[str, Any]) -> str:
    """从 challenge 数据生成摘要（标题 - 技术 - 学习目标）。"""
    title = str(challenge.get("title", "")).strip()
    technique = str(challenge.get("primary_technique", "")).strip()
    objective = str(challenge.get("learning_objective", "")).strip()
    parts = [part for part in (title, technique, objective) if part]
    summary = " - ".join(parts) or "Structured challenge design"
    return summary[:MAX_SUMMARY_CHARS]


def _note_if(notes: list[str], condition: bool, note: str) -> None:
    """条件添加质量门备注。"""
    if condition:
        notes.append(note)
