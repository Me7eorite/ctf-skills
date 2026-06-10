## Why

Challenge generation currently starts from a hand-edited JSONL matrix and a
separate CLI `split` command. Operators can observe generation in the
dashboard, but cannot configure what should be generated there.

## What Changes

- Persist matrix-compatible seed rows in `work/challenge-seeds.json`.
- Add dashboard APIs to create/update, delete, list, and enqueue seeds.
- Add a `种子配置` dashboard view with common fields plus advanced JSON for
  category-specific matrix values.
- Reuse the existing category grouping and shard format when seeds are
  enqueued; refuse to overwrite an existing pending shard.

## Capabilities

### New Capabilities

- `challenge-seed-management`: dashboard-based seed configuration and queue
  creation for Web, Pwn, and Reverse challenges.

## Impact

- New `src/seeds.py`.
- Updates to `paths.py`, `shards.py`, `dashboard.py`, `webserver.py`, and the
  static dashboard.
- New seed and API tests; no new runtime dependency.
