## Context

实际部署后跑 build → validate 这条主线发现三个互相加重的问题（详见 proposal）：

1. 详情页轮询 + 全量重渲染、`list_attempts` 的 `progress` 子查询 `GROUP BY shard` 全表扫描。
2. Hermes 生成的 `validate.sh` 用 `trap cleanup EXIT` 把清理消息写到 stdout，而 host validator 用 `last_nonempty_line(stdout)` 提 flag —— 恒判 `flag_mismatch`。容器名残留会让 `set -e` 触发 `nonzero_exit`。
3. 现有 revalidate 已能修复 failed attempt，但缺少跨请求互斥、异常终态和文件/数据库失败补偿。

这三个 bug 都不是结构性的，所以一份 design 一次性收掉。

涉及的现有模块：

- `src/domain/validation.py` — `ChallengeValidator`（flag 提取）。
- `src/persistence/repositories/build_attempts.py` — `list_attempts` 查询。
- `src/web/build_attempts_endpoints.py` — HTTP API。
- `src/web/static/js/views/build-attempts.js` — 详情页渲染 + 按钮。
- `prompts/shard_prompt.md` — `validate.sh` 生成指引。
- `progress_snapshots` 现有复合主键索引（`shard, challenge_id`）。

## Goals / Non-Goals

**Goals:**

- 修好"build 后 → revalidate 让 attempt 从 `failed` 走到 `succeeded`"这条闭环。
- 让现有 challenge artifact（已经在磁盘上、构建已通过）不需要重做 build 就能被验证为 `passed`。
- 解决"详情页轮询时明显卡顿"和"`list_attempts` 数据增长后线性变慢"两个用户可见性能问题。
- 所有改动无需 schema 迁移，可滚动重启部署。

**Non-Goals:**

- 不重构 `TaskManager` 的单进程槽位语义（保留旧 `/api/actions/validate` 的全量 CLI 行为，文案上区分即可）。
- 不引入新的 worker / 队列层；revalidate 直接由 HTTP 处理线程同步跑 `ChallengeValidator`（典型耗时 ≤ 15s，可接受）。
- 不增加 build_attempt 状态；revalidate 只允许最新 terminal attempt，并在当前单进程 server 内按 attempt 互斥。
- 不动 Hermes 自己的运行时 prompt 实际执行（仅扩展"validate.sh 必须满足的契约"）。

## Decisions

### D1. 加固现有同步 revalidate，不改变 API/UI 语义

**选**：保留现有 failed-only 端点；用 PostgreSQL session advisory lock 覆盖验证过程，
并用绑定目录的 validator adapter 调用 `validate_one`。

**理由**：

- 这是单题目验证（不是全量），典型耗时与 `validate.sh` 的"启容器 + curl /health + 跑 exp"持平，11–15s 量级，超时 30s 即可。
- 走 `TaskManager` 会被单进程槽位锁住（worker 在跑就拒），失去"per-attempt 触发"语义。
- 同步返回也让 UI 拿得到 `validation_status` 直接显示，不必再轮询另一个 task 状态机。

**取舍**：HTTP 处理线程被占住十几秒，但 FastAPI threadpool 默认 40 线程，单用户场景容量充足。如果未来要多人同时 revalidate，再上后台队列。

**备选**：放到 `TaskManager`（被否：抢锁）。放到 BG 队列（被否：维护复杂度太高，与现状不匹配）。

### D2. flag 提取使用带 token 边界的正则

**选**：用 `(?<![A-Za-z0-9_])flag\{[^\r\n{}]+\}(?![A-Za-z0-9_])` 取 stdout 里**最后一处**匹配作为 `printed_flag`。

**理由**：

- "最后一行 = flag" 这个约定在 shell `trap cleanup EXIT` 后必然破。改成"按 pattern 抓最后一处匹配"对 cleanup 输出、debug print、log 行都鲁棒。
- 选最后一处而不是第一处：exp.py 可能在调试时打印多个候选 flag，最终输出的才是 ground truth。validate.sh 已遵循"最后 echo flag"的语义，平移过来一致。

**取舍**：如果 exp.py 故意打印 `flag{wrong}` 然后再打印 `flag{correct}`，会以 correct 为准（与现有语义一致）。如果 cleanup 字符串里碰巧带 `flag{xxx}` 也会被抓到 —— 但 cleanup 函数本就不该输出 flag-shape 内容，且 hermes prompt 改造后 cleanup 走 stderr，这种 corner case 实际不会发生。

**备选**：让 host validator 通过 `process.stdout.split("\n")` + 反向遍历、跳过以 `[` 开头的状态行（被否：脆、与 shell 输出耦合）。要求 validate.sh 把 flag 写到固定文件（被否：契约改动太大，回归量大）。

### D3. Hermes prompt 同步改造 cleanup 输出 + pre-cleanup

**选**：双管齐下 —— host 端 D2 兜底 + prompt 端要求 cleanup 函数 `>&2`、`docker run` 之前 `docker rm -f`。

