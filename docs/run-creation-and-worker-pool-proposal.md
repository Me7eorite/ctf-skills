# Run Creation and Worker Pool Proposal

## Current Gaps

The dashboard currently presents New Run as if it can generate challenges from a
category and count, but the backend generation contract is seed based:

1. Persist complete challenge seeds with `POST /api/seeds`.
2. Split persisted seeds into pending shards with `POST /api/seeds/enqueue`.
3. Start a local worker with `POST /api/actions/worker`.
4. Inspect shard state through `GET /api/runs`.

This creates several product and API mismatches:

- The selected category in `NewRunPage` is preview-only and is not sent to the backend.
- The frontend uses `reverse`, while backend validation expects `re`.
- The numeric input is a shard size, not a challenge count.
- Presets store UI choices, not valid generation seeds.
- A successful enqueue still does not start Hermes.
- Empty projects fail because `work/challenge_seeds.json` does not exist yet.

## Immediate Fix

Add `POST /api/runs` as the dashboard's aggregate run-creation contract.

Request:

```json
{
  "seeds": [
    {
      "id": "web-0001",
      "title": "Session Trust",
      "category": "web",
      "difficulty": "easy",
      "points": 100,
      "port": 8080,
      "primary_technique": "auth bypass",
      "learning_objective": "Understand trust boundaries"
    }
  ],
  "shard_size": 1,
  "start_worker": true
}
```

Response:

```json
{
  "ok": true,
  "message": "已创建 1 个待处理分片",
  "seeds": ["web-0001"],
  "shards": ["web-0001-0001.json"],
  "worker": {
    "requested": true,
    "started": true,
    "message": "worker 已启动"
  }
}
```

The route preserves the existing lower-level endpoints. Existing operators can
still save seeds, enqueue, and start workers manually.

Frontend New Run should become a seed authoring screen:

- Collect the required seed fields.
- Use backend category names (`web`, `pwn`, `re`).
- Label shard size as shard size.
- Offer an explicit "start worker after enqueue" checkbox.
- On success, route to Runs when the worker is started, because the shard may
  be claimed and renamed immediately. Route to the first shard detail when only
  enqueueing.

## Worker Pool Direction

Do not make the frontend call multiple `/api/actions/worker` requests. The
dashboard should grow a scheduler-facing contract instead:

```json
{
  "seeds": [],
  "shard_size": 3,
  "start_worker": true,
  "worker_count": 4,
  "worker_mode": "local-pool"
}
```

Recommended backend evolution:

1. Introduce a `domain.tasks` model for requested runs and worker pool state.
2. Replace the single `TaskManager` process slot with a `WorkerPoolManager` that
   owns multiple subprocesses, names workers deterministically, and exposes each
   worker's PID, shard, log, and return code.
3. Keep `core.queue` as the shard source of truth; workers should still claim
   shards atomically through `ShardQueue`.
4. Add cancellation and concurrency limits before exposing arbitrary pool sizes.
5. Have `POST /api/runs` accept `worker_count` only after the pool manager exists.

This keeps today's UI workflow stable while allowing the execution backend to
move from one local worker to a bounded local pool.
