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
from collections import Counter
from pathlib import Path
from typing import Any

from core.clock import beijing_now_isoformat
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

# Solver/validator anti-cheat: organizer files the reference exploit must never
# read to obtain the flag — it must recover it from the live target (web/pwn) or
# the distributed artifact (re), not from these config/answer files.
_FORBIDDEN_EXPLOIT_SOURCES = ("metadata.json", "challenge.yml", "docker-compose")
_FORBIDDEN_DOCKER_CLEANUP_RE = re.compile(
    r"(?:^|[;&|]\s*)"
    r"(?:docker\s+volume\s+(?:rm|prune)\b|"
    r"docker\s+(?:system|container|image|network)\s+prune\b|"
    r"docker\s+compose\s+down\b[^\n;&|]*\s(?:-v|--volumes)\b|"
    r"docker-compose\s+down\b[^\n;&|]*\s(?:-v|--volumes)\b)",
    re.MULTILINE,
)
_ROOT_START_INSTALL_RE = re.compile(
    r"(?im)^\s*(?:COPY|ADD)\s+(?:--[^\r\n]+\s+)*[^\r\n#]*start\.sh\s+/root/start\.sh\b"
)
_HOST_CHROOT_SETUP_RE = re.compile(
    r"(?:\bcp\s+-R\s+/(?:lib\*|usr/lib\*)\s+/home/ctf\b|"
    r"\bmknod\s+/home/ctf/dev/(?:null|zero|random|urandom)\b|"
    r"\bmkdir\s+(?:-[^\r\n;&|]+\s+)?/home/ctf/(?:dev|bin)\b|"
    r"\bcp\s+/bin/(?:sh|ls|cat)\s+/home/ctf/bin\b|"
    r"\bchmod\s+666\s+/home/ctf/dev/\*)"
)
_HOST_CHROOT_SETUP_TEXT_FIELDS = (
    "build_command",
    "validation",
    "run_command",
)


def validation_failure_detail(
    *,
    phase: str,
    code: str,
    message: str,
    status: str | None = None,
    path: str | None = None,
    hint: str | None = None,
) -> dict[str, str]:
    """Return a stable machine-readable validation diagnostic."""
    detail = {
        "phase": phase,
        "code": code,
        "message": message,
    }
    if status:
        detail["status"] = status
    if path:
        detail["path"] = path
    if hint:
        detail["hint"] = hint
    return detail


def classify_validation_failure(
    *,
    status: str,
    error: str | None = None,
    stderr: str | None = None,
    contract_errors: list[str] | None = None,
) -> list[dict[str, str]]:
    """Classify legacy validation strings into stable repair-friendly codes."""
    messages = contract_errors or ([error] if error else [])
    if status == "nonzero_exit":
        text = stderr or error or "validate.sh exited non-zero"
        if "ModuleNotFoundError" in text or "No module named" in text:
            code = "missing_dependency"
            hint = "Make the solver offline-capable with the standard library or vendored helpers."
        else:
            code = "nonzero_exit"
            hint = "Inspect validate.sh stderr/stdout and repair the failing command."
        return [
            validation_failure_detail(
                phase="validate",
                code=code,
                status=status,
                message=text.strip(),
                path="validate.sh",
                hint=hint,
            )
        ]
    if status == "flag_mismatch":
        return [
            validation_failure_detail(
                phase="validate",
                code="flag_mismatch",
                status=status,
                message=error or "validate.sh did not print metadata.flag",
                path="validate.sh",
                hint="Make the final stdout flag token match metadata.flag.",
            )
        ]
    if status in {"missing_validation", "timeout", "no_shell", "invalid_metadata"}:
        return [
            validation_failure_detail(
                phase="validate",
                code=status,
                status=status,
                message=error or status,
            )
        ]
    if messages:
        return [
            _classify_contract_error(message, status=status)
            for message in messages
            if message
        ]
    return [
        validation_failure_detail(
            phase="validate",
            code=status or "failed",
            status=status,
            message=error or status or "validation failed",
        )
    ]


