"""Deterministic zip archive helpers."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable

from packing.errors import PackingError


def _write_tools_zip(challenge_dir: Path, destination: Path) -> None:
    writenup = challenge_dir / "writenup"
    writeup = writenup / "wp.md"
    exp = writenup / "exp.py"
    if not writeup.is_file():
        raise PackingError(f"{challenge_dir.name}: missing writenup/wp.md")
    if not exp.is_file():
        raise PackingError(f"{challenge_dir.name}: missing writenup/exp.py")

    members = [(writeup, Path("wp.md")), (exp, Path("exp.py"))]
    extra_files = [
        path
        for path in sorted(writenup.rglob("*"))
        if path.is_file() and not path.is_symlink()
        and path.name not in {"wp.md", "exp.py"}
    ]
    for path in extra_files:
        members.append((path, path.relative_to(writenup)))
    _write_zip(destination, members)


def _tree_members(root: Path, archive_root: Path) -> Iterable[tuple[Path, Path]]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path, archive_root / path.relative_to(root)


def _enclosure_members(challenge_dir: Path) -> Iterable[tuple[Path, Path]]:
    for directory_name in ("dist", "attachments"):
        root = challenge_dir / directory_name
        if root.is_dir():
            yield from _tree_members(root, Path())


def _write_zip(destination: Path, members: Iterable[tuple[Path, Path]]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, archive_path in members:
            info = zipfile.ZipInfo.from_file(source, archive_path.as_posix())
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.read_bytes())
