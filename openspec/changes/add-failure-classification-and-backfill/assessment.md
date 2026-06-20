## 评估结论

原提案的问题识别成立，但初稿不能直接实施。主要矛盾是：手工恢复允许改写
`running` run，绕过 claim-token fencing；preview 会调用当前带文件写副作用的解析器；
UI 需要 description/actions，DTO 却未提供；preview 与 apply 之间没有日志内容绑定；
completed run 是否保留 `last_error` 与既有状态机互相冲突。

以下十轮均以当前代码、数据库约束和可测试行为为依据。每轮整改后重新检查对前后
轮结论的影响，第十轮通过后才建议进入实现。

## 十轮评估与整改

### 第 1 轮：手工恢复与 claim fencing

- **问题**：初稿允许 `ResearchBackfillService` 把 `running → completed`。
- **矛盾**：该入口没有 `claimed_by + claim_token`，会绕过所有 worker 终态写的 fencing；
  活 worker 随后还可能继续写结果。
- **整改**：手工 backfill 只接受 `failed`。`running` 返回 `run_not_terminal` 409；过期
  running run 继续只由现有 lease-rescue 在持行锁的清扫路径处理。
- **复评**：操作员入口不能夺取活 claim，queue API 的所有权模型保持闭合。

### 第 2 轮：retry/sibling 状态竞争

- **问题**：初稿允许恢复旧 failed attempt，并在有 queued/running sibling 时只不提升
  parent request。
- **矛盾**：这仍会写入一套孤立结果；retry 后续成功时产生两个 completed 数据集，父
  状态和下游选取语义不确定。
- **整改**：apply 加锁后要求目标是 request 的最高 attempt，且不存在其他
  `queued|running|completed` sibling；否则返回 `superseded_run` 或
  `active_sibling_run` 409。成功后总是原子提升 request 为 `researched`。
- **复评**：每个 request 在 backfill 完成时只有一套可消费结果，状态无需“稍后重算”。

### 第 3 轮：completed 的错误字段不变量

- **问题**：初稿允许 backfill 后 `last_error` 保留作审计。
- **矛盾**：既有规范和 `_apply_run_completed` 都要求 completed 时清空 `last_error`；新
  DTO 又规定 completed 不返回分类。
- **整改**：复用 `_apply_run_completed` 并清空 `last_error`。apply 前后写结构化 server
  log（run id、原分类、日志摘要、数量）；本提案明确不承诺持久化审计。
- **复评**：终态不变量、DTO 与现有实现一致；若需要持久审计应另提 audit 表变更。

### 第 4 轮：分类 DTO 的信息闭环

- **问题**：UI 要展示 description/actions，但初稿 DTO 只增加 category/title/recoverable。
- **矛盾**：前端被要求“只渲染后端文案”，却拿不到所需文案，只能复制 taxonomy。
- **整改**：增加 `last_error_description: string|null` 和
  `last_error_actions: string[]`；所有 run view 使用同一个 serializer。
- **复评**：前端无需复制域规则，API 字段足以完成规定 UI。

### 第 5 轮：preview 的文件副作用

- **问题**：当前 `_parse_research_output` 会把 `raw_text` 写入 staging；初稿要求 preview
  调它，同时又要求 preview 不改文件系统。
- **矛盾**：两条要求不能同时成立。
- **整改**：把“纯解析/规范化/质量门”和“raw_text materialize”拆开。preview 只走纯
  路径；apply 才 materialize，并沿用 promote/commit-failure cleanup。
- **复评**：preview 可由文件树快照测试证明零写入，自动 rescue 与 apply 共享纯解析和
  持久化 helper。

### 第 6 轮：preview/apply 的 TOCTOU

- **问题**：预览后日志可能被 worker、轮转任务或人工修改，apply 会提交不同内容。
- **矛盾**：UI 的二次确认并未绑定用户实际预览的数据。
- **整改**：preview 返回 `log_sha256`；apply body 必须带
  `expected_log_sha256`。apply 在锁内重新安全读取并比对，不同则返回
  `preview_stale` 409，要求重新预览。
- **复评**：确认动作与具体日志字节绑定，数量和目标状态不会静默变化。

### 第 7 轮：日志路径与资源边界

- **问题**：直接信任数据库中的 `hermes_log_path` 可读取任意路径/符号链接，且整文件
  `read_text` 没有大小上限。
- **矛盾**：新增 POST 运维入口放大了脏数据或路径篡改的影响。
- **整改**：统一 safe-log helper：resolve 后必须是 `paths.research_logs` 下的普通文件、
  不接受逃逸 symlink，最大 10 MiB，严格 UTF-8；分别返回 `unsafe_log_path`、
  `log_too_large`、`log_unreadable` 422。DTO 的 `recoverable` 也复用同一安全边界。