def _classify_contract_error(message: str, *, status: str = "contract_failed") -> dict[str, str]:
    phase = "contract"
    code = "contract_failed"
    path: str | None = None
    hint: str | None = None
    if "nested generated output" in message:
        phase = "gate"
        code = "nested_output"
        path = "output/challenges"
        hint = "Move required files to the canonical challenge root and remove nested output trees."
    elif "metadata." in message and "missing" in message:
        code = "missing_metadata_field"
        field = message.split("metadata.", 1)[1].split()[0]
        path = "metadata.json"
        hint = f"Populate metadata.{field} from the actual built artifact or service."
    elif "metadata.build_status is not passed" in message:
        code = "build_status_not_passed"
        path = "metadata.json"
        hint = "Only mark build_status passed after a real artifact or image exists."
    elif "writenup/wp.md missing" in message:
        phase = "gate"
        code = "missing_document"
        path = "writenup/wp.md"
        hint = "Add a reproducible writeup with enough structure for document evidence."
    elif "README.md missing" in message:
        phase = "gate"
        code = "missing_document"
        path = "README.md"
        hint = "Add an organizer README with build, run, and solve evidence."
    elif "challenge.yml missing" in message:
        phase = "gate"
        code = "missing_delivery_file"
        path = "challenge.yml"
        hint = "Generate challenge.yml from metadata so the delivery bundle has platform metadata."
    elif "writenup/exp.py missing" in message:
        phase = "gate"
        code = "missing_solver"
        path = "writenup/exp.py"
        hint = "Add the reference solver under writenup/exp.py."
    elif "src/ missing" in message or "src/ is empty" in message:
        phase = "gate"
        code = "missing_source"
        path = "src/"
        hint = "Provide non-empty source evidence, or promote deploy/src into src/."
    elif "too small" in message or "fewer than 2 level-2 headings" in message:
        phase = "gate"
        code = "document_too_thin"
        path = "README.md" if message.startswith("README.md") else "writenup/wp.md"
        hint = "Add build, solve, and verification evidence with at least two level-2 sections."
    elif "no compiled ELF artifact" in message:
        code = "missing_artifact"
        path = "attachments/"
        hint = "Copy the compiled ELF into attachments/ and update artifact_sha256."
    elif "no compiled PE/EXE artifact" in message:
        code = "missing_artifact"
        path = "attachments/"
        hint = "Copy the compiled PE/EXE into attachments/ and update artifact_sha256."
    elif "artifact file" in message and "missing under attachments" in message:
        phase = "gate"
        code = "missing_artifact"
        path = "attachments/"
        hint = "Ensure metadata.artifact points to an existing file under attachments/."
    elif "artifact_sha256 does not match" in message:
        phase = "gate"
        code = "artifact_hash_mismatch"
        path = "metadata.json"
        hint = "Recompute metadata.artifact_sha256 from the exact published artifact."
    elif "metadata.artifact" in message and "executable" in message:
        phase = "gate"
        code = "artifact_type_mismatch"
        path = "metadata.json"
        hint = "Point metadata.artifact at the primary executable under attachments/."
    elif "metadata.artifact" in message and "missing" in message:
        phase = "gate"
        code = "artifact_missing"
        path = "metadata.json"
        hint = "Point metadata.artifact at an existing file under attachments/."
    elif "generation output is empty" in message:
        phase = "gate"
        code = "generation_empty_output"
        path = None
        hint = "Regenerate the challenge or rebuild it from the shard specification."
    elif "references 'metadata.json'" in message or "references 'challenge.yml'" in message:
        code = "forbidden_solver_source"
        path = "validate.sh" if message.startswith("validate.sh") else "writenup/exp.py"
        hint = "Derive the flag from the target or distributed artifact, not organizer files."
    elif "embeds the literal metadata.flag" in message:
        code = "hardcoded_flag"
        path = "validate.sh" if message.startswith("validate.sh") else "writenup/exp.py"
        hint = "Remove the literal flag and recover it at runtime."
    elif "does not reference the distributed artifact" in message:
        code = "solver_not_artifact_bound"
        path = "writenup/exp.py"
        hint = "Bind the RE solver to attachments/<artifact>."
    elif "destructive Docker cleanup" in message:
        code = "destructive_cleanup"
        path = "validate.sh"
        hint = "Limit cleanup to this challenge's own container/service."
    elif "plaintext flag" in message or "recoverable in plaintext" in message:
        code = "plaintext_flag_exposure"
        path = "attachments/"
        hint = "Encode or encrypt flag material in the delivered artifact."
    return validation_failure_detail(
        phase=phase,
        code=code,
        status=status,
        message=message,
        path=path,
        hint=hint,
    )


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _dockerfile_installs_root_start(dockerfile: Path) -> bool:
    text = _read_text(dockerfile)
    if not text:
        return False
    return bool(_ROOT_START_INSTALL_RE.search(text))