**理由**：

- D2 单独已经能让旧 artifact 通过，但旧 `validate.sh` 仍然有 `nonzero_exit` 风险（容器名残留导致 `docker run` 在 `set -e` 下中止）。
- 让以后新生成的题目从源头就守规矩；老题目通过 D2 救场即可，不必批量回头改老 `validate.sh`。

**取舍**：prompt 模板变更对 Hermes 行为有轻微 drift 风险，但属于 "约束加严"，不会让原本能过的题目变不过。

### D4. `list_attempts` 先固定返回批次再聚合 progress

**选**：构造包含全部过滤、排序和 limit 的 `selected_attempts` CTE，再用其 shard 集限制 progress 聚合；复用复合主键索引。

**理由**：

- 现有 query 结构（CTE `ranked` + 外层 LEFT JOIN `progress`）是合理的。瓶颈在 `progress` 子查询无 prefilter。
- IN 子集自动随外层批次大小（≤ `BUILD_ATTEMPTS_LIST_MAX_LIMIT=500`）封顶，配合索引基本是 O(N log N) 而非 O(global)。
- 不必引入物化视图或新的派生表。

**取舍**：subquery 关联可能让 Postgres planner 切换到 nested loop。对小批量（前端 ≤ 100）正是要的；对极端大批量（500 row × N snapshot per shard）也可控。

**备选**：完全 inline 一个 LATERAL 子查询（被否：增加 SQLAlchemy 复杂度，收益边际）。在应用层做两步查询（被否：多一次 round-trip 反而慢）。

### D5. 详情页 poll 在安全条件下局部追加事件

**选**：模块私有 `Map<event.id, DOMNode>` 维护已渲染事件。每次轮询先比较非事件字段：

1. 非事件字段变化、事件删除/乱序/重复 → 安全回退全量渲染。
2. 只有尾部新 id → 追加 `<div>` 并更新计数（事件行无 icon，不调用 `initIcons`）。
3. 事件完全相同 → 完全跳过 DOM 操作。

**理由**：

- progress 事件是 append-only，永远不会改某条已有 event 的内容；用 id 做 key 安全。
- `initIcons()` 全量重画是 lucide 的轻量级操作但在 22+ 节点 × 每 2.5s 仍然有感。事件增量路径不包含图标，因此无需调用它。
- 不需要引入 React/虚拟 DOM —— 当前 vanilla JS 视图层够用，按 id diff 是最小改动。

**取舍**：`sibling_attempts` 和顶部 header 仍然全量重渲染（数据量小，行为简单），只优化最重的事件时间线。

## Risks / Trade-offs

- **R1**：D1 同步阻塞 HTTP 线程十几秒。
  → 实测 `validate.sh` 平均 11s，threadpool 上限 40，单 user 场景充裕；当真出现多人并发，加一个 `asyncio.to_thread` 包装即可。
- **R2**：D2 在 cleanup 字符串里抓到伪造 `flag{...}`。
  → cleanup 函数职责是清容器，正常 Hermes 模板下不该出现 flag-shape 文本；D3 同步收紧 cleanup `>&2`，整体没有可被攻击面。
- **R3**：D3 让模板变动，Hermes 在已有题目上 retry 时若复用旧 `validate.sh` 不会自动升级。
  → 旧 `validate.sh` 在 D2 的 host 端兜底下也能通过；要让旧题目 cleanup 走 stderr，用户可手动 retry build（runner 会重新生成 validate.sh）。
- **R4**：复合主键索引是否被 planner 选择取决于数据分布。
  → 不添加冗余索引；用生产 `EXPLAIN ANALYZE` 验证，确有需要再独立提迁移。
- **R5**：D5 增量渲染逻辑 bug 可能导致幽灵节点 / 顺序错乱。
  → 用 `event.id` 严格升序排列、每次轮询用服务端返回的最大 id 做 watermark，单测覆盖"乱序到达"和"重复 id"两种 case。

## Migration Plan

1. PR 同时落 D1–D5（小到能 atomic review）。
2. 部署：无需 Alembic；server 走 `tools/scripts/serve.sh` 重启（接已有部署流程）。
3. 验证：
   - 用现有 `web-eb60923d-0001-sql-inject-bypass`（`flag_mismatch` 失败的那一条）触发 `POST /revalidate`，预期 → `succeeded`。
   - 详情页打开 ca789ee5 观察 22+ 事件时间线，确认 DOM 节点不再每 2.5s 全量重画（Chrome DevTools Performance 录制）。
   - `EXPLAIN ANALYZE` 看 `/api/build-attempts` 的 progress 子查询行为，确认是 `progress_snapshots(shard)` 索引扫描而不是 seq scan。
4. 回滚：
   - 代码层全部可 `git revert` 单 PR。
   - 无 schema 变更需要回滚。
   - 不存在数据兼容问题（无 schema 字段变化，只是新索引）。
