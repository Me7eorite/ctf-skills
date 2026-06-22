# 方案 A 实现设计：build_attempt 容器化 + execution 迭代

> 起草: 2026-06-22 · 归属: worker-pool-split-plan.md 题案 3 的实现级展开
>
> 决策前提（见 split-plan v5 修订）: workspace 的隔离单元从「attempt」收敛到「challenge 容器」。
> 本文把「retry 从 build_attempt-minting 迁移到 execution-minting」落到表结构、改写点、迁移和测试。

---

## 一、模型反转：一句话

**现在**：一道题每次重试 = 一个**新 build_attempt 行**（`attempt_id = uuid4()`，`attempt_no+1`），workspace 按 build_attempt_id 命名 → 每次重试一个裸 UUID 顶层目录。

**方案 A**：`build_attempt` 升格为「一次构建会话**容器**」，retry/revision 不再新建 build_attempt，而是在**同一 build_attempt 下 `INSERT executions`**（`iteration_no+1`）。`derive_workspace_id` 已经按 `build_attempt_id` 命名——一旦重试复用同一 build_attempt，**目录键无需改动即自动变成「按题稳定目录」**，`iter-NNN` ↔ `executions.iteration_no` 天然对齐。

容器粒度 = **build_attempt（构建会话）**，不是 design_task。同题若整轮放弃后重新提交 → 新 build_attempt → 新干净目录（旧会话已归档）；一轮内的所有 retry/revision 都留在同一目录。

---

## 二、数据模型

### 2.1 新增 `executions` 表

```sql
CREATE TABLE executions (
    id                  UUID PRIMARY KEY,
    build_attempt_id    UUID NOT NULL REFERENCES build_attempts(id) ON DELETE CASCADE,
    parent_execution_id UUID REFERENCES executions(id),
    iteration_no        INT  NOT NULL,                  -- 容器内单调递增，initial=1
    execution_kind      TEXT NOT NULL,                  -- initial | retry | revision
    worker_id           TEXT,                           -- 题案 4 再 ALTER ADD agent_id
    claim_token         UUID NOT NULL,                  -- fencing token，lease 回收时重签
    lease_expires_at    TIMESTAMPTZ NOT NULL,
    heartbeat_at        TIMESTAMPTZ,
    status              TEXT NOT NULL,                  -- claimed | running | succeeded | failed | lost
    exit_class          TEXT,
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_executions_kind   CHECK (execution_kind IN ('initial','retry','revision')),
    CONSTRAINT ck_executions_status CHECK (status IN ('claimed','running','succeeded','failed','lost')),
    CONSTRAINT ck_executions_revision_parent
        CHECK (execution_kind <> 'revision' OR parent_execution_id IS NOT NULL),
    CONSTRAINT uq_executions_attempt_iter UNIQUE (build_attempt_id, iteration_no)
);

-- 容器内单活跃：替代旧 one_active_build_per_task 的并发守卫职责
CREATE UNIQUE INDEX one_active_execution_per_attempt
    ON executions (build_attempt_id)
    WHERE status IN ('claimed','running');

-- reaper 扫描 lease 到期
CREATE INDEX ix_executions_lease ON executions (status, lease_expires_at)
    WHERE status IN ('claimed','running');

CREATE INDEX ix_executions_attempt_iter ON executions (build_attempt_id, iteration_no DESC);
```

### 2.2 `build_attempts` 字段去重与容器化

| 字段 | 现状 | 方案 A 后 |
|---|---|---|
| `id`, `design_task_id`, `created_at` | 保留 | 容器主键与归属，不变 |
| `attempt_no` | 每次重试 +1 | 语义变「该题第几个**构建会话**」，**只在 fresh submit 时 +1**（retry 不再分配） |
| `shard_basename` | `{attempt_id}.json` | 保留为容器当前 shard（每轮重渲染覆盖，见 §4.3） |
| `status` | queued/running/succeeded/failed/lost | 容器**聚合状态**，从最新 execution 派生维护 |
| `worker` | nullable text | **下沉** executions.worker_id |
| `error` | nullable text | **下沉** executions（每轮各自 error） |
| `started_at`/`finished_at` | 容器级 | 留容器（首轮起、末轮终），逐轮时间在 execution |
| `resulting_challenge_dir`/`artifact_status` | 容器级 | 留容器，指向**最终成功**产物 |
| `idempotency_key` | 有 | 保留（clean_rebuild 用） |
| **新增** `current_execution_id` | — | nullable FK，活跃轮 |
| **新增** `latest_execution_id` | — | nullable FK，最新轮（含终态） |
| **新增** `successful_execution_id` | — | nullable FK，由 publisher 成功时回填（题案 6 正式接管） |