def _host_chroot_setup_errors(challenge_dir: Path, metadata: dict) -> list[str]:
    """Reject Dockerfile-only chroot setup commands in host/runtime scripts."""

    errors: list[str] = []
    scanned_paths = (
        challenge_dir / "validate.sh",
        challenge_dir / "deploy" / "_files" / "start.sh",
        challenge_dir / "deploy" / "_files" / "ctf.xinetd",
        challenge_dir / "deploy" / "_files" / "etc" / "xinetd.d" / "ctf",
        challenge_dir / "deploy" / "_files" / "etc" / "xinetd.d" / "chal",
    )
    for path in scanned_paths:
        text = _read_text(path)
        if text and _HOST_CHROOT_SETUP_RE.search(text):
            rel = path.relative_to(challenge_dir).as_posix()
            errors.append(
                f"{rel} contains Dockerfile-only /home/ctf chroot setup commands; "
                "copying /lib*, creating /home/ctf/dev nodes, and copying /bin helpers "
                "must only appear as RUN steps inside deploy/Dockerfile, never in "
                "host validation or runtime scripts"
            )

    for field in _HOST_CHROOT_SETUP_TEXT_FIELDS:
        value = metadata.get(field)
        if isinstance(value, str) and _HOST_CHROOT_SETUP_RE.search(value):
            errors.append(
                f"metadata.{field} contains Dockerfile-only /home/ctf chroot setup "
                "commands; record `docker build -t <image> deploy` as the host build "
                "command and keep chroot filesystem initialization inside deploy/Dockerfile"
            )
    return errors


# When the intended technique IS recovering the flag via strings/static reading,
# a plaintext flag in the artifact is by design; otherwise it is a defect.
_STRINGS_TECHNIQUE_HINTS = ("strings", "字符串")


def _strings_is_intended(metadata: dict) -> bool:
    haystack = " ".join(
        str(metadata.get(key, ""))
        for key in ("primary_technique", "learning_objective")
    ).lower()
    return any(hint in haystack for hint in _STRINGS_TECHNIQUE_HINTS)


