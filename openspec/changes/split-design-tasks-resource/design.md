## Context

`add-design-task-planning` 把 design tasks 作为 research request detail 的内嵌
字段引入：`GET /api/research/requests/{id}` 一次性返回 request + runs +
sources + findings_by_kind + design_tasks，每个 task 还附带 attempts 数组和
latest_design 对象（见 `src/web/research_endpoints.py:355-421`）。后端在循环
里调 `ChallengeDesignRepository.list_attempts(task.id)` 和 `latest_design(task.id)`，
形成 1 + N + N 次查询。前端 `state.detail` 把研究证据和挑战任务两个生命周期
耦合在一个 polling loop 里（`src/web/static/js/views/research-requests.js:97-165`）：
任何一项变化都会触发整个 payload 刷新，795 行的视图文件里混合了 runs / sources
/ findings / design tasks / design panel / attempts / latest_design 共 7 个独立
组件。

下一个 change 是 design worker，它会向 design task 上附加 batch、claim、
heartbeat、lease 等字段。如果这些字段继续走 request detail 这一个 endpoint，
那条 handler 的 SQL 和前端的渲染函数都会进一步膨胀；polling 也会被迫为了刷
worker 心跳而拉回整个 payload。这是必须现在治本的根因。

## Goals / Non-Goals

**Goals:**

- 在 API 边界把 design tasks 拆成独立资源：list / detail / per-task actions
  都不再寄生在 research request endpoint。
- 研究详情 endpoint 只保留 `design_tasks_summary`（counts + total）做跨页
  链接，不再嵌入完整任务行 / attempts / latest_design。
- 后端消除 research detail handler 内的 N+1：design task 的 attempts 与
  latest_design 改为在新的 list/detail handler 里用有界查询批量加载。
- 前端把 design tasks 提升为 sidebar 一级导航，独立的列表 + 详情视图，
  独立的 state slice 与 polling cadence。
- 测试边界与 OpenSpec 文档同步收敛，避免下一个 change（design worker）
  误把新字段塞回 research detail。

**Non-Goals:**

- 不引入 design worker、batch、claim、heartbeat、lease 等任何执行层新功能。
- 不动 PostgreSQL schema（`design_tasks` 表结构、约束、索引保持不变）。
- 不动生成入口语义（`POST /api/research/requests/{id}/design-tasks/generate`
  路径和行为保持不变）。
- 不动 `POST /api/design-tasks/{id}/queue|archive|design` 的路径/语义。
- 不引入 GraphQL / 实时推送 / WebSocket；仍是 REST + 轮询。
- 不实现跨 request 的批量操作 UI（列表支持过滤即可，批量编辑留给后续）。

## Decisions

### 决策 1：BREAKING 改 `GET /api/research/requests/{id}`，不做兼容字段

**选择**：直接删除响应里的 `design_tasks` 字段，加 `design_tasks_summary`。
**备选 A**：保留 `design_tasks` 字段但只放 summary（同名不同结构）。
**备选 B**：保留 `design_tasks` 完整字段，新增 `design_tasks_summary` 共存
一段时间，下个 release 再移除。

**理由**：当前 API 只有一个 dashboard 消费者，没有外部稳定契约；保留同名字
段会让前端代码继续按列表渲染，治本目标失效。共存策略让后端继续做 N+1，恰
恰是本变更要消灭的根因。直接 BREAKING + 同 PR 改前端，一次到位。

### 决策 2：list endpoint 走 `/api/design-tasks?generation_request_id=…`，
不走 `/api/research/requests/{id}/design-tasks`

**选择**：扁平资源路径，request id 作为过滤参数。
**备选**：嵌套路径，强调"design tasks 是 request 的下级资源"。

**理由**：本变更的核心就是解除"design tasks 是 request 的下级资源"这一架
构假设。嵌套路径会把这个错误的所有权关系固化在 URL 里，下一个 change
（design worker 列出所有 queued tasks 不分 request）就要被迫再开扁平 endpoint。
扁平 + 过滤参数同时支持单请求视图和全局视图，避免 URL 翻新。

`generate` 动作保留嵌套路径（`POST /api/research/requests/{id}/design-tasks/generate`），
因为生成动作语义上确实属于"为某个 request 生成 tasks"，不是对 design tasks
集合的操作。

### 决策 3：detail 返回 history；list 保持轻量并禁止 N+1

