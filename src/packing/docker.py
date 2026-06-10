"""Docker image export helpers for packing."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from packing.errors import PackingError

ENCLOSURE_RULES = {
    "web": "skip",
    "pwn": "optional",
    "cloud": "optional",
}


def _save_docker(
    docker: str,
    metadata: dict[str, Any],
    delivery_name: str,
    output: Path,
    generated_on: date,
    errors: list[str],
    require_docker: bool,
) -> tuple[Path | None, list[Any] | None]:
    port = metadata.get("port", "")
    tar_name = f"{delivery_name}[{port}]-{generated_on:%Y%m%d}.tar"
    tar_path = output / tar_name
    image = metadata.get("docker_image") or f"{metadata.get('id')}:{generated_on:%Y%m}"
    process = subprocess.run(
        [docker, "save", "-o", str(tar_path), str(image)],
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        message = (
            f"{metadata.get('id')}: docker save failed for {image}: "
            f"{process.stderr.strip() or process.stdout.strip()}"
        )
        if require_docker:
            raise PackingError(message)
        errors.append(message)
        tar_path.unlink(missing_ok=True)
        return None, None
    return tar_path, [
        metadata.get("delivery_name") or metadata.get("title") or metadata.get("id"),
        tar_name,
        port,
        metadata.get("base_image", ""),
        metadata.get("start_command", ""),
    ]


def _should_emit_enclosure(category: str, include_pwn_attachments: bool) -> bool:
    rule = ENCLOSURE_RULES.get(category, "required")
    if rule == "skip":
        return False
    if category == "pwn":
        return include_pwn_attachments
    return rule == "required"


def _is_containerized(metadata: dict[str, Any]) -> bool:
    category = str(metadata.get("category", "")).lower()
    deployment = str(metadata.get("deployment", "")).lower()
    return category in {"web", "pwn"} or "docker" in deployment or bool(metadata.get("docker_image"))