def _file_contains_bytes(path: Path, needle: str) -> bool:
    """Whether ``needle`` appears as a contiguous byte run in the file.

    The flag is a printable run, so a contiguous byte-substring hit is exactly
    what an ordinary ``strings | grep`` would surface — without shelling out.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return needle.encode("utf-8", "ignore") in data


def _delivered_artifact_roots(challenge_dir: Path, category: str | None) -> list[Path]:
    roots = [challenge_dir / "attachments", challenge_dir / "dist"]
    if category == "pwn":
        roots.append(challenge_dir / "deploy")
    return roots


def _artifact_elfs(challenge_dir: Path, category: str | None) -> list[Path]:
    """Locate delivered ELF artifacts (mirrors the contract-check roots)."""
    return [
        path
        for root in _delivered_artifact_roots(challenge_dir, category)
        if root.exists()
        for path in root.rglob("*")
        if is_elf(path)
    ]


def _artifact_pes(challenge_dir: Path, category: str | None) -> list[Path]:
    """Locate delivered PE/COFF artifacts for Windows RE challenges."""
    return [
        path
        for root in _delivered_artifact_roots(challenge_dir, category)
        if root.exists()
        for path in root.rglob("*")
        if is_pe(path)
    ]


def _delivered_artifacts(challenge_dir: Path, category: str | None) -> list[Path]:
    """Locate delivered binary artifacts checked for shortcuts."""
    return [
        path
        for root in _delivered_artifact_roots(challenge_dir, category)
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and (is_elf(path) or is_pe(path))
    ]


def _bare_run_reveals_flag(artifact: Path, flag: str, timeout: int) -> bool:
    """Run the artifact with no input; True if the flag appears in its output.

    A genuine medium+ challenge must NOT print the flag on a plain ``./chal``.
    The artifact is already trusted enough to run under ``validate.sh``; here we
    run it directly with empty stdin, no args, and a short timeout. Any failure
    to execute is treated as "did not reveal" (the normal solve path governs).
    """
    if not flag:
        return False
    try:
        import os

        os.chmod(artifact, 0o755)
    except OSError:
        pass
    try:
        data = artifact.read_bytes()
        if data.startswith(b"#!"):
            return flag in data.decode("utf-8", "ignore")
        command = [str(artifact)]
        proc = subprocess.run(
            command,
            cwd=artifact.parent,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(timeout, 20),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return flag in (proc.stdout or "") or flag in (proc.stderr or "")


def _plaintext_flag_locations(
    challenge_dir: Path, flag: str, category: str | None = None
) -> list[str]:
    """Return delivered files where the flag appears in plaintext.

    Scans player-delivered artifacts. Local source trees may carry plaintext
    organizer material during implementation, but they must not be shipped as
    player attachments.
    """
    if not flag:
        return []
    hits: list[str] = []
    scan_roots = _delivered_artifact_roots(challenge_dir, category)
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and _file_contains_bytes(path, flag):
                hits.append(path.relative_to(challenge_dir).as_posix())
    return sorted(set(hits))



def _is_empty_generation_output(challenge_dir: Path) -> bool:
    if not challenge_dir.exists():
        return False
    meaningful_files = [path for path in challenge_dir.rglob("*") if path.is_file()]
    if meaningful_files:
        return False
    return any(path.is_dir() for path in challenge_dir.rglob("*")) or challenge_dir.is_dir()


def _delivery_contract_errors(challenge_dir: Path) -> list[str]:
    errors: list[str] = []
    for relative in ("challenge.yml", "README.md", "writenup/wp.md", "writenup/exp.py"):
        if not (challenge_dir / relative).is_file():
            errors.append(f"{relative} missing")
    source_root = challenge_dir / "src"
    if not source_root.is_dir():
        errors.append("src/ missing")
    elif not any(path.is_file() and path.stat().st_size > 0 for path in source_root.rglob("*")):
        errors.append("src/ is empty")
    for relative in ("README.md", "writenup/wp.md"):
        reason = _document_quality_error(challenge_dir / relative, relative)
        if reason:
            errors.append(reason)
    for nested in ("output/challenges", "deploy/output/challenges", "attachments/output/challenges"):
        if (challenge_dir / nested).is_dir():
            errors.append(f"nested generated output tree remains at {nested}")
    return errors


def _document_quality_error(path: Path, relative: str) -> str | None:
    text = _read_text(path)
    if text is None:
        return None
    if len(text.strip()) <= 300:
        return f"{relative} too small"
    if text.count("##") < 2:
        return f"{relative} has fewer than 2 level-2 headings"
    return None


def _extend_artifact_metadata_errors(
    errors: list[str],
    challenge_dir: Path,
    metadata: dict,
    executable_paths: list[Path],
) -> None:
    if metadata.get("category") != "re":
        return
    artifact = metadata.get("artifact")
    if artifact is None:
        return
    if not isinstance(artifact, str) or not artifact.startswith("attachments/"):
        errors.append("metadata.artifact missing or not under attachments/")
        return
    artifact_path = challenge_dir / artifact
    if not artifact_path.is_file():
        errors.append(f"metadata.artifact missing under attachments/: {artifact}")
        return
    if executable_paths and not any(path.resolve() == artifact_path.resolve() for path in executable_paths):
        errors.append("metadata.artifact does not point to the primary executable artifact")
    expected_sha = metadata.get("artifact_sha256")
    if not isinstance(expected_sha, str) or not expected_sha:
        errors.append("metadata.artifact_sha256 missing")
        return
    try:
        import hashlib

        actual_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    except OSError:
        return
    if expected_sha != actual_sha:
        errors.append("metadata.artifact_sha256 does not match artifact contents")


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


def is_pe(path: Path) -> bool:
    """判断文件是否为 PE/COFF 格式（Windows .exe/.dll）。"""
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            mz_header = handle.read(0x40)
            if len(mz_header) < 0x40 or mz_header[:2] != b"MZ":
                return False
            pe_offset = int.from_bytes(mz_header[0x3C:0x40], "little")
            if pe_offset <= 0:
                return False
            handle.seek(pe_offset)
            return handle.read(4) == b"PE\x00\x00"
    except OSError:
        return False


def pe_machine(path: Path) -> str:
    """解析 PE/COFF 文件的 machine 架构标签。"""
    try:
        with path.open("rb") as handle:
            mz_header = handle.read(0x40)
            pe_offset = int.from_bytes(mz_header[0x3C:0x40], "little")
            handle.seek(pe_offset + 4)
            machine = int.from_bytes(handle.read(2), "little")
    except OSError:
        return "unknown"
    return {
        0x014C: "x86",
        0x8664: "x86_64",
        0xAA64: "aarch64",
        0x01C0: "arm",
        0x01C4: "armv7",
    }.get(machine, "unknown")


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
            "generated_at": beijing_now_isoformat(),
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

    def validate_path(
        self,
        challenge_dir: Path,
        *,
        expected_challenge_id: str,
    ) -> dict:
        """Validate one execution-bound directory without global ID lookup."""
        if challenge_dir.is_symlink() or not challenge_dir.is_dir():
            return {
                "challenge_id": expected_challenge_id,
                "status": "missing_challenge",
                "error": f"challenge directory does not exist: {challenge_dir}",
            }
        metadata = read_json(challenge_dir / "metadata.json")
        if not isinstance(metadata, dict):
            return {
                "challenge_id": expected_challenge_id,
                "status": "invalid_metadata",
            }
        if metadata.get("id") != expected_challenge_id:
            return {
                "challenge_id": expected_challenge_id,
                "status": "identity_mismatch",
                "error": (
                    f"metadata.id={metadata.get('id')!r}, "
                    f"expected={expected_challenge_id!r}"
                ),
            }
        result = self.validate_one(challenge_dir, persist_result=False)
        result["challenge_id"] = expected_challenge_id
        return result

    def validate_one(
        self,
        challenge_dir: Path,
        *,
        persist_result: bool = True,
    ) -> dict:
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
            if _is_empty_generation_output(challenge_dir):
                return {
                    **record,
                    "status": "generation_empty_output",
                    "error": "generation output is empty",
                    "failure_details": classify_validation_failure(
                        status="generation_empty_output",
                        contract_errors=["generation output is empty"],
                    ),
                }
            return {**record, "status": "invalid_metadata"}

        expected_flag = metadata.get("flag", "")
        record["expected_flag"] = expected_flag

        def record_status(status: str, note: str | None = None) -> None:
            if persist_result:
                self._update_metadata(metadata_path, status, note)

        # 第一步：合约检查
        errors = self.contract_errors(challenge_dir, metadata)
        if errors:
            record_status("failed", "; ".join(errors))
            return {
                **record,
                "status": "contract_failed",
                "contract_errors": errors,
                "failure_details": classify_validation_failure(
                    status="contract_failed",
                    contract_errors=errors,
                ),
            }

        # 第二步：检查 validate.sh 是否存在
        validation_script = challenge_dir / "validate.sh"
        if not validation_script.exists():
            record_status("failed", "validate.sh missing")
            return {
                **record,
                "status": "missing_validation",
                "failure_details": classify_validation_failure(
                    status="missing_validation",
                    error="validate.sh missing",
                ),
            }

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
            record_status("failed", "validation timed out")
            return {
                **record,
                "status": "timeout",
                "failure_details": classify_validation_failure(
                    status="timeout",
                    error="validation timed out",
                ),
            }
        except FileNotFoundError as exc:
            record_status("failed", "validation shell not found")
            return {
                **record,
                "status": "no_shell",
                "error": str(exc),
                "failure_details": classify_validation_failure(
                    status="no_shell",
                    error=str(exc),
                ),
            }

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
            record_status("failed", f"validation exited {process.returncode}")
            return {
                **record,
                "status": "nonzero_exit",
                "failure_details": classify_validation_failure(
                    status="nonzero_exit",
                    stderr=record.get("stderr_tail"),
                    error="validate.sh exited non-zero",
                ),
            }

        # 比对 flag
        matches = FLAG_TOKEN_RE.findall(process.stdout)
        printed_flag = matches[-1] if matches else ""
        record["printed_flag"] = printed_flag
        if expected_flag and printed_flag == expected_flag:
            # 预期路径必要性：即使官方解能跑通，如果不经过预期路径也能拿到 flag
            # （裸跑产物直接吐 flag / flag 明文出现在产物或源码里），这题实际是
            # easy 甚至无效，必须判失败而不是 passed。
            necessity = self._intended_path_unnecessary(
                challenge_dir, metadata, expected_flag
            )
            if necessity:
                record_status("failed", necessity)
                return {
                    **record,
                    "status": "unnecessary_intended_path",
                    "error": necessity,
                    "necessity_note": necessity,
                    "contract_errors": [necessity],
                    "failure_details": classify_validation_failure(
                        status="unnecessary_intended_path",
                        error=necessity,
                        contract_errors=[necessity],
                    ),
                }
            record_status("passed")
            return {**record, "status": "passed"}

        record_status("failed", "flag did not match metadata")
        return {
            **record,
            "status": "flag_mismatch",
            "failure_details": classify_validation_failure(
                status="flag_mismatch",
                error="flag did not match metadata",
            ),
        }

    def _intended_path_unnecessary(
        self, challenge_dir: Path, metadata: dict, expected_flag: str
    ) -> str | None:
        """Return a reason string if the flag is reachable WITHOUT the intended path."""
        category = metadata.get("category")
        if _strings_is_intended(metadata):
            return None

        plaintext_hits = _plaintext_flag_locations(
            challenge_dir, expected_flag, category
        )
        if plaintext_hits:
            return (
                "flag is recoverable in plaintext without the intended technique "
                f"({', '.join(plaintext_hits)})"
            )

        if category in {"re", "pwn"}:
            for artifact in _delivered_artifacts(challenge_dir, category):
                if _bare_run_reveals_flag(artifact, expected_flag, self.timeout):
                    rel = artifact.relative_to(challenge_dir).as_posix()
                    return (
                        f"running {rel} with no input prints the flag; the "
                        "intended path is not required"
                    )
        return None

    def contract_errors(self, challenge_dir: Path, metadata: dict) -> list[str]:
        """Run deterministic metadata, artifact, and solver contract checks."""
        errors = [
            f"metadata.{field} is missing"
            for field in ("id", "title", "difficulty", "build_status", "flag")
            if not metadata.get(field)
        ]
        if metadata.get("build_status") != "passed":
            errors.append("metadata.build_status is not passed")

        category = metadata.get("category")
        if category in {"web", "pwn"}:
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
            start_path = challenge_dir / "deploy" / "_files" / "start.sh"
            if not start_path.is_file():
                errors.append("missing deploy/_files/start.sh")
            dockerfile_path = challenge_dir / "deploy" / "Dockerfile"
            if dockerfile_path.is_file() and not _dockerfile_installs_root_start(
                dockerfile_path
            ):
                errors.append(
                    "deploy/Dockerfile must copy deploy/_files/start.sh to /root/start.sh"
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
            if category == "pwn":
                errors.extend(_host_chroot_setup_errors(challenge_dir, metadata))

        target_format = metadata.get("target_format", "elf")
        if category in {"re", "pwn"} and target_format == "elf":
            elf_paths = _artifact_elfs(challenge_dir, category)
            if not elf_paths:
                errors.append("no compiled ELF artifact found in attachments/")

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

            _extend_artifact_metadata_errors(errors, challenge_dir, metadata, elf_paths)

            flag = metadata.get("flag") or ""
            if category == "re" and flag and not _strings_is_intended(metadata):
                exposed = [
                    path.relative_to(challenge_dir).as_posix()
                    for path in elf_paths
                    if _file_contains_bytes(path, flag)
                ]
                if exposed:
                    errors.append(
                        "delivered artifact exposes the plaintext flag via strings "
                        f"({', '.join(exposed)}); embed it so the solver must "
                        "recover it, or declare strings as the intended technique"
                    )

        if category == "re" and target_format == "exe":
            pe_paths = _artifact_pes(challenge_dir, category)
            if not pe_paths:
                errors.append("no compiled PE/EXE artifact found in attachments/")

            expected_architecture = (
                metadata.get("architecture")
                or metadata.get("target_platform", "").rsplit("/", 1)[-1]
            )
            accepted = ARCH_ACCEPTS.get(expected_architecture)
            if accepted:
                canonical = accepted[0]
                wrong_arch = [
                    path.relative_to(challenge_dir).as_posix()
                    for path in pe_paths
                    if pe_machine(path) not in accepted
                ]
                if wrong_arch:
                    errors.append(
                        f"PE artifact architecture is not {canonical}: "
                        + ", ".join(wrong_arch)
                    )

            _extend_artifact_metadata_errors(errors, challenge_dir, metadata, pe_paths)

            flag = metadata.get("flag") or ""
            if flag and not _strings_is_intended(metadata):
                exposed = [
                    path.relative_to(challenge_dir).as_posix()
                    for path in pe_paths
                    if _file_contains_bytes(path, flag)
                ]
                if exposed:
                    errors.append(
                        "delivered artifact exposes the plaintext flag via strings "
                        f"({', '.join(exposed)}); embed it so the solver must "
                        "recover it, or declare strings as the intended technique"
                    )

        errors.extend(self._solver_integrity_errors(challenge_dir, metadata))
        return errors

    def _solver_integrity_errors(
        self, challenge_dir: Path, metadata: dict
    ) -> list[str]:
        """Deterministic anti-cheat checks on the reference solver/validator.

        The host's flag-match gate alone cannot tell a genuine solve from a
        validator that hardcodes or sideloads the known flag. These checks make
        the cheat deterministic-detectable for every category:

          A. ``validate.sh`` / ``writenup/exp.py`` must NOT embed the literal
             ``metadata.flag`` — a real solver recovers it at runtime.
          B. The exploit must NOT read the flag from organizer config/answer
             files (``metadata.json`` / ``challenge.yml`` / docker-compose);
             web/pwn exploits recover it from the live service, never from the
             compose file that injects it.
          C. A ``re`` solver must actually reference the distributed artifact
             under ``attachments/`` and must not read organizer files.
          D. Docker cleanup must never remove/prune volumes or global resources;
             generated validators may only remove their own named container.
        """
        errors: list[str] = []
        category = metadata.get("category")
        flag = metadata.get("flag") or ""
        validate_text = _read_text(challenge_dir / "validate.sh")
        exp_text = _read_text(challenge_dir / "writenup" / "exp.py")

        # A — hardcoded flag literal in the solver/validator
        if flag:
            for name, text in (
                ("validate.sh", validate_text),
                ("writenup/exp.py", exp_text),
            ):
                if text and flag in text:
                    errors.append(
                        f"{name} embeds the literal metadata.flag; the reference "
                        "solver must recover the flag, not hardcode it"
                    )

        # B - the exploit/validator must not read organizer config/answer files
        for name, text in (("validate.sh", validate_text), ("writenup/exp.py", exp_text)):
            if not text:
                continue
            for token in ("metadata.json", "challenge.yml"):
                if token in text:
                    errors.append(
                        f"{name} references '{token}'; the solver must recover "
                        "the flag from the target or distributed artifact, not organizer files"
                    )
        if exp_text and "docker-compose" in exp_text:
            errors.append(
                "writenup/exp.py references 'docker-compose'; the exploit must "
                "recover the flag from the target, not organizer files"
            )

        # D — destructive Docker cleanup can remove host infrastructure volumes
        if validate_text and _FORBIDDEN_DOCKER_CLEANUP_RE.search(validate_text):
            errors.append(
                "validate.sh contains destructive Docker cleanup; it must not "
                "remove/prune volumes or global Docker resources"
            )

        # C — a re solver must open the distributed artifact, never organizer files
        if category == "re":
            combined = f"{validate_text or ''}\n{exp_text or ''}"
            if combined.strip():
                if "attachments" not in combined:
                    errors.append(
                        "re solver does not reference the distributed artifact "
                        "under attachments/; it must derive the flag from it"
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
