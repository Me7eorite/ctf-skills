## Why

当前 build 阶段已经有顺序队列入口，但它仍是单个本地后台任务：worker 名称固定、结果文件固定、`TaskManager` 只持有一个 `_process`。这导致运营上不能并行跑多个顺序队列，也会让 dashboard 的 worker / result / progress 视图串线。

本题案目标是支持“多个顺序队列并行执行，每个顺序队列最多 12 个 build attempts”，同时保留队列内部现有的逐个 claim、逐个运行、逐个记录结果的语义。

## What Changes

- **新增** 队列编排层：把一个 ordered build-attempt 列表拆成多个顺序队列，每队最多 12 个 attempt。
- **新增** attempt 级 reservation：启动 worker 前先原子占用队列内每个 attempt，避免两个不同 `queue_id` 并发包含同一个 attempt。
- **新增** 队列级 worker 标识：每个顺序队列拥有独立 worker 名称、日志、metadata 和结果文件。
- **调整** CLI 顺序执行入口：支持 queue-scoped result output path，不能继续只写全局 `dashboard-sequential-worker-result.json`。
- **调整** dashboard / API：queue-start 返回 queue breakdown；实施进度界面按 queue worker/card 展示。
- **保持** execution-backed retry 语义：retry / clean-rebuild 复用原 `build_attempt` 容器并追加 `executions`，队列编排不得重新 mint attempt，也不得假设 pending shard 只有 `{build_attempt_id}.json`。

## Scope

This change touches build orchestration only:

- `src/web/dashboard.py`
- `src/web/build_attempts_endpoints.py`
- `src/web/server.py`
- `src/cli.py`
- `src/services/*`（如新增轻量 queue coordinator / reservation helper）
- `src/core/state.py`（仅在需要增强队列进度表达时）
- `tests/app/*` dashboard / build-attempt / sequential-queue tests

It does **not** change the publisher contract, challenge artifact layout, Hermes prompt format, or build-failure taxonomy. A database queue table is not required for the first release unless implementation proves disk-backed reservation cannot be made safe enough.

## Implementation Constraints

- `TaskManager` must replace the single sequential `_process` slot with queue-scoped process handles while preserving existing non-sequential worker/validate guards.
- Queue reservation must be atomic at the **attempt** level, not merely at the queue metadata filename level. If reserving any attempt fails, already-created reservations for that queue must be rolled back before returning an error.
- The CLI sequence body must keep lazy per-attempt claim/heartbeat. The coordinator must not eagerly mark or lease every attempt in a queue.
- Execution-minted attempts use shard basenames like `{build_attempt_id}.iter-NNN.json`; queue eligibility and progress lookup must accept both legacy `{build_attempt_id}.json` and iteration basenames.
- A non-terminal `latest_execution_id` is eligible only when it is the queued execution represented by the matching pending shard. Non-null `current_execution_id` or active queue reservation blocks assignment.
- Queue subprocess lifecycle must converge queue metadata to a terminal state even when the CLI exits before writing result JSON.
- The “progress worker” in this change is a queue-scoped dashboard worker card / metadata aggregator, not a new Hermes executor role. It observes metadata and progress events; it does not claim shards or write execution terminal state.

## Success Criteria

- A submitted batch is split into deterministic queues of at most 12 attempts.
- Concurrent queue-start requests cannot reserve the same attempt in two queues.
- Multiple sequential queues can run in parallel without sharing worker identity, result path, log path, or queue metadata.
- A queue process that exits early still produces terminal queue metadata with return code and log path.
- Dashboard shows one worker/queue card per active or recently finished queue.
- A failure in one queue does not cancel, corrupt, or overwrite another queue.
- Retrying an execution-backed build attempt after this change still appends a new execution under the same `build_attempt` container and does not create a new top-level execution workspace directory.

## Non-Goals

- Do not redesign Hermes prompt format.
- Do not change per-attempt build lifecycle or build-failure taxonomy.
- Do not introduce an external broker such as Redis.
- Do not modify challenge generation, validation, or publication semantics beyond the queue boundary.
