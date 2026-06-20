## Why

Research 子系统是 build → validate 之前唯一跨越「外部 LLM ↔ 本地 DB ↔ 本地 FS」三条边界的环节。生产实测发现这三条边界各有可证据的契约漏洞：subprocess 拿到 DB 凭证；FS 写入早于 DB 提交且不回滚；scoped worker 不验前置；状态词在 DB / API / UI 三处不一致；输出"评估"只做结构解析；同 run 内 content_hash 重复 DB 也不拒。这些坑每个操作员都遇得到，每次都得人盯 log 才能绕开。

本提案把这批**可证据、可修、不涉及产品形态变更**的契约漏洞一次收齐。明确**不**包括：研究内容质量评估、跨 request 去重 / novelty、UI 清理，以及 lost-lease 取消终态语义 —— 这些已规划成三个独立后续提案。

## What Changes

- **R1（research-planning · modified）**：Hermes research subprocess env 改 allowlist；显式 deny `*DATABASE*` / `*PASSWORD*` / `*TOKEN*` / `*SECRET*` / `*POSTGRES*` 等密钥族；profile binding 缺失或 `disabled` 直接 fail-fast 写 `failed: profile_not_bound|profile_disabled`，不再静默回退 default。
- **R2（research-planning · modified）**：research source 原文文件写入改成 `work/research/sources_staging/<run_id>/` 临时区，DB commit 成功后原子 promote 到 `work/research/sources/<run_id>/`；任何失败路径 finally 清理 staging；server 启动时清扫超龄孤儿 staging 目录；`resource_deletion_service` 删 request 时同时回收 `sources/<run_id>/`。
- **R3（research-planning · modified）**：`POST /api/research/requests/{id}/worker/start` 前置 SELECT 检查请求存在、请求状态 ∈ `{draft, researching, failed}`、存在 `queued` 或可恢复的过期租约 run；缺失分别返 `404 / 409 (already_researched|final_failure_no_retry_left|no_runnable_run)`；worker subprocess 起来后 ≤ 5s 内必须落 handshake 标记文件，否则 manager 报 `worker_startup_failed` 附 stderr tail；删除原 `sleep(0.2) + poll()`。
- **R4（research-planning · modified）**：HTTP `POST /api/research/requests` 接收并校验 `runtime_constraints`；顶层 key 限定白名单（`runtime` / `framework` / `language` / `compiler` / `target_format` / `architecture` / `port` / `mitigations` / `target_platform` / `strip`）；保留 `experimental.*` 命名空间允许任意 string→string；service 二次校验防 CLI/HTTP 不一致；CLI 同步暴露 `--runtime-constraint key=value`。
- **R5（research-planning · modified）**：HTTP submit 接受可选 `Idempotency-Key` 头；同一 `(category, topic, target_count, idempotency_key)` 在 TTL（默认 1800 秒，可由 `RESEARCH_SUBMIT_IDEMPOTENCY_TTL_SECONDS` 覆盖）内重复提交返回已存在 request（200 而非 201）；未带 key 时维持现有"每次新建"语义；DB 加 `generation_requests.idempotency_key` 字段 + 复合索引（非 UNIQUE，过 TTL 后允许重提）。
- **R6（research-planning · modified）**：DB 状态字典锁定为 `{draft, researching, researched, failed}`；API 响应字段拆双轨：`status` = 持久化态，`display_status` = 派生态（含 `queued` 等 UI 友好态）；列表 `?status=` 严格走持久化字段；新增 `?display_status=` 走派生；submit 响应去硬编码 `"queued"`，改返 `{request, latest_run}` 双对象。
- **R7（research-planning · modified）**：研究输出最小质量门 —— URL 匹配 `^https?://[^\s]+$` 且 host 非空、content_hash 匹配 `^[0-9a-f]{64}$`、单 run 内 `(run_id, content_hash)` 由现有普通索引升级为 UNIQUE 约束（含历史数据 dedup pre-step）、findings 数 ≥ `ceil(target_count * 0.5)`；stdout 解析改成"从尾部反向找最后一个合法 JSON object"，允许 Hermes 前置 markdown / log 噪声；不达标判 `failed`，error 携带具体 diagnostic（`url_shape_invalid` / `content_hash_dup:<hash>` / `insufficient_findings:got=N,need=M`）。
- **design-task-planning · modified**：明确 `research_run.status = 'failed'` 不参与 design-task 生成；同时拒绝"理论上 R7 已挡掉但残留在历史数据里"的 `findings_count < ceil(target_count * 0.5)` 的 completed run（防御性兜底）。

