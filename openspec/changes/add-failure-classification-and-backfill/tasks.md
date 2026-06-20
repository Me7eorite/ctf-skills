## 0. 实施前盘点

- [x] 0.1 用 `rg` 盘点所有 `last_error` 写入点和 `ResearchValidationError` 文本，形成可提交的 taxonomy fixture；生产样本只能作为补充，不得成为测试依赖。
- [x] 0.2 记录现有 `_parse_research_output` 的 staging 写副作用、`_apply_run_completed` 的终态不变量以及 transaction helper 的 commit 边界。

## 1. R1 — 失败原因分类 taxonomy（后端纯函数 + 单测）

- [x] 1.1 新建 `src/domain/research_failure_taxonomy.py`：定义 `FailureCategory = Literal[...]`（9 个枚举值）+ frozen `FailureClassification` dataclass `(category, title, description, actions: tuple[str, ...])`。
- [x] 1.2 在同模块实现 `classify_last_error(text: str | None) -> FailureClassification`：按前缀/正则匹配（顺序 timeout → lease_expired → parse_failure → quality_gate → field_validation → binding → runtime → cancelled → unknown）；空 / None 落 `unknown`；`insufficient_findings:got=X,need=Y` 从字符串里解析出实际 X/Y 注入 description。
- [x] 1.3 新建 `tests/app/test_research_failure_taxonomy.py`，覆盖每个 category、所有当前失败写入点、规则优先级、大小写、`None`/空/超长/垃圾字符串不抛异常，以及动态 got/need 文案。
- [x] 1.4 跑 `uv run pytest tests/app/test_research_failure_taxonomy.py -v` 全过。

## 2. R2 — Run DTO 增量字段 + 懒检查 `recoverable`

- [x] 2.1 找到所有 run serializer（当前集中在 `src/web/research_endpoints.py::_run_dict`），让调用方显式传入 `ProjectPaths` / app paths 后统一加 `last_error_category/title/description/actions` 与 `recoverable`；禁止在 serializer 内隐式 `ProjectPaths.discover()`。非 failed 输出 `null/null/null/[]/false`。
- [x] 2.2 新增共用 safe-log helper：resolve 后限制在 `paths.research_logs`、普通文件、无逃逸 symlink、≤10 MiB、严格 UTF-8；定义稳定错误 code。`recoverable`、manual backfill preview/apply、以及现有 `_try_rescue_from_log` 都必须复用该 helper，避免自动 rescue 与手工 backfill 的日志安全边界漂移。
- [x] 2.3 `_is_run_recoverable` 仅处理 failed run，复用 safe-log helper 并只检查有序 markers，不做 JSON/质量门。
- [x] 2.4 扩展 endpoint 测试：完整分类字段；completed/running 非候选；missing/truncated/逃逸 symlink/非 UTF-8/超限日志均 false；所有 run views 字段一致。
- [x] 2.5 跑 `uv run pytest tests/app/test_research_api.py -v`。

## 3. R3 — 前端进度卡 alert 重渲染 + 历史表新列

- [ ] 3.1 在 `src/web/static/js/ui/format.js` 加 `failureMeta(category) -> {icon, tone}` 展示映射；title/description/actions 只使用 API 字段，不复制 taxonomy 文案。
- [ ] 3.2 重写 `src/web/static/js/views/research-requests.js` 当前 `latest?.last_error` alert 区域：标题行（图标 + API title）/ API description 段 / API actions 列表 / `<details>` 折叠原始 `last_error`。actions 为空数组时整段隐藏；该区域不得继续通过前端 `researchErrorMessage(latest.last_error)` 推导分类文案。
- [ ] 3.3 在运行历史表增加“失败原因”列；failed 行显示 API 的 `last_error_title`，其他行留空。
- [ ] 3.4 在 `src/web/static/css/views/research-requests.css` 加 `.rq-alert-actions ul`（缩进 + 圆点）、`.rq-alert-details summary`（可点击折叠样式）、`.rq-history-failure-col`（窄屏下 `display: none` via `@media (max-width: 767px)`）。
- [ ] 3.5 UI 测试覆盖 `failureMeta`、API 文案字段、`<details>`、actions、窄屏列与 HTML escaping；不得断言复制后端 taxonomy 的 JS 文案表。
- [ ] 3.6 启动 dev server 用浏览器手动验收一条 failed run：alert 渲染正确、actions 显示、`<details>` 可展开折叠、历史表新列显示。

## 4. R4 — 纯解析、materialize 与持久化边界

- [ ] 4.1 把 `_parse_research_output` 拆为无 I/O 的解析/规范化/质量门与显式 raw-text materialize；normal executor 与 lease rescue 保持原结果。
- [ ] 4.2 抽 `_persist_rescue_payload(session, run, source_payloads, finding_payloads, log_path)`；它只写 DB rows 与 `_apply_run_completed`，不开事务、不取锁、不 promote、不 commit。
- [ ] 4.3 lease rescue/backfill 调用方分别负责安全日志读取、staging → flush → promote → commit/savepoint 及异常清理；补 `_try_rescue_from_log` 拒绝逃逸/超限/非 UTF-8 日志和 commit 失败后 final cleanup 测试。
- [ ] 4.4 加 preview 文件树前后快照测试，证明带 `raw_text` 的 preview 也不创建 staging/final；跑 lease/executor/heartbeat/service 回归。

## 5. R5 — `ResearchBackfillService` + 新 API 端点

