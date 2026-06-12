## Why

现有仪表盘是 162 行 HTML + 458 行 vanilla JS + Tailwind CDN，6 个 view 全部"表格 + 列表"风格，感观是"小 AI 工具"而非教育产品。三个阻碍把它当作平台对外展示：

1. **核心能力只有"题目生成器"暴露**。未来要加情景生成、学习资料、学习路线三条能力，当前 IA 没有"能力（capability）"这个一级实体。
2. **Hermes LLM provider 配置只能手编 `~/.hermes/config.yaml` 与 `auth.json`**。运营无法在 UI 切 provider、改 base_url、轮换 API key，门槛高且易写坏。
3. **状态只能 5 秒轮询，没有实时推送**。shard 跑到哪一步、validate 是否通过无动效反馈，与"AI 产品"感知背道而驰。

本变更升级前端技术栈与信息架构，把"能力"作为一级模型沉下来，把 LLM 配置变成 UI 一等公民，并用 SSE 替代轮询。

## What Changes

借鉴 Linear、Anthropic Console、Vercel Dashboard 的范式；不引入认证、多租户、计费、暗黑模式、移动端响应式精修。

- **前端技术栈迁移到 Vue 3 + Vite + TypeScript + Tailwind + shadcn-vue**。新增 `frontend/` 工作区，包含 `vue@3`、`vite@5`、`typescript`、`tailwindcss`、`shadcn-vue`、`pinia`、`vue-router@4`、`@tanstack/vue-query`、`lucide-vue-next`、`@vueuse/core`、`monaco-editor`。开发模式 `npm run dev` 通过 vite proxy 把 `/api/*` 转发到 FastAPI；生产构建产物落到 `src/web/static/dist/`；`pyproject.toml` `package-data` 追加该目录。
- **删除旧前端文件**：`src/web/static/index.html` 与 `src/web/static/app.js` 删除；`src/web/server.py` 新增 SPA fallback（非 `/api/*` 与非 `/static/*` 的 GET 返回 `dist/index.html`），`/static/dist/assets/*` 1 年缓存头。
- **设计系统**：6 色语义调色板（success/warning/danger/info/neutral/accent）通过 Tailwind theme.extend 注入；Inter + JetBrains Mono 字体自托管；4 档字号；8px 间距网格；3 档圆角（4/8/12）。
- **基础组件层**：按 shadcn-vue 模式把组件复制到 `frontend/src/components/ui/`：Button、Card、Badge、Skeleton、EmptyState、Toast、Dialog、Sheet、Tabs、Tooltip、DropdownMenu、Command（Cmd-K）、ProgressBar、Sparkline、GanttRow。Skeleton 强制覆盖所有数据加载态；空状态强制使用 EmptyState 组件（SVG 插图 + 标题 + 描述 + 主 CTA）。
- **信息架构**：顶层导航 7 个分组——Overview / Generate（enabled）/ Scenario（coming soon）/ Learning（Materials + Paths，coming soon）/ Operate（Queue/Workers/Logs）/ Quality（Lint/Diversity，本变更显示但 disabled，等 Phase 1）/ Settings（LLM Provider/Generation Profile）。顶部栏含 workspace label、面包屑、Cmd-K 按钮、通知铃铛（SSE 推送的 toast 历史 ≤50 条）、帮助下拉。
- **能力（capability）模型**：`GET /api/capabilities` 返回固定 4 条 `{id, name, status: enabled|coming_soon|disabled, description, icon, route}`。Overview 渲染 4 张 capability tile；coming_soon 项可点击跳到 placeholder 页（SVG 插图 + 文案），不返回 404。
- **新增页面**：Overview、Generate（New Run 三栏 compose、Runs 列表、Run detail 六 tab、Challenge detail 六 tab）、Scenario placeholder、Learning placeholder、Operate（Queue/Workers/Logs）、Settings（LLM Provider 新功能、Generation Profile Monaco 编辑器）。
- **LLM Provider 配置（新功能）**：
  - 后端 `src/domain/llm_settings.py`：`load_settings` 合并读 `~/.hermes/config.yaml` 与 `auth.json`，API key mask 成 `sk-***1234`；`save_settings` 原子写回保留无关字段；`test_connection` 用当前生效配置发起最小探测请求返回 `{ok, latency_ms, model, error}`。
  - 后端 `src/web/api/llm.py`：`GET /api/settings/llm`、`PUT /api/settings/llm`（api_key 等于 mask 占位符则不覆盖）、`POST /api/settings/llm/test`。
  - 前端 `/settings/llm`：provider 下拉（anthropic/openai/glm/custom）、base_url、api_key（password input + show toggle）、model（下拉 + 自定义）、Test connection 按钮、保存。
  - **安全硬约束**：API key 永不出现在 JSON 响应、日志、SSE 消息、错误堆栈中；只能 mask。
