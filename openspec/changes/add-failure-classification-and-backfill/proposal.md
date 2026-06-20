## Why

研究 run 失败后，两类信息没有暴露给操作员：
1. **失败原因的分类与可操作建议**——UI 上的"本次运行未完成"红色 alert 直接显示数据库里的 `last_error` 原始字符串（`Hermes exited with 124`、`lease expired`、`unparseable_output:no_terminal_json_object` 等），操作员必须翻代码才能判断属于哪类失败、该怎么处理。
2. **从日志恢复研究结果的能力**——上一轮 lease-rescue 改动（已上线）能拦住未来的"心跳丢失/超时"，但**存量已 failed 的 run**（如 `52f8fe4c-…`）即便 Hermes 日志里有完整可解析的 JSON，也永远不会被自动恢复，操作员只能放弃这次成果或重跑研究。

这两件事共享底层解析逻辑（`_parse_research_output` + `_extract_stdout_block`），适合作为一组能力同时交付。

## What Changes

- 新增后端 `classify_last_error(text)` 纯函数，返回不可变的
  `FailureClassification(category, title, description, actions)`；分类闭枚举：`timeout /
  lease_expired / parse_failure / quality_gate / field_validation / binding / runtime / cancelled /
  unknown`。
- 研究 run DTO 上**增量**新增五个分类字段：`last_error_category`、`last_error_title`、`last_error_description`、`last_error_actions`、`recoverable: bool`。未消费这些字段的旧客户端不受影响。
- 详情页"本次运行未完成"红色 alert 重新渲染为：图标 + 分类标题 + 中文描述 + 推荐动作列表 + 可折叠原始 `last_error`。
- "运行历史"表新增一列摘要，展示失败原因分类标签。
- 新增 `ResearchBackfillService` + 端点 `POST /api/research/runs/{run_id}/backfill`，支持 `apply=false`（预览）与 `apply=true`（带预览摘要确认后落库）。服务只接受未被 retry/成功 sibling 取代的最新 `failed` run，不允许手工改写 `running` run。
- 详情页 alert 内在分类描述下方渲染"尝试从日志恢复结果"入口（仅当 `recoverable: true`）；点击 → 弹窗预览 → 确认 → 应用 → 自动刷新。
- CLI `challenge-factory research backfill` 精简成两条用法：`--run-id <UUID> --dry-run`（审计/单条检查）和 `--all-recoverable --apply`（批处理历史积压）；**单条 apply 走 UI 不在 CLI 暴露**。
- 抽出 `_persist_rescue_payload` 模块函数，让 `_try_rescue_from_log`（lease-rescue 路径）和 `ResearchBackfillService`（手动 backfill 路径）共享同一段落库逻辑，避免漂移。

## Capabilities

### New Capabilities
- `research-failure-classification`: 把 `last_error` 字符串映射成闭枚举类别 + 中文描述 + 推荐动作清单，并通过 DTO 字段把这层信息暴露给 UI。
- `research-result-backfill`: 在 run 处于 `failed`、是 request 的最新 attempt、未被 active/completed sibling 取代时，从 `hermes_log_path` 解析 Hermes 输出并把结果补落库；提供 UI 交互（preview → confirm → apply）与 CLI 应急/批处理通道。`running` run 仅由 lease-rescue 自动恢复，不开放手工接管。

### Modified Capabilities
- `research-planning`: 研究 run DTO 新增结构化失败分类字段与 `recoverable` 候选标志；同时引入唯一的操作员恢复转换 `failed → completed`。该 run 必须是 request 的最新 attempt、没有 active/completed sibling、没有既有结果，且日志可安全读取、可解析并通过质量门。

## Impact

- 新增模块：`src/domain/research_failure_taxonomy.py`、`src/services/research_backfill_service.py`、`src/web/static/js/ui/backfill-dialog.js`。
- 修改：`src/web/research_endpoints.py`（DTO 字段 + 新 POST 端点）、`src/services/research_job_service.py`（抽 `_persist_rescue_payload`）、`src/services/research_agent_executor.py` 间接复用、`src/web/static/js/views/research-requests.js`（alert 重渲染 + 历史表列）、`src/web/static/css/views/research-requests.css`、`src/cli.py`（精简 `research backfill` 子命令）。
- 测试：`tests/app/test_research_failure_taxonomy.py`（单测）、`tests/app/test_research_endpoints.py`（扩展字段断言）、`tests/app/test_research_backfill_service.py`（PG 集成）、`tests/app/test_research_backfill_cli.py`（CLI）、`tests/app/test_research_requests_ui.py`（静态契约扩展）。
- 数据库：**无迁移**——分类是纯派生，backfill 复用现有 sources/findings/runs 表。
- 向后兼容：所有新 DTO 字段都是增量派生字段；旧 API 端点签名不变；新增 CLI 子命令不改变既有命令。