- [ ] 5.1 新建 `src/services/research_backfill_service.py`：定义 `BackfillPreview` 与 `BackfillResult` dataclass，类 `ResearchBackfillService(paths, repository_factory=None)`。
- [ ] 5.2 实现纯读 `preview(run_id)`：完整资格、安全日志、解析/质量门；返回 projected fields + `log_sha256`，不写 DB/FS。
- [ ] 5.3 实现 `apply(run_id, expected_log_sha256)`：使用与 `complete_run_with_staged_results()` 同级的手动 session/commit 边界，以便捕获 commit 失败并补偿 final 文件；在同一事务先用 UUID bytes 的稳定 signed-64 key 取 advisory lock（禁止 Python `hash()`），再 `SELECT FOR UPDATE` 重判资格、摘要与结果计数。
- [ ] 5.4 资格要求 failed + 最高 attempt + 无其他 queued/running/completed sibling + 无既有结果；分别映射 `run_not_terminal`、`superseded_run`、`active_sibling_run`、`already_has_results`。
- [ ] 5.5 apply 执行 materialize、共享 persist、flush、promote、commit；所有失败按阶段清理 staging/final，并写结构化成功/失败日志。
- [ ] 5.6 API 请求模型要求 `apply`、拒绝 extra；confirmed apply 强制 64-hex digest；包括畸形请求在内的 backfill endpoint 错误都显式返回顶层 `{code,detail}`，畸形请求使用 `invalid_request` 422。
- [ ] 5.7 服务/endpoint 测试覆盖 happy path、纯 preview、running/superseded/active sibling、路径逃逸/超限/非 UTF-8、stale digest、并发、commit 补偿和错误体形状。

## 6. R6 — 前端 backfill 弹窗 + alert 内入口

- [ ] 6.1 新建 `src/web/static/js/ui/backfill-dialog.js`：仿 `delete-dialog.js` 的弹窗骨架，导出 `confirmBackfill({preview})`；返回 Promise，resolve(true) = 确认，resolve(false) = 取消。
- [ ] 6.2 增加 preview/apply fetch；apply 必须携带 preview 的 `expected_log_sha256`，失败解析顶层 `{code,detail}`。
- [ ] 6.3 在进度卡 alert 中分类描述 / actions 下方加入 backfill 入口：仅当 `state.detail.latest_run.recoverable === true` 渲染；按钮文案"尝试从日志恢复结果"；点击 → `requestBackfillPreview` → 弹窗 → 确认后 `requestBackfillApply` → toast → `refreshDetail()`。
- [ ] 6.4 弹窗显示日志路径、摘要、数量和目标状态，并说明候选不保证成功；preview 错误禁用确认；`preview_stale` 不自动重试，要求重新预览。
- [ ] 6.5 在 `tests/app/test_research_requests_ui.py` 加静态契约断言：源码包含 `confirmBackfill`、`requestBackfillPreview`、`recoverable`、按钮文案"尝试从日志恢复结果"。
- [ ] 6.6 用测试 fixture 启动 dev server 验收 preview → 取消 → preview → 确认及 stale-preview 路径；生产样本仅作部署后可选观察。

## 7. R7 — CLI `challenge-factory research backfill`

- [ ] 7.1 增加 `backfill` 子命令；`--run-id <UUID>` 只接受一个值，并与 `--all-recoverable` 互斥。
- [ ] 7.2 校验互斥：单独 `--run-id ... --apply`（无 `--all-recoverable`）必须报错并 exit code != 0（spec 要求）；`--dry-run` 不能和 `--apply` 同时出现；`--all-recoverable` 必须配 `--dry-run` 或 `--apply` 之一。
- [ ] 7.3 实现 `--run-id <id> --dry-run`：调 `ResearchBackfillService.preview` 打印结果，每行格式 `[backfill] ...`；error 也按行打。
- [ ] 7.4 `--all-recoverable --apply` 扫 failed safe-log marker candidates，必须使用专用分页/流式枚举而不是 `list_runs(limit=100)` 默认上限；逐条 preview 并把 digest 传给 apply；每条独立事务，继续并汇总。任何 skipped/failed 时退出 1。
- [ ] 7.5 CLI 测试覆盖互斥/缺失 mode、单 run apply 拒绝、dry-run 零写、batch digest 传递、逐条失败继续和退出码。

## 8. 共用：分类 i18n 文案稳定性

- [ ] 8.1 把所有中文 title / description / actions 文案集中到 `src/domain/research_failure_taxonomy.py` 模块级常量字典 `_CATEGORY_COPY`，便于将来 i18n。
- [ ] 8.2 在 README 操作员手册（或新建 `docs/research-failure-categories.md`）列一张分类对照表：category 英文枚举 / 中文 title / 触发条件 / 推荐动作。

## 9. 上线验证

- [ ] 9.1 本地 `uv run pytest tests/app -q` 全过。
- [ ] 9.2 用可重建 fixture 在 dashboard 验证分类、原文 disclosure、preview/confirm/stale 和 hero 刷新。
- [ ] 9.3 跑 `uv run challenge-factory research backfill --run-id <fixture> --dry-run`，核对 DB 与文件树零变化。
- [ ] 9.4 部署后由操作员明确授权再运行 `uv run challenge-factory research backfill --all-recoverable --apply`；stdout 只是操作记录，不宣称强审计。
- [ ] 9.5 新建一个故意失败的需求（删 binding 或塞坏 prompt），确认 alert 显示对应分类（`binding` / `parse_failure`）且 `recoverable=false` 不显示按钮。
- [ ] 9.6 检查 server 启动日志中 `_sweep_stale_research_staging` / `_reconcile_orphan_research_sources` 未被本次改动破坏。

## 10. 收尾

- [ ] 10.1 commit 拆 6 段，每段独立 revert-safe（顺序见 design.md "Migration Plan"）。
- [ ] 10.2 PR description 引用 design.md 的 decisions 段；备注与上一轮 lease-rescue 提案的关系。
- [ ] 10.3 将自动化测试结果贴进 PR；浏览器截图和生产 batch log 仅在相应环境/授权存在时补充。
