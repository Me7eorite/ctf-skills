"""Challenge artifact and reference-solve validation."""

from __future__ import annotations

import subprocess
import time
from collections import Counter
from pathlib import Path

from core.jsonio import read_json, write_json
from core.paths import ProjectPaths

# Maps the architecture token an author may declare (in metadata.architecture
# or the trailing segment of metadata.target_platform like "linux/arm64") to
# the set of ELF machine labels we will accept for that declaration. The
# canonical label for each declaration — used in the error message — is the
# first item of the matching value set.
ARCH_ACCEPTS: dict[str, tuple[str, ...]] = {
    "amd64": ("x86_64",),
    "x86_64": ("x86_64",),
    "arm64": ("aarch64",),
    "aarch64": ("aarch64",),
    "arm": ("arm",),
    "armv7": ("arm",),
}


def last_nonempty_line(text: str) -> str:
    return next(
        (line.strip() for line in reversed(text.splitlines()) if line.strip()), ""
    )


def is_elf(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def elf_machine(path: Path) -> str:
    """Return a compact ELF machine label for architecture checks."""
    try:
        with path.open("rb") as handle:
            header = handle.read(20)
    except OSError:
        return ""
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return ""
    machine = int.from_bytes(header[18:20], "little")
    return {
        0x03: "x86",
        0x28: "arm",
        0x3E: "x86_64",
        0xB7: "aarch64",
    }.get(machine, f"machine_{machine}")


class ChallengeValidator:
    def __init__(self, paths: ProjectPaths, timeout: int = 120, shell: str = "bash"):
        self.paths = paths
        self.timeout = timeout
        self.shell = shell

    def validate(self, challenge_ids: list[str] | None = None) -> dict:
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

    def validate_one(self, challenge_dir: Path) -> dict:
        metadata_path = challenge_dir / "metadata.json"
        metadata = read_json(metadata_path)
        record = {"id": challenge_dir.name, "path": str(challenge_dir)}
        if not isinstance(metadata, dict):
            return {**record, "status": "invalid_metadata"}

        expected_flag = metadata.get("flag", "")
        record["expected_flag"] = expected_flag
        errors = self.contract_errors(challenge_dir, metadata)
        if errors:
            self._update_metadata(metadata_path, "failed", "; ".join(errors))
            return {**record, "status": "contract_failed", "contract_errors": errors}

        validation_script = challenge_dir / "validate.sh"
        if not validation_script.exists():
            self._update_metadata(metadata_path, "failed", "validate.sh missing")
            return {**record, "status": "missing_validation"}

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

        record.update(
            {
                "elapsed": round(time.monotonic() - started, 2),
                "returncode": process.returncode,
                "stdout_tail": process.stdout[-2000:],
            }
        )
        if process.stderr.strip():
            record["stderr_tail"] = process.stderr[-500:]
        if process.returncode != 0:
            self._update_metadata(
                metadata_path, "failed", f"validation exited {process.returncode}"
            )
            return {**record, "status": "nonzero_exit"}

        printed_flag = last_nonempty_line(process.stdout)
        record["printed_flag"] = printed_flag
        if expected_flag and printed_flag == expected_flag:
            self._update_metadata(metadata_path, "passed")
            return {**record, "status": "passed"}

        self._update_metadata(metadata_path, "failed", "flag did not match metadata")
        return {**record, "status": "flag_mismatch"}

    def contract_errors(self, challenge_dir: Path, metadata: dict) -> list[str]:
        errors = [
            f"metadata.{field} is missing"
            for field in ("id", "title", "difficulty", "build_status", "flag")
            if not metadata.get(field)
        ]
        if metadata.get("build_status") != "passed":
            errors.append("metadata.build_status is not passed")

        category = metadata.get("category")
        if category == "web":
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
            if not metadata.get("runtime") or not metadata.get("framework"):
                errors.append("Web metadata must record runtime and framework")

        if category in {"re", "pwn"} and metadata.get("target_format", "elf") == "elf":
            roots = [challenge_dir / "dist"]
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
                errors.append("no compiled ELF artifact found")
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
        metadata = read_json(path, {})
        metadata["solve_status"] = status
        if note:
            metadata["solve_note"] = note
        write_json(path, metadata)