**选择**：新增一个聚合查询方法，例如
`DesignTaskRepository.get_with_history(task_id) -> (DesignTask, list[DesignAttempt], ChallengeDesign | None)`，
内部用显式 JOIN 或固定数量查询取齐数据；detail endpoint 返回 attempts 与
latest_design。列表 endpoint 不返回 attempts/latest_design，只返回任务行及轻量
展示字段，避免把 request detail 的重量搬到全局列表里。
**备选**：detail handler 顺序调三个 repository 方法
（`get_design_task` → `list_attempts` → `latest_design`）。

**理由**：连续三次 SELECT 看似无害，但 list endpoint 即将面对"N tasks × 3
queries"的场景。建立"history 数据只能由 detail 或显式 history 查询返回"的
边界，list endpoint 就能保持稳定延迟并服务全局视图。

list endpoint 同样要避免 N+1：`list_tasks(filters)` 只查 `design_tasks`，
按 `generation_request_id`、`status`、`category`、`limit` 过滤并固定排序。
如果后续确实需要在列表展示 attempt/latest_design 摘要，必须用固定数量查询
批量加载，不能在循环里按 task 查询。

### 决策 4：前端 state slice 拆分，path-based 路由

**选择**：新增 `state.designTasks = { list: …, listFilters: …, detail: …,
detailId: null }` slice；路由表新增
`#/design-tasks` 和 `#/design-tasks/:id`。
**备选**：复用现有 `state.detail` 结构，仅在 `state.detail.designTasks` 下
增加 list 字段。

**理由**：复用 `state.detail` 等于把耦合移到前端 state 树里，polling 仍会
互相触发。独立 slice 让 design tasks 视图可以在 research detail 关闭时仍
保留自己的过滤状态、滚动位置；polling 也可独立按 status 决定 cadence
（`draft|archived` 慢轮询、`designing|queued` 快轮询，留给后续 design
worker change 落地）。

### 决策 5：research detail 保留 summary card + 跳转链接

**选择**：详情页用 summary card（小尺寸、只显示 counts 和 `View design
tasks →`），路由跳转到 `#/design-tasks?generation_request_id=…`。
**备选**：彻底不在 research detail 提及 design tasks，操作员通过 sidebar
自己找。

**理由**：操作员的实际任务流就是"看完研究 → 生成 design tasks → 切到任务
视图"，研究详情页是这个流程的天然起点，保留链接降低导航成本。Summary 是
轻量级元数据，不会重新引入耦合（后端只是计数，不读 attempts/designs）。

### 决策 6：保留 generate 动作放在 research request endpoint 下

**选择**：`POST /api/research/requests/{id}/design-tasks/generate` 路径不动，
返回的 payload 改成只含新创建的 task ids + count，不再返回完整任务行。前端
拿到 ids 后跳转到 design tasks 列表（过滤到该 request）。
**备选**：把 generate 也搬到 `POST /api/design-tasks?request_id=…`。

**理由**：generate 是把"研究结果"转成"设计任务"的边界动作，语义上属于
research request 上下文（依赖该 request 的 latest run + findings）。URL 反
映这种依赖比纯扁平化更准确。返回 payload 瘦身是为了避免它继续是 N+1 的
温床：调用方需要完整数据时，跳到 list endpoint 再查一次即可（操作员体验上
是一次跳转，不增加感知延迟）。

### 决策 7：跨页 summary 不依赖跨表 JOIN

**选择**：`design_tasks_summary` 用一条
`SELECT status, COUNT(*) FROM design_tasks WHERE generation_request_id = ? GROUP BY status`
计算。
**备选**：缓存在 `generation_requests` 表的列里，每次状态变化触发更新。

**理由**：summary 查询走索引
`ix_design_tasks_generation_request_status`（已存在），数据量上限是单
request 的 target_count（默认 ≤ 50），延迟可忽略。缓存列引入"状态机变更
要记得改两处"的维护负担，得不偿失。

### 决策 8：list endpoint 暴露 category 过滤参数

**选择**：`GET /api/design-tasks` 支持 `category` 过滤，与 UI sidebar 设计
一致；实现上直接过滤 `design_tasks.category`。
**备选**：只支持 request/status 过滤，把 category 留给前端本地过滤。

**理由**：Design Tasks 是跨 request 的全局视图，操作员自然会按题型分类扫描。
`category` 已经在 `design_tasks` 表中作为 shard-compatible 字段持久化，服务端
过滤不需要额外 join，也能避免大列表先全量下发再由浏览器过滤。

