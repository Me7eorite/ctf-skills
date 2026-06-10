"""Delivery bundle output layout helpers."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from packing.errors import PackingError


def _prepare_output(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for owned_name in ("工具", "题库资源", "虚拟机资源"):
        owned_path = output / owned_name
        if owned_path.is_symlink():
            raise PackingError(f"refusing symlinked output path: {owned_path}")
        if owned_path.exists():
            if not owned_path.is_dir():
                raise PackingError(f"output path is not a directory: {owned_path}")
            shutil.rmtree(owned_path)


def _create_layout(output: Path) -> dict[str, Path]:
    paths = {
        "工具": output / "工具",
        "题库资源": output / "题库资源",
        "deploy": output / "题库资源" / "deploy",
        "enclosure": output / "题库资源" / "deploy" / "enclosure",
        "report": output / "题库资源" / "deploy" / "report",
        "虚拟机资源": output / "虚拟机资源",
        "docker-tar": output / "虚拟机资源" / "docker-tar",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    if not normalized:
        raise PackingError(f"invalid delivery name: {value!r}")
    return normalized