无破坏性变更对操作员手工流程；但 submit 响应字段拆分对脚本化 caller 是 **breaking** —— 见 design.md 的迁移说明。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `research-planning`：env 隔离 / FS 原子性 / preflight + handshake / runtime_constraints / idempotency / 状态语义 / 输出质量门 七处契约都集中在此 capability。
- `design-task-planning`：新增"拒绝 failed / 不达标 research"的生成前置条件。

## Impact

**代码**

- `src/hermes/research.py`：env allowlist + deny。
- `src/services/research_agent_executor.py`：profile binding fail-fast；stdout 尾部 JSON 解析；FS staging 路径。
- `src/services/research_job_service.py`：idempotency lookup；FS promote / cleanup 钩子。
- `src/services/research_worker.py`：subprocess 启动写 handshake。
- `src/services/resource_deletion_service.py`：把 `work/research/sources/<run_id>/` 加进 deletion scope。
- `src/web/research_endpoints.py`：runtime_constraints 接收 + 白名单；scoped worker preflight；submit 响应字段；filter 改持久化字段。
- `src/web/research_worker_manager.py`：换 handshake-based startup 取代 `sleep(0.2) + poll()`。
- `src/web/server.py`：启动钩子调 staging 孤儿清理。
- `src/domain/research_validators.py`：URL/sha256 校验；min-coverage；runtime_constraints 校验。
- `src/cli.py`：`research submit` 子命令补 `--runtime-constraint key=value`（可多次）。
- 新增 `src/services/research_artifact_promotion.py`（或合入 job_service）：promote / cleanup 实现。

**Alembic 迁移**

- `0010_generation_request_idempotency_key.py`：新增 `idempotency_key` text 列 + `(category, topic, target_count, idempotency_key, created_at)` 复合索引（非 UNIQUE）。
- `0011_research_sources_content_hash_unique.py`：先跑 dedup 脚本（保留每 `(run_id, content_hash)` 组里 `id` 最早一行，删晚的对应 findings 引用一并修正），然后 `drop_index('ix_research_sources_run_hash') + create_unique_constraint(...)`；downgrade 反向。
- 配套 `tools/scripts/dedup_research_sources.py`：迁移前的 dry-run 报告 + 实际执行入口。

**测试**

- `tests/app/test_hermes_research.py`：env allowlist 含 deny 验证；末尾 JSON 解析允许噪声。
- `tests/app/test_research_services_unit.py`：profile fail-fast、staging 清理、idempotency TTL、promote 原子性。
- `tests/app/test_research_api.py`：scoped worker preflight 三种 4xx；runtime_constraints 黑白名单；status / display_status 双字段；submit 响应字段。
- `tests/app/test_research_worker_manager.py`：handshake 成功 / 超时 / stderr tail。
- `tests/app/test_research_validators.py`：URL/hash/dup/min-coverage 边界。

**运维**

- `alembic upgrade head` 触发两条迁移；`0011` 前必须先跑 dedup 脚本，迁移本身会做最终 sanity check 并在仍有重复时报错回滚。
- server 重启沿用 `tools/scripts/serve.sh`（已有 `__pycache__` 清理 + `PYTHONDONTWRITEBYTECODE`）。
- 配置层无新增运行时变量；可选 `RESEARCH_SUBMIT_IDEMPOTENCY_TTL_SECONDS` 调整 R5 的 TTL。

**显式 Out-of-Scope（不在本提案）**

- F7 lost-lease 取消终态语义 → 后续提案 `define-research-cancellation-terminal-states`。
- F13 跨 request novelty / 重复 topic 防重 → 后续提案 `add-cross-request-research-novelty`。
- F14 research / design-task 页面 UI 清理 + 中文 copy → 后续提案 `revamp-research-design-task-ui`。
- Hermes 心跳线程 / 双 worker 资源浪费 → 非提案的单 PR hardening（后续 issue 跟踪）。