`one_active_build_per_task` 部分唯一索引**保留在 build_attempts**（保证同 design_task 同时只有一个活跃构建会话），判定依据改为容器聚合 `status IN ('queued','running')`，而该状态由 execution 转换驱动维护。

---

## 三、claim / lease 单事务流程

复用现有 `with_for_update()` 行锁（[build_attempts.py:40](src/persistence/repositories/build_attempts.py#L40)），在同一事务里多签 token + lease：

```text
BEGIN
  SELECT design_task / build_attempt FOR UPDATE          -- 沿用现有锁
  iteration_no := COALESCE(MAX(executions.iteration_no), 0) + 1
  claim_token  := uuid4()
  lease_expires_at := now() + LEASE_TTL                  -- 默认 = 现 BUILD_LOST_GRACE(300s)
  INSERT executions(status='claimed', execution_kind, parent_execution_id, iteration_no, claim_token, lease_expires_at)
  UPDATE build_attempts SET status='running', current_execution_id=:new, latest_execution_id=:new
COMMIT
```

`one_active_execution_per_attempt` 部分唯一索引在 INSERT 时兜底：若已有活跃 execution，第二个 claim 直接撞唯一约束失败，不依赖应用层判断。

---

## 四、retry/rebuild/revision 改写点（orchestration service）

### 4.1 `_prepare` —— 拆分 fresh submit 与 retry 两条路

当前 [build_orchestration_service.py:347](src/services/build_orchestration_service.py#L347) 无差别 `attempt_id = uuid4()`。改为：

- **fresh submit**（`submit_batch`，`retry_sources` 为空）：建 build_attempt 容器 + execution(kind=`initial`, iter=1)。
- **retry/clean_rebuild**（`retry_sources` 非空）：解析既有 build_attempt（由 `retry_sources[task_id]` → 该 attempt 的容器），**不建新 build_attempt**，只 INSERT execution(kind=`retry`, iter=max+1, parent=上一轮 execution)。
- **revision**（人工反馈触发）：同 retry，kind=`revision`，并 materialize base-artifact + feedback（§六）。

### 4.2 `_commit` —— `create_attempt` 调用点分流

[build_orchestration_service.py:399](src/services/build_orchestration_service.py#L399) 现在统一 `create_attempt`。改为：
- fresh → `create_attempt`（保留，但内部还要建 iter=1 execution）。
- retry → 新增 `create_execution(build_attempt_id, kind, parent)`，不碰 `next_build_attempt_no`。

`_validate_task_for_submit` 的资格校验（[:408](src/services/build_orchestration_service.py#L408)）逻辑可保留——它判 task 状态 + latest attempt failed/lost，正是「容器可追加新 execution」的前置；只是「latest attempt」语义换成「容器的 latest_execution」。

### 4.3 shard 文件策略

shard 仍命名 `{build_attempt_id}.json`，**每轮重渲染覆盖**（带上本轮 resume_from / base-artifact ref / feedback），保持 `work/shards` 扫描和队列认领逻辑不变。**每轮不可变快照**落在 workspace 的 `attempts/iter-NNN/input/shard.json`，审计取此处而非 `work/shards`。

---

## 五、token fence 插桩点

| 位置 | 现状 | 加固 |
|---|---|---|
| `update_to_running` ([build_attempts.py:209](src/persistence/repositories/build_attempts.py#L209)) | 只校验 status==queued | + 校验 execution.claim_token 匹配 |
| `update_to_terminal` ([build_attempts.py:230](src/persistence/repositories/build_attempts.py#L230)) | 只校验 status∈{queued,running} | **核心 fence**：token 不匹配 → 拒绝 complete/fail，输出留 quarantine |
| publisher（题案 2） | 无 | 原子 rename 前重校验 token；过期则不 publish |
| heartbeat（新增 endpoint） | 无 | 校验 token + 续 lease（`lease_expires_at = now + TTL`, `heartbeat_at = now`） |

旧 token 一律拒绝 = lease 过期被回收后，旧 Hermes 进程可本地跑完但任何写库/publish 都被 fence。

---

## 六、reconciler 改造（lease 回收器）

`BuildReconciler` 现在按 300s grace 把卡住的 attempt 标 `lost`，且代码注明「状态只有 reconciler 自己会改」（[build_reconciler.py:111](src/services/build_reconciler.py#L111)）。方案 A 后它升级为 **execution lease 回收器**：

- 扫 `executions WHERE status IN ('claimed','running') AND lease_expires_at < now AND (heartbeat_at IS NULL OR heartbeat_at < now - TTL)` → 标 `lost`，需要恢复时**重签 claim_token**（旧 token 自此失效）。
- execution 进终态 → 派生维护 build_attempt 容器聚合 status / latest_execution_id。
- 「状态只有 reconciler 改」的不变式放宽为「**execution 状态由 claim 路径、worker、reconciler 共同改，但写库一律经 token 校验**」，安全性由 fence 而非独占保证。
- 现 `BUILD_LOST_GRACE=300s` 直接复用为 `LEASE_TTL` 默认值，逻辑同构。

---

## 七、workspace 接线（题案 1，按题收敛布局）

`derive_workspace_id` 返回 `build_attempt_id`，retry 复用同容器后**自动稳定**，无需改键。`prepare_workspace` 改造：

1. 题目目录 `work/executions/<build_attempt_id>/` 不存在则建；`references/` 只首次 materialize。
2. 推进新一轮前：把旧 `current/{output,logs}` 归档进 `attempts/iter-<prev>-<exit_class>/`（**不 rmtree 抹除**），`iter` 号取上一轮 execution.iteration_no。
3. 清空并重建 `current/`，cwd 恒定指向 `current/`。
4. revision：base-artifact 从 `../../attempts/iter-(N-1)/output` 就地 symlink/copy（§六的复用前提）。

---

## 八、UI 接线

- `list_attempts` 已按 design_task 折叠取 latest（[build_attempts.py:90](src/persistence/repositories/build_attempts.py#L90)），容器化后天然变成「一题一行」。
- 详情/「最近完成」改为：build_attempt 容器 → executions 时间线（`iter-1 失败 → iter-2 失败 → iter-3 通过`）。截图里同题 3 行、看板 2 张孤立 shard 卡的怪象由此消除。
- 收尾题案 `add-build-attempt-feedback-ui`：在容器详情页展示 execution 历史 + 每轮 feedback + 触发 revision 按钮。

---

## 九、迁移（Alembic 0012）

1. `CREATE TABLE executions` + 索引；`ALTER build_attempts ADD current/latest/successful_execution_id`（nullable）。
2. **不回填** pre-cutover build_attempt 的 execution 行——沿用题案 3 已有边界：迁移瞬间 in-flight 的 attempt 由 reconciler 走 legacy 路径完成本轮，新 claim 才进容器+execution 模型。
3. **retry 语义切换有风险**，建议 cutover 时间戳 / feature flag 守护：flag 关 → 旧 build_attempt-minting；flag 开 → execution-minting。迁移脚本显式记录 cutover 时刻，`one_active_build_per_task` 在过渡期兼容两种判定。

---

## 十、回归测试清单

- retry 在**同一 build_attempt** 下建 execution(iter=2)，workspace 顶层目录不变，上一轮归档进 `attempts/iter-1/`。
- 10 题 × 2 重试 → `work/executions/` 恒为 10 个顶层目录（堆积消失）。
- stale token 的 complete/publish 被 `update_to_terminal` 拒绝，输出留 quarantine。
- lease 过期 reaper 重签 token → 旧进程 publish 被 fence。
- revision execution 从 `attempts/iter-(N-1)/` materialize base-artifact + feedback，不领取无关 shard。
- `one_active_execution_per_attempt` 部分唯一索引：并发双 claim 第二个撞唯一约束失败。
- 同题整轮放弃后 fresh submit → 新 build_attempt 容器 + 新干净目录，`attempt_no` 才 +1。
- 迁移后 in-flight legacy attempt 由 reconciler 走 legacy 路径收尾，不补建 execution。
