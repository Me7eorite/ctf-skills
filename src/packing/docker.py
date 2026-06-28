"""Docker image export helpers for packing."""

from __future__ import annotations

import json
import re
import subprocess
import tarfile
from datetime import date
from pathlib import Path
from typing import Any

from packing.errors import PackingError

ENCLOSURE_RULES = {
    "web": "skip",
    "pwn": "optional",
    "cloud": "optional",
}


def _docker_safe(value: str) -> str:
    # 中文注释：和 build 阶段的镜像命名口径一致——小写、仅 [a-z0-9_.-]。
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-._")


def _image_repo(image: str) -> str:
    # 中文注释：取镜像引用的仓库名（去掉可选 registry 主机、tag、digest）。
    # registry:port 只会出现在更靠前的路径段，所以对最后一段去尾部 tag 是安全的。
    last_segment = image.split("@", 1)[0].rsplit("/", 1)[-1]
    return last_segment.split(":", 1)[0]


def _image_belongs_to_challenge(
    image: str,
    challenge_id: str,
    delivery_name: str,
) -> bool:
    """镜像是否属于这道题本身（防止 docker save 偏移到其它题/基础镜像）。

    build 阶段约定镜像名为归一化后的 ``<id>-<slug>``，题目 id 是稳定前缀，
    因此仓库名必须等于、或以 ``<id>-`` / ``<delivery_name>-`` 开头才算归属本题。
    """
    repo = _docker_safe(_image_repo(image))
    if not repo:
        return False
    candidates = {_docker_safe(challenge_id), _docker_safe(delivery_name)}
    return any(
        candidate and (repo == candidate or repo.startswith(f"{candidate}-"))
        for candidate in candidates
    )


def _verify_saved_image(tar_path: Path, image: str) -> str | None:
    """校验 docker save 产出的 tar 确实是目标镜像的存档。

    删除镜像是不可逆的敏感操作，所以 rmi 前必须确认 save 正确：tar 非空、是可读
    的镜像存档、且（当存档声明了 RepoTags 时）包含我们要保存的镜像仓库。返回错误
    描述则视为校验失败，调用方据此拒绝删除镜像。
    """
    if not tar_path.exists() or tar_path.stat().st_size == 0:
        return "saved tar is missing or empty"
    try:
        with tarfile.open(tar_path) as archive:
            member = archive.extractfile("manifest.json")
            if member is None:
                return "saved tar has no manifest.json"
            manifest = json.loads(member.read().decode("utf-8"))
    except (tarfile.TarError, OSError, ValueError, KeyError) as exc:
        return f"saved tar is not a readable image archive: {exc}"
    if not isinstance(manifest, list) or not manifest:
        return "saved tar manifest is empty"
    repo_tags = [
        tag
        for entry in manifest
        if isinstance(entry, dict)
        for tag in (entry.get("RepoTags") or [])
        if isinstance(tag, str)
    ]
    if repo_tags:
        target_repo = _docker_safe(_image_repo(image))
        if not any(_docker_safe(_image_repo(tag)) == target_repo for tag in repo_tags):
            return f"saved tar contains {repo_tags}, not the expected image '{image}'"
    return None


def _save_docker(
    docker: str,
    metadata: dict[str, Any],
    delivery_name: str,
    output: Path,
    generated_on: date,
    errors: list[str],
    warnings: list[str],
    require_docker: bool,
) -> tuple[Path | None, list[Any] | None]:
    challenge_id = str(metadata.get("id") or delivery_name)
    port = metadata.get("port", "")
    tar_name = f"{delivery_name}[{port}]-{generated_on:%Y%m%d}.tar"
    tar_path = output / tar_name
    image = str(metadata.get("docker_image") or f"{metadata.get('id')}:{generated_on:%Y%m}")

    def _fail(message: str) -> tuple[None, None]:
        # 中文注释：require_docker 时硬失败，否则降级为告警并清理半成品 tar。
        if require_docker:
            raise PackingError(message)
        errors.append(message)
        tar_path.unlink(missing_ok=True)
        return None, None

    # 护栏 1：镜像必须归属本题，绝不 save 到其它题或基础镜像。
    if not _image_belongs_to_challenge(image, challenge_id, delivery_name):
        return _fail(
            f"{challenge_id}: docker_image '{image}' does not belong to this "
            f"challenge; refusing to save another challenge's image"
        )

    # 护栏 2：镜像必须真实存在于本机（即 build 阶段确实生成过），否则不能交付。
    inspect = subprocess.run(
        [docker, "image", "inspect", str(image)],
        text=True,
        capture_output=True,
        check=False,
    )
    if inspect.returncode != 0:
        return _fail(
            f"{challenge_id}: image '{image}' not present on host; build did not "
            f"produce it, so docker save cannot deliver this challenge's image"
        )

    # 最后一步：save 镜像存档。
    process = subprocess.run(
        [docker, "save", "-o", str(tar_path), str(image)],
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        return _fail(
            f"{challenge_id}: docker save failed for {image}: "
            f"{process.stderr.strip() or process.stdout.strip()}"
        )

    # 护栏 3：删除镜像前必须确认 save 正确，否则保留镜像、丢弃坏 tar。
    verify_error = _verify_saved_image(tar_path, image)
    if verify_error:
        return _fail(
            f"{challenge_id}: docker save verification failed ({verify_error}); "
            f"image '{image}' is NOT removed"
        )

    # save 已确认正确，才删除镜像以节省空间。删除失败不致命：tar 已交付，仅告警。
    removal = subprocess.run(
        [docker, "image", "rm", str(image)],
        text=True,
        capture_output=True,
        check=False,
    )
    if removal.returncode != 0:
        warnings.append(
            f"{challenge_id}: docker image rm failed for {image}: "
            f"{removal.stderr.strip() or removal.stdout.strip()}; "
            f"tar delivered but image not pruned"
        )

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
