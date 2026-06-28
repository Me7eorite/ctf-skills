"""Render generated challenges into the delivery format v2 bundle."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from core.paths import ProjectPaths
from packing.archive import _enclosure_members, _tree_members, _write_tools_zip, _write_zip
from packing.docker import _is_containerized, _save_docker, _should_emit_enclosure
from packing.errors import PackingError
from packing.layout import _create_layout, _prepare_output, _safe_name
from packing.pdf import _render_pdf
from packing.selector import _selected_challenges
from packing.workbooks import _overview_row, _write_workbook

CATEGORY_PREFIXES = {
    "crypto": "crypto",
    "web": "web",
    "pwn": "pwn",
    "re": "reverse",
    "reverse": "reverse",
    "misc": "misc",
    "stego": "stego",
    "forensics": "forensics",
    "ics": "ics",
    "ai": "ai",
    "cloud": "cloud",
    "mobile": "mobile",
    "blockchain": "blockchain",
    "iot": "iot",
    "auto": "auto",
    "data": "data",
    "malware": "malware",
    "osint": "osint",
}

CATEGORY_LABELS = {
    "crypto": "Crypto",
    "web": "Web",
    "pwn": "Pwn",
    "re": "Reverse",
    "reverse": "Reverse",
    "misc": "Misc",
    "stego": "Stego",
    "forensics": "Forensics",
    "ics": "ICS",
    "ai": "AI",
    "cloud": "Cloud",
    "mobile": "Mobile",
    "blockchain": "Blockchain",
    "iot": "IoT",
    "auto": "Auto",
    "data": "Data",
    "malware": "Malware",
    "osint": "OSINT",
}

ENCLOSURE_RULES = {
    "web": "skip",
    "pwn": "optional",
    "cloud": "optional",
}

OVERVIEW_HEADERS = [
    "题目ID",
    "题目名称",
    "题目描述",
    "题型",
    "难度",
    "考点",
    "分值",
    "flag格式",
    "状态",
]

IMAGE_HEADERS = ["题目名称", "镜像文件", "端口", "基础镜像", "启动命令"]


@dataclass(frozen=True)
class PackerOptions:
    include_pwn_attachments: bool = False
    skip_docker: bool = False
    require_docker: bool = False
    generated_on: date | None = None


class Packer:
    def __init__(self, paths: ProjectPaths, options: PackerOptions | None = None):
        self.paths = paths
        self.options = options or PackerOptions()
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def pack(self, out_dir: Path | None = None) -> dict[str, Any]:
        self.warnings.clear()
        self.errors.clear()
        if self.options.skip_docker and self.options.require_docker:
            raise PackingError("--skip-docker and --require-docker cannot be combined")

        output = (out_dir or self.paths.delivery_bundle).resolve()
        _prepare_output(output)
        directories = _create_layout(output)
        challenges = _selected_challenges(self.paths)
        overview_rows = []
        image_rows = []
        emitted = []

        docker = None if self.options.skip_docker else shutil.which("docker")
        has_containers = any(_is_containerized(metadata) for _, metadata in challenges)
        if has_containers and not self.options.skip_docker and not docker:
            message = "docker CLI unavailable; Docker tar export skipped"
            if self.options.require_docker:
                raise PackingError(message)
            self.warnings.append(message)

        for challenge_dir, metadata in challenges:
            record = self._pack_challenge(
                challenge_dir, metadata, directories, docker
            )
            overview_rows.append(_overview_row(metadata))
            if record["image_row"]:
                image_rows.append(record["image_row"])
            emitted.append(record)

        _write_workbook(
            directories["题库资源"] / "ctf-overview.xlsx",
            OVERVIEW_HEADERS,
            overview_rows,
        )
        _write_workbook(
            directories["虚拟机资源"] / "镜像模板.xlsx",
            IMAGE_HEADERS,
            image_rows,
        )
        return {
            "output": str(output),
            "challenges": len(challenges),
            "emitted": emitted,
            "warnings": self.warnings,
            "errors": self.errors,
        }

    def _pack_challenge(
        self,
        challenge_dir: Path,
        metadata: dict[str, Any],
        directories: dict[str, Path],
        docker: str | None,
    ) -> dict[str, Any]:
        challenge_id = str(metadata.get("id") or challenge_dir.name)
        category = str(metadata.get("category") or challenge_dir.parent.name).lower()
        prefix = CATEGORY_PREFIXES.get(category, category)
        delivery_name = _safe_name(str(metadata.get("delivery_name") or challenge_id))
        stem = f"js-{prefix}-{delivery_name}"

        tools_zip = directories["工具"] / f"{stem}exp.zip"
        _write_tools_zip(challenge_dir, tools_zip)

        pdf_path = directories["report"] / f"{stem}.pdf"
        _render_pdf(challenge_dir / "writenup" / "wp.md", pdf_path, self.warnings)

        deploy_zip = None
        if _is_containerized(metadata):
            _require_valid_port(metadata, challenge_id)
            deploy_zip = directories["deploy"] / f"{stem}.zip"
            deploy_dir = challenge_dir / "deploy"
            if not deploy_dir.is_dir():
                raise PackingError(f"{challenge_id}: missing deploy directory")
            _write_zip(deploy_zip, _tree_members(deploy_dir, Path("deploy")))

        enclosure_zip = None
        if _should_emit_enclosure(category, self.options.include_pwn_attachments):
            members = list(_enclosure_members(challenge_dir))
            if not members:
                raise PackingError(f"{challenge_id}: required enclosure is empty")
            enclosure_zip = directories["enclosure"] / f"{stem}.zip"
            _write_zip(enclosure_zip, members)

        tar_path = None
        image_row = None
        if deploy_zip and docker:
            tar_path, image_row = _save_docker(
                docker,
                metadata,
                delivery_name,
                directories["docker-tar"],
                self.options.generated_on or date.today(),
                self.errors,
                self.warnings,
                self.options.require_docker,
            )

        return {
            "id": challenge_id,
            "tools": str(tools_zip),
            "report": str(pdf_path),
            "deploy": str(deploy_zip) if deploy_zip else None,
            "enclosure": str(enclosure_zip) if enclosure_zip else None,
            "docker_tar": str(tar_path) if tar_path else None,
            "image_row": image_row,
        }


def _require_valid_port(metadata: dict[str, Any], challenge_id: str) -> int:
    raw_port = metadata.get("port")
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise PackingError(
            f"{challenge_id}: containerized challenge has invalid port"
        ) from exc
    if not 1 <= port <= 65535:
        raise PackingError(
            f"{challenge_id}: containerized challenge has invalid port"
        )
    return port