- **实时事件流（SSE）**：`src/web/sse.py` 提供 `GET /api/events/stream`，使用 `text/event-stream` 协议；服务端轮询 `progress_events` 增量推给所有连接；15 秒心跳 `:heartbeat`；响应头加 `X-Accel-Buffering: no` 防 nginx 缓冲。客户端 Vue composable `useEventStream()` 退避重连（1s/2s/4s）。旧的 `/api/state` 等轮询端点契约保留。
- **后端 API 扩展**：`src/web/api/` 模块新增 `capabilities.py`、`runs.py`（`/api/runs`、`/api/runs/{shard}`、`/api/runs/{shard}/challenges/{id}`、`/api/runs/{shard}/artifacts/{path:path}`，路径校验防 traversal）、`kpis.py`（`/api/kpis` 返回 Overview 4 张卡指标）、`llm.py`、`presets.py`（`work/presets.json` 持久化 New Run saved presets）。`server.py` 调整路由挂载顺序（先 `/api/*` 后 SPA catch-all）。
- **新依赖**：Python 端加 `pyyaml`（解析 `.hermes/config.yaml`），前端用 npm 管理。
- **文档**：README 新增 "Frontend development" 段；`docs/architecture.md` 加 "Frontend stack" 边界图；仓库根新增 `.nvmrc`（Node 20 LTS）；新增 `Makefile` 包装 `ui-dev` / `ui-build` / `ui-test`。

## Capabilities

### New Capabilities

- `web-console`: 操作员通过浏览器使用本平台的全部 UX 契约，涵盖技术栈选型、设计系统约束、信息架构、能力（capability）展示模型、LLM provider 配置、实时事件流、页面 IA 与基础组件。

### Modified Capabilities

（无）—— `delivery-bundle` / `challenge-seed-management` / `re-target-platforms` / `module-architecture` / `hermes-execution-protocol` 五条主规范契约不变。

## Impact

- **代码新增**：`frontend/` 工作区（Vue 3 + Vite + TS + Tailwind + shadcn-vue）；`src/domain/llm_settings.py`；`src/web/api/`（多个新模块）；`src/web/sse.py`；新增 SVG 插画 4 张；4 个新后端测试模块；前端 Vitest 测试。
- **代码删除**：`src/web/static/index.html`、`src/web/static/app.js`。
- **代码修改**：`src/web/server.py` 路由挂载与 SPA fallback；`src/web/dashboard.py` 保留现有 worker 子进程逻辑不变；`pyproject.toml` 加 `pyyaml` 依赖 + `package-data` 追加 `web/static/dist/**`；`README.md` 新增 frontend 段；`docs/architecture.md` 新增 frontend 边界图。
- **构建工具链**：引入 Node.js 20 LTS 作为构建依赖。CI 与本地需要 `npm install + npm run build`；构建产物 commit 到 git 以便纯 Python 使用者 `uv sync` 后直接 `challenge-factory serve`。
- **运行时**：FastAPI 服务的 API 表面向上扩展（新增路由）；既有 `/api/state` 等保留契约不变；新增 SSE 长连接对反向代理的缓冲配置要求文档化。
- **运维**：Hermes LLM provider 配置文件 `~/.hermes/config.yaml` 与 `auth.json` 现在可被 UI 写入，需在文档中明示这是写入路径，避免与手工编辑冲突。
- **风险点**：构建产物体积、SSE 在反向代理下的缓冲、LLM API key 防泄漏、Node 工具链引入的入门门槛、placeholder 页面误认线上故障——已在 design 与 spec 中分别钉死。
- **回滚**：本变更前端文件 commit 在 git；回滚仅需 `git revert` 即可恢复旧 dashboard。后端新增 API 与旧 API 共存，无破坏性。
