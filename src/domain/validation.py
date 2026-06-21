"""题目产物和参考解题（reference solve）的确定性校验。

本模块提供了一套不依赖 AI 的确定性检查机制，用于验证 AI 生成的题目是否质量合格。
主要包括:
  1. ELF 文件架构识别
  2. metadata 合约检查（contract check）
  3. 参考解题脚本执行验证
"""

from __future__ import annotations

import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from core.jsonio import read_json, write_json
from core.paths import ProjectPaths

# ========== ELF 架构识别 ==========

# 将作者声明的架构标识（metadata.architecture 或 metadata.target_platform 的尾部）
# 映射到可接受的 ELF machine 标签集合。每个映射值的第一个元素是规范名称（用于错误消息）。
ARCH_ACCEPTS: dict[str, tuple[str, ...]] = {
    "amd64": ("x86_64",),       # AMD64 → x86_64
    "x86_64": ("x86_64",),      # x86_64（同义）
    "arm64": ("aarch64",),      # ARM64 → aarch64
    "aarch64": ("aarch64",),    # aarch64（同义）
    "arm": ("arm",),            # ARM（32位）
    "armv7": ("arm",),          # ARMv7 → arm
}


FLAG_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])flag\{[^\r\n{}]+\}(?![A-Za-z0-9_])"
)


def compose_literal_flag(compose_path: Path) -> str | None:
    """Return a literal FLAG value from an environment list entry.

    The factory intentionally requires the unambiguous Compose list form
    ``- FLAG=flag{...}``. Variable interpolation such as ``${FLAG}`` is rejected
    because a generated challenge must carry one deterministic organizer flag.
    """
    try:
        lines = compose_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    environment_indent: int | None = None
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        if environment_indent is None:
            if stripped == "environment:":
                environment_indent = indent
            continue
        if indent <= environment_indent:
            environment_indent = None
            if stripped == "environment:":
                environment_indent = indent
            continue
        if not stripped.startswith("-"):
            continue
        item = stripped[1:].strip()
        if len(item) >= 2 and item[0] == item[-1] and item[0] in {"'", '"'}:
            item = item[1:-1].strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key.strip() != "FLAG":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return None


