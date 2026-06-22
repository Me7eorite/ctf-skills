## Why

当前 build 阶段的“顺序队列”能力已经存在，但它仍然被实现为单个本地后台任务：一次只能运行一条顺序队列，worker 名称固定，结果文件也固定。这会带来两个问题：

1. 运营上无法同时执行多个顺序队列；
2. 看板上的进度展示无法把不同队列清晰分开，容易出现 worker 视图串线或结果覆盖。

对于“build 阶段同时执行多个顺序队列、且每个顺序队列最多 12 题”的需求，当前实现的缺口不在“单队列顺序执行”，而在“队列编排、队列隔离、队列级进度展示”。

## What Changes

- **新增** 队列编排层：把一个 build attempt 列表拆成多个顺序队列，每队最多 12 个 attempt。
- **新增** 队列级 worker 标识：每个顺序队列拥有独立 worker 名称和结果文件，避免相互覆盖。
- **调整** dashboard / API：支持同时启动多个顺序队列，并在实施进度界面中按队列展示 worker 与进度。
- **保持** 队列内部仍然按现有顺序执行器逐个 claim、逐个运行，不改变单队列的失败分类与恢复语义。

## Scope

This change touches the build orchestration surface only.

- `src/web/dashboard.py`
- `src/web/build_attempts_endpoints.py`
- `src/web/server.py`
- `src/cli.py`
- `src/core/state.py`（仅在需要增强队列进度表达时）
- `tests/app/*` 相关 dashboard / build-attempt / sequential-queue 测试

It does **not** change the publisher contract, challenge artifact layout, or database schema for build attempts unless later implementation proves a lightweight queue metadata table is required.

## Success Criteria

- A submitted batch can be split into multiple sequential queues.
- No sequential queue contains more than 12 attempts.
- Multiple sequential queues can run in parallel without sharing the same worker identity or result file.
- The dashboard can show one worker/queue card per sequential queue.
- A failure in one queue does not cancel or corrupt the others.

## Non-Goals

- Do not redesign the Hermes prompt format.
- Do not change the per-attempt build lifecycle or build-failure taxonomy.
- Do not introduce an external broker such as Redis.
- Do not modify challenge generation, validation, or publication semantics beyond the queue boundary.