- **复评**：预检查与真实读取不会在安全口径上漂移，大日志不会无界占用内存。

### 第 8 轮：并发锁与事务/文件补偿

- **问题**：`pg_advisory_xact_lock(hash(run_id))` 未定义稳定 hash、取锁时机和事务范围；
  `_persist_rescue_payload` 也被描述成同时负责互相矛盾的 savepoint/promote/commit。
- **整改**：使用 UUID 字节稳定派生的 signed 64-bit key（禁止 Python `hash()`）；apply
  在同一事务中先取 advisory lock，再 `SELECT ... FOR UPDATE` 重读资格。共享 helper 只做
  DB rows + completed 状态；调用方负责 materialize、flush、promote、commit 和失败清理。
- **复评**：同 run 调用串行；commit 失败会清理 final，parse/persist 失败会清理 staging。

### 第 9 轮：错误协议、候选语义与 CLI

- **问题**：FastAPI 默认 `HTTPException(detail=...)` 不能产生规定的顶层
  `{code,detail}`；`recoverable=true` 仅凭 marker 也不代表解析必成功；CLI 范围前后不一。
- **整改**：端点显式返回统一错误对象；请求模型要求 `apply` 且拒绝未知字段。文档明确
  `recoverable` 是“可预览候选”而非成功保证。CLI 仅支持单个
  `--run-id ID --dry-run` 和 `--all-recoverable --apply`；批处理仍逐条 preview/apply，
  每条独立事务并传摘要。
- **复评**：HTTP、UI 和 CLI 对失败有同一稳定 code；启发式按钮不会被误解为保证。

### 第 10 轮：分类覆盖、测试与可执行验收

- **问题**：初稿只列少量前缀，当前解析器还会产生 `source field ...`、
  `finding source_indices ...` 等消息；任务依赖固定生产 UUID、lab DB 和截图。
- **整改**：taxonomy 测试从所有当前失败写入点建立 fixture，并验证规则优先级、大小写、
  超长/未知输入；新增 latest/sibling、running 拒绝、路径逃逸、大小限制、纯 preview、
  stale digest、并发、commit 补偿和顶层错误体测试。固定 UUID/生产回填改为部署后可选
  观察项，不作为实现完成条件。
- **复评**：提案约束与当前代码路径对应，自动化验收可在干净环境复现，可以进入实施。

### 第 11 轮：OpenSpec 结构化正文完整性

- **问题**：整改后的 Requirement 正文为便于阅读进行了换行，但 `openspec show --json`
  只把首行收进结构化 delta description。
- **矛盾**：Markdown 肉眼可见的安全和事务约束可能不进入后续归档/工具消费结果。
- **整改**：每条 Requirement 的完整规范正文合并为单个首段；场景仍按标准标题分隔。
- **复评**：`openspec show --json` 的 16 条 delta description 均包含完整 SHALL/MUST 约束。

### 第 12 轮：最终交叉复评

- **检查**：重新搜索旧口径（手工 running backfill、Python `hash(run_id)`、保留 completed
  `last_error`、repeatable `--run-id`、active sibling 仅不提升 request）并执行 strict validate。
- **整改**：清除残余实现任务中的旧口径；保留 assessment 中作为“初稿问题”的引用。
- **复评**：proposal/design/tasks/三份 delta spec 对状态资格、摘要确认、错误码、文件边界、
  并发与 CLI 口径一致；`openspec validate ... --strict` 通过。

## 最终推荐方案

1. 后端集中维护失败 taxonomy，并通过完整派生字段供所有 run DTO 与 UI 使用。
2. `recoverable` 只表示安全日志中存在完整 stdout block；真正资格由 preview 判定。
3. backfill 只恢复最新、未被 sibling 取代的 failed run；preview 纯读，apply 用日志摘要、
   advisory lock、行锁和文件补偿保证确认一致性与并发安全。
4. CLI 批处理逐条事务、可中断、可重跑；单条写操作保留在带二次确认的 UI。

## 剩余风险

- PostgreSQL 与文件系统不存在真正的分布式事务；方案通过 staging/promote 和 commit
  失败补偿收敛，但进程在 promote 与 commit 之间崩溃仍依赖现有启动 reconcile。
- `recoverable` 是低成本候选标志，可能在 preview 时因解析或质量门失败；UI 必须如实
  展示具体错误，不得把按钮文案表述为必然成功。
- 本提案没有持久化操作审计。若合规要求需要“谁在何时恢复了什么”，必须新增审计表
  与操作员身份体系，不能用 server log 冒充强审计。