def is_elf(path: Path) -> bool:
    """判断文件是否为 ELF 格式（通过幻数 \\x7fELF 识别）。"""
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def elf_machine(path: Path) -> str:
    """解析 ELF 文件的机器架构标签。

    读取 ELF header 中的 e_machine 字段（偏移 18，2 字节，小端序），
    映射为人类可读的架构标签。

    支持的架构:
      0x03 → x86（32位）
      0x28 → arm（32位ARM）
      0x3E → x86_64（64位AMD/Intel）
      0xB7 → aarch64（64位ARM）
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(20)
    except OSError:
        return ""
    # 验证 ELF 幻数和 header 长度
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return ""
    machine = int.from_bytes(header[18:20], "little")
    return {
        0x03: "x86",
        0x28: "arm",
        0x3E: "x86_64",
        0xB7: "aarch64",
    }.get(machine, f"machine_{machine}")


# ========== 题目校验器 ==========

class ChallengeValidator:
    """题目的确定性校验（合约检查 + 参考解题执行）。

    属性:
        paths: 项目路径管理实例
        timeout: 参考解题脚本的执行超时秒数（默认 120）
        shell: 执行脚本的 shell（默认 bash）
    """

    def __init__(self, paths: ProjectPaths, timeout: int = 120, shell: str = "bash"):
        self.paths = paths
        self.timeout = timeout
        self.shell = shell

    def validate(self, challenge_ids: list[str] | None = None) -> dict:
        """批量校验题目。

        参数:
            challenge_ids: 要校验的题目 ID 列表。为 None 或空列表时校验所有题目。

        返回:
            校验汇总结果，包含 total、status_counts 和 results 字段，
            并写入 work/reports/validation.json。
        """
        challenge_dirs = self._challenge_dirs(challenge_ids or [])
        results = [self.validate_one(path) for path in challenge_dirs]
        counts = Counter(item["status"] for item in results)
        summary = {
            "total": len(results),
            "status_counts": dict(counts),
            "results": results,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        write_json(self.paths.reports / "validation.json", summary)
        return summary

    def validate_challenge(self, challenge_id: str) -> dict:
        """按 challenge_id 校验单个题目。

        challenge_id 支持前缀匹配，如 "web-0001" 可以匹配 "web-0001-sqli"。
        需要恰好匹配一个目录，否则返回错误状态。
        """
        matches: list[Path] = [
            path
            for path in self.paths.challenges.glob("*/*")
            if path.is_dir()
            and (path.name == challenge_id or path.name.startswith(f"{challenge_id}-"))
        ]
        if not matches:
            return {
                "challenge_id": challenge_id,
                "status": "missing_challenge",
                "error": f"no challenge directory matches {challenge_id}",
            }
        if len(matches) > 1:
            return {
                "challenge_id": challenge_id,
                "status": "ambiguous_challenge",
                "error": (
                    f"{len(matches)} challenge directories match {challenge_id}: "
                    + ", ".join(sorted(match.name for match in matches))
                ),
            }
        result = self.validate_one(matches[0])
        result.setdefault("challenge_id", challenge_id)
        return result

    def validate_one(self, challenge_dir: Path) -> dict:
        """校验单个题目目录。

        校验流程:
          1. 读取并检查 metadata.json
          2. 执行合约检查（contract check）
          3. 执行参考解题脚本（validate.sh）
          4. 比对 flag 输出

        返回的 status 可能值:
          - "invalid_metadata": metadata.json 不是合法 JSON 对象
          - "contract_failed": 合约检查未通过
          - "missing_validation": validate.sh 不存在
          - "timeout": 参考解题脚本超时
          - "no_shell": 指定的 shell 不可用
          - "nonzero_exit": 参考解题脚本返回非零
          - "flag_mismatch": output 中的 flag 与 metadata 中的 flag 不一致
          - "passed": 校验通过
        """
        metadata_path = challenge_dir / "metadata.json"
        metadata = read_json(metadata_path)
        record: dict[str, Any] = {
            "id": challenge_dir.name,
            "path": str(challenge_dir),
        }
        if not isinstance(metadata, dict):
            return {**record, "status": "invalid_metadata"}

        expected_flag = metadata.get("flag", "")
        record["expected_flag"] = expected_flag

        # 第一步：合约检查
        errors = self.contract_errors(challenge_dir, metadata)
        if errors:
            self._update_metadata(metadata_path, "failed", "; ".join(errors))
            return {**record, "status": "contract_failed", "contract_errors": errors}

        # 第二步：检查 validate.sh 是否存在
        validation_script = challenge_dir / "validate.sh"
        if not validation_script.exists():
            self._update_metadata(metadata_path, "failed", "validate.sh missing")
            return {**record, "status": "missing_validation"}

        # 第三步：执行参考解题脚本
        started = time.monotonic()
        try:
            process = subprocess.run(
                [self.shell, str(validation_script)],
                cwd=challenge_dir,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._update_metadata(metadata_path, "failed", "validation timed out")
            return {**record, "status": "timeout"}
        except FileNotFoundError as exc:
            self._update_metadata(metadata_path, "failed", "validation shell not found")
            return {**record, "status": "no_shell", "error": str(exc)}

        # 记录执行结果
        record.update(
            {
                "elapsed": round(time.monotonic() - started, 2),
                "returncode": process.returncode,
                "stdout_tail": process.stdout[-2000:],  # 只保留末尾 2000 字符
            }
        )
        if process.stderr.strip():
            record["stderr_tail"] = process.stderr[-500:]

        # 非零退出 → 失败
        if process.returncode != 0:
            self._update_metadata(
                metadata_path, "failed", f"validation exited {process.returncode}"
            )
            return {**record, "status": "nonzero_exit"}

        # 比对 flag
        matches = FLAG_TOKEN_RE.findall(process.stdout)
        printed_flag = matches[-1] if matches else ""
        record["printed_flag"] = printed_flag
        if expected_flag and printed_flag == expected_flag:
            self._update_metadata(metadata_path, "passed")
            return {**record, "status": "passed"}

        self._update_metadata(metadata_path, "failed", "flag did not match metadata")
        return {**record, "status": "flag_mismatch"}

    def contract_errors(self, challenge_dir: Path, metadata: dict) -> list[str]:
        """执行 metadata 合约检查。

        检查项:
          1. 必需字段是否存在（id, title, difficulty, build_status, flag）
          2. build_status 是否为 "passed"
          3. Web 类别: 必须有 Dockerfile、docker-compose.yml、deploy/src 目录
          4. RE/Pwn 类别: 必须有编译后的 ELF 产物（在 attachments/ 或 legacy dist/
             或 pwn 的 deploy/ 下），架构必须与声明匹配
        """
        errors = [
            f"metadata.{field} is missing"
            for field in ("id", "title", "difficulty", "build_status", "flag")
            if not metadata.get(field)
        ]
        if metadata.get("build_status") != "passed":
            errors.append("metadata.build_status is not passed")

        category = metadata.get("category")
        if category in {"web", "pwn"}:
            # Web/Pwn 类别必须有完整 Docker 部署文件和确定性 flag 注入。
            required = (
                challenge_dir / "deploy" / "Dockerfile",
                challenge_dir / "deploy" / "docker-compose.yml",
                challenge_dir / "deploy" / "src",
            )
            errors.extend(
                f"missing {path.relative_to(challenge_dir).as_posix()}"
                for path in required
                if not path.exists()
            )
            compose_path = challenge_dir / "deploy" / "docker-compose.yml"
            if compose_path.is_file():
                compose_flag = compose_literal_flag(compose_path)
                if compose_flag != metadata.get("flag"):
                    errors.append(
                        "deploy/docker-compose.yml must define environment list entry "
                        "FLAG=<metadata.flag> with a literal matching value"
                    )
            if category == "web" and (
                not metadata.get("runtime") or not metadata.get("framework")
            ):
                errors.append("Web metadata must record runtime and framework")

        if category in {"re", "pwn"} and metadata.get("target_format", "elf") == "elf":
            # RE/Pwn 的 ELF 产物架构检查。
            # 约定：交付目录是 attachments/（玩家下载用），等同于打包出口；
            # dist/ 是历史遗留兼容位置（早期 prompt 写的是 dist/），新题应该用
            # attachments/，旧题已有 dist/ 的继续受支持。pwn 还会扫 deploy/
            # 因为 docker 镜像里有时直接嵌编译产物。
            roots = [
                challenge_dir / "attachments",
                challenge_dir / "dist",  # legacy compatibility
            ]
            if category == "pwn":
                roots.append(challenge_dir / "deploy")
            elf_paths = [
                path
                for root in roots
                if root.exists()
                for path in root.rglob("*")
                if is_elf(path)
            ]
            if not elf_paths:
                errors.append("no compiled ELF artifact found in attachments/ or dist/")

            # 从 metadata 推断期望的架构
            expected_architecture = (
                metadata.get("architecture")
                or metadata.get("target_platform", "").rsplit("/", 1)[-1]
            )
            accepted = ARCH_ACCEPTS.get(expected_architecture)
            if accepted:
                canonical = accepted[0]
                wrong_arch = [
                    path.relative_to(challenge_dir).as_posix()
                    for path in elf_paths
                    if elf_machine(path) not in accepted
                ]
                if wrong_arch:
                    errors.append(
                        f"ELF artifact architecture is not {canonical}: "
                        + ", ".join(wrong_arch)
                    )
        return errors

    def _challenge_dirs(self, challenge_ids: list[str]) -> list[Path]:
        """获取要校验的题目目录列表。

        如果 challenge_ids 为空，返回所有包含 metadata.json 的题目目录。
        否则返回匹配指定 id 前缀的目录。
        """
        directories = sorted(
            path
            for path in self.paths.challenges.glob("*/*")
            if path.is_dir()
        )
        if not challenge_ids:
            return [path for path in directories if (path / "metadata.json").exists()]
        return [
            path
            for path in directories
            if any(path.name.startswith(challenge_id) for challenge_id in challenge_ids)
        ]

    @staticmethod
    def _update_metadata(path: Path, status: str, note: str | None = None) -> None:
        """更新 metadata.json 中的解题状态。

        在 metadata 中写入 solve_status 和可选的 solve_note 字段。
        """
        metadata = read_json(path, {})
        metadata["solve_status"] = status
        if note:
            metadata["solve_note"] = note
        write_json(path, metadata)