## Risks / Trade-offs

- **[Risk] BREAKING 改动会破坏外部脚本/工具**
  → Mitigation：当前 API 没有外部消费者；同 PR 内修复前端唯一调用者；
    archive 时在 spec deltas 里显式标记 BREAKING；提交 PR 描述里列出变更字段。

- **[Risk] 拆分后操作员需要多走一步导航**
  → Mitigation：research detail 保留 summary card + 跳转链接，常见路径只多
    一次点击；Design Tasks sidebar 是常驻入口，重度操作员可直接绕开 research
    detail。

- **[Risk] 前端 state 拆分可能引入新的状态同步 bug**（例如生成 tasks 后 summary
  card 没刷新）
  → Mitigation：summary card 在打开 research detail 时按 detail 的 polling
    cadence 刷新；generate 成功的回调显式触发 summary refetch；新增端到端
    场景覆盖：generate → summary 更新 → 跳转列表显示新行。

- **[Risk] history 查询的 SQL 复杂度上升**
  → Mitigation：list endpoint 保持轻量，只读 `design_tasks`；detail endpoint
    面对单 task history，用显式 JOIN 或固定数量查询，测试断言不会退回 per-row
    N+1。为 `design_attempts.design_task_id` 与
    `challenge_designs.design_task_id` 检查/补充覆盖索引（如果尚未存在）。

- **[Risk] 文档漂移：`add-design-task-planning` 仍未 archive，本变更修改的
  requirement 在 `openspec/specs/` 里还不存在**
  → Mitigation：在归档顺序上要求 `add-design-task-planning` 先 archive；
    本变更的 delta 引用的是 add-design-task-planning archive 后写入
    `specs/design-task-planning/spec.md` 的内容，文本一致；archive 前再次
    比对 requirement 标题，确保 MODIFIED 能精确匹配。

- **[Trade-off] generate 返回 payload 瘦身**：调用方拿不到完整任务行，必须
  跟一次 list 请求。
  → 理由：避免 generate handler 成为新的 N+1 热点；多一次往返对操作员体验影响
    可忽略（generate 是低频动作）。

## Migration Plan

1. **归档前置**：先把 `add-design-task-planning` archive 到
   `openspec/specs/design-task-planning/`（用 `openspec-archive-change` /
   `opsx:archive`），让本变更的 delta 有正式的 base spec 可 modify。
2. **后端实现**：
   - 在 `DesignTaskRepository` 新增 `get_with_history(task_id)` 和
     `list_tasks(filters)`。
   - 新增 `src/web/design_task_endpoints.py`（或同名挂在
     `research_endpoints.py` 之外）注册 `/api/design-tasks` 两个 GET。
   - 修改 `_register_request_detail` 改为只返回 `design_tasks_summary`；
     删除 handler 内的 `attempts_by_task` / `latest_design_by_task` 循环。
   - 调整 `_register_design_task_endpoints` 的 generate handler 返回精简
     payload。
3. **前端实现**：
   - 新增 `src/web/static/js/views/design-tasks.js`（list + detail）。
   - 路由表注册 `#/design-tasks` 与 `#/design-tasks/:id`；sidebar 加入口。
   - 从 `research-requests.js` 移出 `renderDesignTasks` /
     `renderDesignTasksTable` / `renderDesignPanel` /
     `renderDesignAttempts` / `renderLatestDesign` 等函数。
   - research detail 增加 summary card 与跳转链接。
   - 拆出 `state.designTasks` slice，迁移 generate / queue / archive /
     design 动作的回调。
4. **测试**：
   - 重写 `tests/app/test_design_task_api.py` 中针对 request detail 的断言。
   - 新增 `tests/app/test_design_task_list_endpoint.py`、
     `tests/app/test_design_task_detail_endpoint.py`。
   - 新增 repository 测试覆盖 `get_with_history` / `list_tasks`，
     断言 SQL round-trip 数量（用 `sqlalchemy.event` 或会话统计）。
5. **回滚**：本变更纯 API + 前端层，无 schema/数据迁移，回滚 = 还原 PR。

## Open Questions

- summary card 是否要显示 *"queued: 3 / 10"* 这种"非终态/总数"的占比？默认
  方案是按 status counts 全部列出，留给 UI 决定展示形式；不写进 spec。
- list endpoint 排序键是固定 `(generation_request_id, task_no)` 还是支持
  `?sort=...`？倾向先固定，后续真有跨 request 排序需求再加。
