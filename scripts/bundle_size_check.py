#!/usr/bin/env python3
"""Log the gzipped size of the initial JS bundle; warn above 800 KB.

The "initial bundle" is the entry chunk Vite emits as
``src/web/static/dist/assets/index-*.js``. Vendor and lazy chunks (Monaco,
per-page splits) are deliberately excluded — they do not load on the first
paint and so should not count against the budget.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

BUDGET_BYTES = 800 * 1024
DIST = Path(__file__).resolve().parents[1] / "src" / "web" / "static" / "dist" / "assets"


def main() -> int:
    if not DIST.is_dir():
        print(f"missing {DIST}; run `cd frontend && npm run build`", file=sys.stderr)
        return 1

    candidates = sorted(DIST.glob("index-*.js"))
    if not candidates:
        print("no index-*.js entry chunk found", file=sys.stderr)
        return 1

    entry = candidates[0]
    gzipped = len(gzip.compress(entry.read_bytes()))
    print(f"{entry.name}: {gzipped} bytes gzipped ({gzipped / 1024:.1f} KB)")

    if gzipped > BUDGET_BYTES:
        print(
            f"WARNING: initial bundle exceeds 800 KB gzipped budget by "
            f"{(gzipped - BUDGET_BYTES) / 1024:.1f} KB",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
