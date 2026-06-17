## Why

`GET /api/research/requests/{id}` 当前在单一 handler 里返回 request + runs +
sources + findings + design_tasks（每个 task 还附带 attempts 和 latest_design），
并在循环里做 N+1 查询。前端 `state.detail` 把"研究证据"和"挑战任务"两个生命
周期耦合在一个 polling loop 里，UI 必须在一屏上塞下两组互不相关的工作流。下一
个 change 是 design worker，会继续往这个 handler 里塞 batch / claim / heartbeat
字段；如果现在不拆，后续改动只能在已经耦合的结构上继续叠加，前端视图和后端
endpoint 的复杂度都会指数级增长。

## What Changes

- **BREAKING**: `GET /api/research/requests/{id}` 不再返回 `design_tasks` 字段；
  改为只返回 `design_tasks_summary`（按 status 计数 + total），作为跨页链接的
  元数据。
- 新增 `GET /api/design-tasks?generation_request_id=&status=&limit=` 列表
  endpoint，按 `(generation_request_id, task_no)` 排序。
- 新增 `GET /api/design-tasks/{id}` 详情 endpoint，返回单个任务的完整字段，
  含 `attempts` 数组和 `latest_design` 对象（即原本嵌在 request detail 里的
  per-task 数据）。
- 保留 `POST /api/research/requests/{id}/design-tasks/generate` 作为生成入口
  （生成动作语义上仍属于 research request 上下文）。
- 保留 `POST /api/design-tasks/{id}/queue|archive|design` 三个动作 endpoint，
  路径和语义不变。
- 后端 handler 拆分：research detail handler 不再读 `design_tasks` /
  `design_attempts` / `challenge_designs`，N+1 查询移到新的 design-task 列表
  handler 内并改为一次性 JOIN。
- 前端 sidebar 新增一级导航 *Design Tasks*：
  - 列表视图：跨 request 列出所有 design tasks，支持按 request / status /
    category 过滤。
  - 详情视图：单个 design task 的完整信息（含 attempts、latest_design、操作
    按钮）。
- 前端 research request 详情页只保留 `design_tasks_summary` 摘要卡片，
  附 *"查看 design tasks →"* 链接跳到 Design Tasks 列表（带 request 过滤）。
- 前端 state 拆成 `researchRequests` 和 `designTasks` 两个独立 slice，各自
  拥有独立的 polling cadence。research detail 不再因 design 状态变化触发
  整页重渲染。

## Capabilities

### New Capabilities

（无 —— 本变更只重塑既有 capability 的资源边界，不引入新 capability）

### Modified Capabilities

- `design-task-planning`: design tasks 提升为独立资源；列表和详情走专属
  endpoint；research request detail 不再嵌入完整任务行，改为 summary 元数据。
  （`research-planning` spec 本身没有提及 design tasks，request detail 中
  design tasks 的暴露完全由 design-task-planning 的 "Request detail exposes
  design tasks" requirement 拥有，因此本变更只 delta 这一个 capability。）

## Impact

- **API**: `GET /api/research/requests/{id}` 响应体 BREAKING；新增 2 个
  `/api/design-tasks*` GET endpoints。
- **后端**: `src/web/research_endpoints.py` handler 拆分，N+1 查询从研究详情
  handler 移除；新增 `src/web/design_task_endpoints.py`（或同名分文件）承载
  `/api/design-tasks*`。
- **前端**: `src/web/static/js/views/research-requests.js` 中 design 相关
  渲染函数（`renderDesignTasks` / `renderDesignTasksTable` / `renderDesignPanel`
  / `renderDesignAttempts` / `renderLatestDesign`）整体迁出，新增
  `src/web/static/js/views/design-tasks.js` 列表与详情视图；sidebar/路由配
  置增加新节点；state slice 拆分。
- **测试**: 现有 `tests/app/test_design_task_api.py` 中针对 request detail
  里的 design_tasks 嵌入字段的断言需要重写；新增 design-tasks 列表/详情
  endpoint 测试；前端视图覆盖（如果有）同步调整。
- **OpenSpec 文档**: 修改 `design-task-planning` 和 `research-planning` 两份
  spec 的相关 requirement；本变更归档时这两份 spec 的对应 scenario 必须重写。
- **范围排除**: design worker / batch / claim / heartbeat 仍由 follow-up
  change 承接；本变更只做资源拆分和导航重构，不引入新功能；无数据库 schema
  变更。
