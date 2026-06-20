# Research 失败分类与恢复手册

Dashboard 根据 `research_runs.last_error` 动态派生失败分类，不修改历史数据。原始错误始终保留在失败卡片的“原始错误”折叠区。

| category | 中文标题 | 主要触发条件 | 推荐动作 |
| --- | --- | --- | --- |
| `timeout` | 研究执行超时 | Hermes 退出码 124，或错误包含 timeout/timed out | 调整 Hermes 超时；降低目标数量；检查日志是否已有完整输出 |
| `lease_expired` | 研究租约过期 | `lease expired` 或租约字段异常 | 检查 Worker 心跳与数据库；确认是否已生成 retry；检查日志恢复候选 |
| `parse_failure` | 研究输出无法解析 | `unparseable_output:*` | 检查日志末尾和终端 JSON；修正 prompt 输出约束后重试 |
| `quality_gate` | 研究质量未达标 | findings 数量不足或质量门错误 | 增加线索；调整目标/难度；检查 findings 重复或缺失 |
| `field_validation` | 研究字段校验失败 | source/finding 字段类型、必填值、索引或关联无效 | 对照原始 JSON 修正字段形状和 prompt 示例 |
| `binding` | 研究 Agent 配置不可用 | profile 未绑定、停用、不存在，或 role/binding 异常 | 检查 research binding；绑定并启用有效 Hermes profile |
| `runtime` | 研究运行时错误 | Hermes 非 124 退出、request 缺失、提交校验等运行错误 | 检查 Hermes/服务端日志和数据库状态；修复环境后重试 |
| `cancelled` | 研究已取消 | cancelled/canceled | 确认取消是否符合预期；需要时重新提交研究 |
| `unknown` | 未知研究失败 | 空错误或任何未命中规则的文本 | 展开原始错误并检查服务端日志；新增稳定错误形状时补 taxonomy |

## 从日志恢复结果

`recoverable=true` 仅表示日志通过安全读取检查并包含有序 stdout markers，不保证 JSON 解析或质量门成功。

- 单条恢复：在 Dashboard 失败卡片点击“尝试从日志恢复结果”，先预览，再确认。确认绑定预览返回的 SHA-256；日志变化会返回 `preview_stale`，必须重新预览。
- 单条只读检查：`challenge-factory research backfill --run-id <UUID> --dry-run`。
- 批量恢复：`challenge-factory research backfill --all-recoverable --apply`。每条 run 使用独立事务；任何 skipped/failed 都会使命令退出 1，但不会阻止后续候选继续处理。

手工恢复仅允许最新、没有 active/completed sibling、没有既有结果的 `failed` run。`running` run 仍只由 lease-rescue 路径处理。日志必须位于 `work/research/logs` 下，是不超过 10 MiB 的普通 UTF-8 文件。
