## Context

研究 run 的失败语义目前在三个层面错位：

1. **存储层**：`research_runs.last_error` 是自由文本字符串，由 executor 在不同失败路径写入，格式不统一（`"Hermes exited with 124"`、`"lease expired"`、`"unparseable_output:no_terminal_json_object"`、`"insufficient_findings:got=3,need=5"`、`"profile_disabled:default"` 等）。
2. **API 层**：`/api/research/requests/{id}` 直接把 `last_error` 透传给前端，没有结构化分类。
3. **UI 层**：[`research-requests.js:545`](../../../src/web/static/js/views/research-requests.js#L545) 的红色 alert 用 `researchErrorMessage()` 做一次极薄的字符串包装，呈现给操作员的是技术错误码本身。

操作员现场操作时的真实路径是：看到红色 alert → 看不懂 → 翻日志 → 翻代码确认错误码含义 → 决定重试/调超时/换 prompt。**这个流程对一线很反人类**，且对已经 failed 但日志里有完整结果的 run（如 `52f8fe4c-…`）完全无补救手段。

上一轮 `add-lease-rescue` 改动落在 `_try_rescue_from_log` 路径（[`research_job_service.py`](../../../src/services/research_job_service.py)），它只在 `claim_next_run` 的过期清扫窗口工作：要求 `status='running'` + `lease_expires_at < now`。一旦 run 已经被标 `failed`，那条路径就够不着了。

本提案在不引入新存储、不破坏现有 API 的前提下，补齐"看得懂失败"和"能手动救回"这两块缺口。

## Goals / Non-Goals

**Goals:**
- 建立 `last_error` 字符串 → 闭枚举分类的稳定映射，并通过 DTO 增量字段暴露给前端。
- 前端进度卡的失败信息从"一行原始字符串"升级为"图标 + 中文标题 + 描述 + 推荐动作 + 可折叠原文"。
- 在 dashboard 提供"从日志恢复结果"的两步交互（preview → confirm → apply），救回已 failed 但日志完整的 run。
- 抽出 `_persist_rescue_payload` 作为公共底层，让自动 rescue（`_try_rescue_from_log`）和手动 backfill 共用一段落库逻辑。
- CLI 保留两条能力以满足应急/审计/批处理：`--run-id ... --dry-run`、`--all-recoverable --apply`。

**Non-Goals:**
- 不做失败原因的趋势 metrics / 仪表盘（趋势统计放后续提案）。
- 不做"自动 re-queue 失败 run"——backfill 是补救而非重跑；自动重试由现有 `max_attempts` 机制覆盖。
- 不覆盖设计阶段 / build 阶段——本次只针对 research run。
- 不改 `last_error` 的存储格式或长度限制；分类逻辑全部在读路径上派生。
- 不引入 audit 表；操作记录依赖 server log + dashboard 历史。

## Decisions

### 1. 分类逻辑放后端，前端只渲染

**选择**：后端 `src/domain/research_failure_taxonomy.py` 提供纯函数
`classify_last_error(text) -> FailureClassification`。所有 run view 统一下发
`last_error_category/title/description/actions`；前端不复制描述与动作规则。

**理由**：
- 分类是**域知识**——"`Hermes exited with 124` 是超时"这种事，应该是后端的事实，不是前端约定。
- 同样的分类未来要给 CLI（如 `--all-recoverable --categories timeout,lease_expired`）、给 metrics、给日志聚合复用，CLI/批处理脚本不应该跑 JS 才知道分类。
- 前端只持有 `category → {icon, tone}` 的纯展示映射，标题/描述/动作文案由后端给。

**备选**：
- 纯前端分类：写一份 JS 映射表。**驳回**——CLI 复用难，且分类会和后端真实错误码漂移。
- 写到 DB 列 `last_error_category`：要 migration + 历史数据回填 + 字符串匹配又得保留。**驳回**——成本远高于派生。

### 2. 分类是闭枚举 + 前缀匹配

**选择**：分类枚举固定 9 个值（`timeout / lease_expired / parse_failure / quality_gate / field_validation / binding / runtime / cancelled / unknown`），匹配用前缀/正则，未知一律落 `unknown`。

**理由**：
- 闭枚举便于前端做 i18n 字典、CLI 做 filter、单测做穷举。
- 前缀匹配能吸收带具体值的错误（`profile_disabled:default`、`insufficient_findings:got=3,need=5`、`Hermes exited with 137`），不需要为每条 SKU 加分支。
- 未知落 `unknown` 时 UI 仍然展示原文，不阻塞操作员。

**备选**：开放分类（动态字符串）。**驳回**——失去 enum 校验和 i18n 能力。

### 3. `recoverable: bool` 是安全的低成本候选检查

**选择**：所有 run serializer 复用 `_is_run_recoverable`。它只对 `failed` run 执行，
并通过统一 safe-log helper 检查：路径 resolve 后位于 `paths.research_logs` 下、是普通文件、
不超过 10 MiB、可按 UTF-8 读取，且包含一对有序 stdout markers。

**理由**：
- 不做完整 `_parse_research_output`——把 JSON 解析 + quality gate 留给"点击预览"那一步。
- 不写 DB 意味着不需要 migration、不需要数据回填、不会有状态漂移。
- `recoverable=true` 只表示“值得预览”，不保证 backfill 成功；解析与质量门错误由 preview
  返回稳定 code。

**备选**：
- 完整 dry-run 解析后缓存到 `runs` 表新列：易漂移、违背"派生"原则。
- 不做 `recoverable` 直接让前端永远显示按钮：用户点了才发现救不回，体验差。

### 4. Backfill 是 UI 主导，CLI 是应急/审计/批处理

**选择**：
  - UI：`POST /api/research/runs/{run_id}/backfill`；preview body 为 `{apply:false}`，
    返回预计变化与 `log_sha256`；apply body 为
    `{apply:true, expected_log_sha256:"..."}`，摘要不一致返回 `preview_stale`。
- 前端 alert 内的"尝试从日志恢复"入口 → 弹窗 preview → 确认 apply → toast → 详情页 reload。
- CLI：保留 `cli research backfill --run-id <UUID> --dry-run`（一次审计输出）和 `--all-recoverable --apply`（一次性扫所有可救的 failed run）。**不暴露 CLI 单条 apply**——单条强制走 UI。

**理由**：
- 操作员日常工作面在 dashboard。让单条恢复走 UI 拿到弹窗二次确认，比 CLI 安全。
- 批处理（如迁移后清空积压）和应急（dashboard 进程挂了）仍需要 CLI 通道。
- 双通道共享同一个 service 方法，没有重复实现。

**备选**：
- 只做 CLI：违背 UI-first 操作惯例。
- 只做 UI：批处理和审计场景缺位。
- UI 直接 apply，无 preview：失败原因不可见，且单条 apply 是不可逆操作（虽然能再 backfill 一次但 sources/findings 会卡 UNIQUE 约束）。

### 5. 抽 `_persist_rescue_payload` 作为公共底层

**选择**：先把 `_parse_research_output` 拆成无 I/O 的解析/规范化路径与单独的 raw-text
materialize；再把 `_try_rescue_from_log` 里"落 sources/findings + 标 run completed"提取成
模块函数 `_persist_rescue_payload(session, run, source_payloads, finding_payloads, log_path)`。
helper 不开事务、不取锁、不 promote，由两条调用方负责文件生命周期：
- `_try_rescue_from_log`（自动 rescue，要求 `status='running'`，由 `claim_next_run` 触发）
- `ResearchBackfillService.apply`（手动 backfill，只允许最新 `failed` run，由 API/CLI 触发）

**理由**：避免两份落库逻辑漂移。`_try_rescue_from_log` 已经处理好了 staging promotion、UNIQUE 约束、savepoint 回滚等边界，手动 backfill 没理由再走一遍同样的弯路。

### 6. Backfill 的状态合法性约束

**选择**（apply 在同一事务取得 advisory lock 并 `SELECT ... FOR UPDATE` 后，按优先级递进）：
- run 必须存在 → 否则 404 `run_not_found`
- run.status 必须是 `failed`；completed → 409 `already_completed`，running/queued → 409 `run_not_terminal`
- run 必须是 request 的最高 attempt；存在更高 attempt → 409 `superseded_run`
- 不得存在其他 `queued|running` sibling → 409 `active_sibling_run`；不得存在其他
  `completed` sibling → 409 `superseded_run`
- run 下 sources count + findings count == 0；否则 409 `already_has_results`
- `hermes_log_path` 必须通过 safe-log path/type/size/UTF-8 检查；失败按
  `no_log_file|unsafe_log_path|log_too_large|log_unreadable` 返回 422
- `_extract_stdout_block` 命中 → 否则 422 `parse_failed:no_stdout_block`
- `_parse_research_output` 成功 → 否则 422 `parse_failed:<reason>`
- `apply_research_quality_gate(target_count=request.target_count)` 通过 → 否则 422 `quality_gate_failed:<reason>`
- apply 重新计算日志 SHA-256 并与 preview 提供值比较；不一致 → 409 `preview_stale`
- advisory key 由 UUID 16 字节稳定折叠为 signed 64-bit；禁止使用进程随机化的 Python `hash()`

**理由**：每个失败都给操作员**精确诊断**，而不是一个泛化"失败"。`already_has_results` 这条尤其重要——避免重入 backfill 撞 UNIQUE。

### 7. `request.status` 与唯一结果集

**选择**：有 active/completed sibling 时直接拒绝 backfill。通过资格检查后，run 与 parent
request 在同一事务分别变为 `completed` / `researched`，并清空 run 的 `last_error`。

**理由**：
- 避免旧 attempt 写出一套不会被消费的结果，也避免 retry 随后成功产生第二套 completed 数据。
- 保持现有 `_apply_run_completed` 的不变量：completed run 有 `finished_at`、`last_error=null`，
  parent request 为 `researched`。

**实现**：资格检查通过后复用 `_apply_run_completed`；原失败分类与日志摘要写结构化 server log。

### 8. CLI `--all-recoverable` 的范围

**选择**：扫 `status='failed'` 且通过 safe-log marker 候选检查的 run。逐条 preview，成功后
把 preview 的 `log_sha256` 传给 apply。每个 run 单独事务、单独 advisory lock；失败继续下一条。

**理由**：批处理工具应该是**幂等 + 可中断 + 报告式**的。已 sources 的 run 自动跳过（撞 `already_has_results`），不需要事先 filter。

## Risks / Trade-offs

- **[分类映射会随 `last_error` 写入路径变化漂移]** → Mitigation: `tests/app/test_research_failure_taxonomy.py` 覆盖所有当前已知 `last_error` 字符串前缀；CI 跑分类穷举测；新增/修改任何 `mark_run_failed` 调用点必须同步更新 taxonomy（在 CONTRIBUTING 加一行）。
- **[`recoverable` 检查的 IO 成本]** → Mitigation: 与 backfill 共用 10 MiB 上限；超限直接
  false，不缓存易漂移的文件状态。如列表规模增长，再以 `(path, size, mtime_ns)` 做短时缓存。
- **[操作员确认后日志变化]** → Mitigation: preview 返回 SHA-256，apply 强制匹配，否则
  `preview_stale` 并要求重新预览。
- **[操作员恢复已被 retry 取代的 run]** → Mitigation: 只允许最高 attempt，且拒绝任何
  active/completed sibling。
- **[`_persist_rescue_payload` 抽出过程改坏了 lease-rescue 路径]** → Mitigation: 先抽出 + 用现有 PG 集成测套件验证不退化（`tests/app/test_research_lease.py` 等已覆盖 rescue），再加 backfill 调用方。
- **[CLI `--all-recoverable` 长时间运行卡住其他写]** → Mitigation: 每个 run 用独立短事务（含 advisory lock），不要一个大事务包所有；批处理过程可 Ctrl-C 中断，已成功的 run 不回滚。
- **[新端点 `POST /api/research/runs/{run_id}/backfill` 的权限语义]** → 现行 dashboard 没有用户层鉴权（单租户内部工具）。本端点沿用同模型，不引入新鉴权层；若未来加权限，应与 worker 启停、需求删除等"运维操作"归为同一权限组。

## Migration Plan

无 schema migration。落地按以下顺序分段上线（每段一个 commit，确保可独立 revert）：

1. **第一段**：后端 `research_failure_taxonomy` 模块 + 单测；`_run_view` DTO 增字段 `last_error_category` / `last_error_title` / `recoverable`。**0 风险**——只读，旧前端忽略。
2. **第二段**：前端进度卡 alert 重渲染 + 运行历史表增列。验收：所有现有失败状态 run 在 UI 上显示新格式。
3. **第三段**：抽 `_persist_rescue_payload`；既有 lease-rescue 路径回归测必须全过。
4. **第四段**：纯解析/materialize 拆分、safe-log helper、`ResearchBackfillService` + 新 API
   端点（同时支持 preview 与 digest-bound apply）。这一段单独可由 curl 验收。
5. **第五段**：前端 backfill 弹窗 + alert 入口。用可重建 fixture 验收正常、取消、失败与
   stale-preview 路径；生产 run 只作为部署后观察项。
6. **第六段**：CLI `cli research backfill --run-id ... --dry-run` 与 `--all-recoverable --apply`。最后落地，主要服务后续历史积压清理与审计需求。

**回滚**：每段都是 forward-only-friendly：
- 一/三段是纯重构 + 增量字段，回滚只需 revert commit。
- 二/五段是前端 commit，单独 revert 不影响后端。
- 四/六段引入新接口，回滚 = revert + 端点立即 404；已 apply 过的 backfill 不会被回滚（这是预期行为——数据已经在 sources/findings 里了，本来就该保留）。

## Open Questions

- "运行历史"表的"失败原因"列在窄屏（< 768px）下隐藏，以避免破坏横向布局。
- 弹窗 preview 里要不要展示前 N 条 findings 的 label 让操作员肉眼核对？倾向**不展示**——信息冗余且增加渲染成本；只显示数量。如后续操作员反馈不放心再加。
- backfill apply 后不自动 redirect；刷新当前详情，让操作员核对已恢复 findings。
- backfill 是否需要写一行 audit log 到独立表？暂不——server 日志（结构化）+ dashboard 历史已经够。如审计强约束变化再单独开提案。
