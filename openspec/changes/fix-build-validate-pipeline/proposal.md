## Why

生产环境观察到三个相互关联的问题，让"build 跑完 → 验证 → 重试"这条主线无法闭环：

1. 详情页（22+ progress events）每 2.5s 全量重渲染 + `list_attempts` 的 `progress` 子查询全表扫描 `progress_snapshots`，体感卡顿。
2. Hermes 生成的 `validate.sh` 用 `trap cleanup EXIT` 在出口处往 stdout 打印 "[*] Cleaning up..."，而 host validator 以 `last_nonempty_line(stdout)` 提取 flag —— 永远拿到 cleanup 行，结果是**所有题目恒判 `flag_mismatch`**，且容器名残留时会撞名触发 `nonzero_exit`。
3. `bcdd3fba` 已加入 per-attempt revalidate，但并发请求、validator 异常以及 failed→done 与数据库提交失败时仍可能留下矛盾状态。

前两个 bug 仍影响验证正确性和页面性能；第三个问题已从“缺少功能”转为“已有闭环的可靠性不足”。

## What Changes

- **Bug 2 — flag 提取兜底**：`ChallengeValidator.validate_one` 不再用 `last_nonempty_line`，改用带 token 边界的 flag 正则扫描 stdout，取最后一处匹配作为 `printed_flag`。
- **Bug 2 — validate.sh 模板**：更新 Hermes prompt 的 "Exploit Validation" 段，要求生成的 `validate.sh` (a) cleanup 函数全部输出走 `>&2`，(b) 脚本开头执行一次 pre-cleanup `docker rm -f "$CONTAINER_NAME" 2>/dev/null || true` 防止容器名残留触发 `nonzero_exit`。
- **Bug 3 — 加固现有 revalidate**：保留 `bcdd3fba` 已有的 failed-only API/UI 和 `complete/*` 事件语义；增加 PostgreSQL advisory lock、验证器异常终态、精确 artifact 目录绑定，以及 failed→done 文件移动在数据库提交失败时的反向补偿。
- **Bug 3 — 保持 UI 语义**：继续使用现有“重新校验 / 重试构建”按钮和中文失败摘要，不恢复已移除的列表级全量 Validate 按钮。
- **Bug 1 — `list_attempts` 子查询收敛**：先应用 latest fold、过滤、排序和 limit 得到 `selected_attempts`，`progress` 聚合只读取该批次的 shard；复用 `progress_snapshots(shard, challenge_id)` 主键索引，不增加冗余单列索引。
- **Bug 1 — 详情页事件时间线增量渲染**：poll 仅在非事件字段变化时全量渲染；append-only 事件按 `event.id` 插入新增节点并更新计数，无变化时不写 DOM。

无破坏性变更。现有 `POST /api/build-attempts/{id}/revalidate` 响应和 failed-only 前置条件保持不变。

## Capabilities

### New Capabilities

无新 capability。

### Modified Capabilities

- `build-orchestration`：加固现有 revalidate 契约 + `list_attempts` 性能契约（子查询规模与返回批次同阶）。
- `hermes-execution-protocol`：validate 阶段产物契约新增 "cleanup 输出禁止污染 stdout 的 flag 行" + host validator 用正则提取 flag 而非最后一行。

## Impact

**代码**：

- `src/domain/validation.py`：`validate_one` 的 flag 提取逻辑。
- `src/services/build_attempt_revalidation_service.py`：加固现有 revalidation。
- `src/persistence/repositories/build_attempts.py`：`list_attempts` 子查询收敛。
- `src/web/static/js/views/build-attempts.js`：详情页增量渲染、新 Revalidate 按钮、文案区分。
- `prompts/shard_prompt.md`：validate.sh 生成指引。
- 无 schema 迁移；现有复合主键索引覆盖 shard 前缀扫描。

**测试**：

- `tests/app/test_validate_challenge.py`：cleanup 行污染 stdout 时仍能正确提取 flag。
- `tests/app/test_build_attempts_api.py`：`POST /revalidate` 成功 / failed / 不存在 / 并发等场景。
- `tests/app/test_build_attempts_repository.py`：`list_attempts` 在 N=200 snapshots / 5 attempts 下的查询稳定性回归。

**运维**：无 schema 迁移；重启 server 后生效。
