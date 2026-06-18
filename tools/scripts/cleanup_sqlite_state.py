"""Remove legacy SQLite progress-state files.

This script is idempotent and deliberately targets only the exact files the
old progress store could create for a given project root.
"""

from __future__ import annotations

import argparse
import hashlib
import tempfile
from pathlib import Path


def _candidate_paths(root: Path) -> list[Path]:
    root = root.resolve()
    local = root / "work" / "state.sqlite3"
    root_key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    fallback = Path(tempfile.gettempdir()) / "challenge-factory" / root_key / "state.sqlite3"
    return [
        local,
        local.with_name("state.sqlite3-wal"),
        local.with_name("state.sqlite3-shm"),
        fallback,
        fallback.with_name("state.sqlite3-wal"),
        fallback.with_name("state.sqlite3-shm"),
    ]


def cleanup(root: Path, *, dry_run: bool = False) -> list[Path]:
    removed: list[Path] = []
    for path in _candidate_paths(root):
        if not path.exists():
            continue
        if path.is_dir():
            continue
        if not dry_run:
            path.unlink()
        removed.append(path)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    removed = cleanup(args.root, dry_run=args.dry_run)
    verb = "would remove" if args.dry_run else "removed"
    for path in removed:
        print(f"{verb} {path}")
    if not removed:
        print("no legacy SQLite state files found")


if __name__ == "__main__":
    main()
