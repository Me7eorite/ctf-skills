# 挑战工厂 (Challenge Factory) — 中文项目理解手册

> 本手册面向中文开发者，系统性地解释项目的架构、模块职责、核心流程和关键概念。
> 配合英文源码阅读，可大幅降低理解门槛。

---

## 目录

- [一、项目概述](#一项目概述)
- [二、核心概念术语表](#二核心概念术语表)
- [三、分层架构全景图](#三分层架构全景图)
- [四、各层模块详解](#四各层模块详解)
  - [4.1 core/ — 核心工具层](#41-core--核心工具层)
  - [4.2 persistence/ — 持久化层](#42-persistence--持久化层)
  - [4.3 domain/ — 领域逻辑层](#43-domain--领域逻辑层)
  - [4.4 hermes/ — AI 代理交互层](#44-hermes--ai-代理交互层)
  - [4.5 services/ — 服务编排层](#45-services--服务编排层)
  - [4.6 packing/ — 打包导出层](#46-packing--打包导出层)
  - [4.7 web/ — Web 接口层](#47-web--web-接口层)
- [五、核心流程解读](#五核心流程解读)
  - [5.1 Research 研究流程](#51-research-研究流程)
  - [5.2 Challenge Design 题目设计流程](#52-challenge-design-题目设计流程)
  - [5.3 打包导出流程](#53-打包导出流程)
- [六、数据模型速览](#六数据模型速览)
- [七、CLI 命令行参考](#七cli-命令行参考)
- [八、关键类/函数速查表](#八关键类函数速查表)

---

## 一、项目概述

**Challenge Factory** 是一个自动化 CTF（Capture The Flag）题目生成工厂。它的核心工作流是：

> **研究 (Research)** → AI 搜集选题资料 → **设计 (Design)** → **构建 (Build)** → AI 生成题目代码/文档 → **打包 (Pack)** → 输出可部署的比赛题目包

整个流程由 AI 模型驱动（通过 Hermes 子进程调用），配合 PostgreSQL 持久化和文件队列，支持多 Worker 并行执行。

---

## 二、核心概念术语表

| 英文术语 | 中文解释 | 所在模块 |
|---------|---------|---------|
| **shard** | 分片。将多个挑战按类别分组成小批次，每个批次是一个 .json 文件。Worker 以 shard 为单位认领和加工。 | `core/queue.py` |
| **claim** | 认领。Worker 从 pending 目录拿走一个 shard，移到 running 目录，防止其他 Worker 重复处理。 | `core/queue.py` |
| **lease** | 租约。Research 流程中，Worker 获得 run 后的有效时间窗口。超时后其他 Worker 可恢复该 run。 | `services/research_job_service.py` |
| **claim_token** | 认领令牌。Research run 被 claim 时生成的 UUID，用于后续写入时的悲观锁校验（token-fencing）。 | `services/research_job_service.py` |
| **token-fencing** | 令牌栅栏。一种并发控制策略：只有持有最新 claim_token 的 Worker 才能修改 run 状态，防止并发冲突。 | `services/research_job_service.py` |
| **Hermes** | AI 代理子进程。项目通过调用外部 Hermes CLI 工具来与大模型交互，生成题目设计和代码。 | `hermes/` |
| **seed** | 种子。人工编写的题目原型数据，包含 id、标题、难度、类别等。AI 在此基础上展开设计。 | `domain/seeds.py` |
| **challenge_id** | 题目唯一标识符，形如 `web-0001`（类别-序号）。 | 全局使用 |
| **progress event** | 进度事件。每个 shard 执行过程中的状态变更记录，如 `design passed`、`implement running`。 | `core/state.py` |
| **stage** | 阶段。进度管线的七个阶段：`queued → design → implement → build → validate → document → complete` | `core/state.py` |
| **status** | 状态。每个阶段的四种可能结果：`pending`、`running`、`passed`、`failed` | `core/state.py` |
| **resume** | 续跑/断点恢复。如果之前的 shard 执行中断，resume 机制可以跳过已完成的阶段，从断点继续。 | `domain/resume.py` |
| **contract** | 合约。metadata.json 中字段的期望值和格式要求，如 category、architecture、files 必须满足约定。 | `domain/validation.py` |
| **reference solve** | 参考解题。题目生成后附带的官方解题脚本，需要执行验证以确认题目可解。 | `domain/validation.py` |
| **matrix** | 题目矩阵。一个 JSONL 格式的文件，每行是一个题目的属性数据（id、标题、难度等），用于批量生成。 | `core/queue.py` |
| **run** | 运行实例。Research 流程中，每个 generation_request 可以有多次 run（支持重试）。 | `persistence/models/research.py` |
| **attempt** | 尝试次数。run 的第 N 次尝试。每次失败后可重新 claim 再次尝试，最多 max_attempts 次。 | `services/research_job_service.py` |
| **gate model** | 质量门。判断题目质量是否合格的标准（代码是否存在、文件是否齐全、reference solve 是否通过等）。 | `hermes/validation.py` |
| **packing** | 打包。将生成完毕的题目文件组织成最终可交付的 zip/tgz 包和 PDF 文档。 | `packing/` |

### 阶段与进度的计算规则

进度百分比由 `_percent(stage, status)` 函数计算（位于 `core/state.py`）：

| 阶段 (stage) | pending | running | failed | passed |
|-------------|---------|---------|--------|--------|
| queued (排队) | 0% | 5% | 8% | 16% |
| design (设计) | 8% | 21% | 24% | 32% |
| implement (实现) | 24% | 37% | 40% | 48% |
| build (构建) | 40% | 53% | 56% | 64% |
| validate (校验) | 56% | 69% | 72% | 80% |
| document (文档) | 72% | 85% | 88% | 96% |
| complete (完成) | 88% | — | 99% | 100% |

规则：passed = 下一阶段的起始，failed = 本阶段完成一半，pending = 上一阶段 passed 扣 8%。

---

## 三、分层架构全景图

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI / Web API 入口层                       │
│  src/cli.py          src/web/server.py                            │
├─────────────────────────────────────────────────────────────────┤
│                      services/ — 服务编排层                        │
│  ResearchAgentExecutor   DesignAgentExecutor                      │
│  ResearchJobService      DesignTaskPlanningService                │
│  ResearchWorker          ChallengeDesignService                   │
├─────────────────────────────────────────────────────────────────┤
│                      hermes/ — AI 代理交互层                       │
│  runner.py (shard执行)   design.py (设计服务)                      │
│  process.py (子进程)     prompt.py (提示词)                        │
│  validation.py (质量门)  progress.py / report.py                   │
├───────────────────────┬─────────────────────────────────────────┤
│   domain/ — 领域逻辑层  │          packing/ — 打包导出层             │
│   research.py          │          packer.py (主打包器)              │
│   challenge_designs.py │          pdf.py (PDF渲染)                 │
│   validation.py        │          docker.py (Docker导出)            │
│   resume.py (断点续跑)  │          archive.py (zip/tgz)             │
│   seeds.py (种子管理)   │          workbooks.py (Excel报表)          │
├───────────────────────┴─────────────────────────────────────────┤
│                   persistence/ — 持久化层                           │
│  engine.py (数据库引擎) session.py (事务管理)                       │
│  models/ (ORM模型)     repositories/ (仓库模式)                     │
├─────────────────────────────────────────────────────────────────┤
│                       core/ — 核心工具层                           │
│  paths.py (路径管理)  queue.py (文件队列)  state.py (进度协议)       │
│  jsonio.py (JSON IO)  docker.py (Docker工具)                      │
└─────────────────────────────────────────────────────────────────┘
```

**依赖方向**: 上层依赖下层，下层不依赖上层。`core/` 被所有人依赖，`web/` 依赖所有人。

---

## 四、各层模块详解

---

### 4.1 core/ — 核心工具层

> 最底层的基础设施，提供文件路径、JSON 读写、进度协议、文件队列等基础能力。

#### `core/paths.py` — 项目路径管理

定义 `ProjectPaths` 类，统一管理项目的所有目录结构：

```python
class ProjectPaths:
    work_root           # → work/
    shards              # → work/shards/        (分片数据)
    challenges          # → work/challenges/    (生成的题目)
    seeds               # → work/seeds/         (种子数据)
    reports             # → work/reports/       (报告输出)
    dashboards          # → work/dashboards/    (看板数据)
    packing             # → work/packing/       (打包输出)
    # ... research 相关路径 ...
```

**关键点**: 所有路径由 `WORK_ROOT` 环境变量或默认值派生，保证开发和部署环境一致性。

#### `core/state.py` — 进度常量与协议

定义了整个系统的进度模型：

- **`STAGES`** — 七个执行阶段的元组
- **`STATUSES`** — 四种可能状态
- **`ProgressEventInput`** — 进度事件的数据结构（dataclass）
- **`ProgressStore`** — 进度存储的抽象协议（Protocol），定义了 `record()`、`events_for_shard()` 等方法
- **`InMemoryProgressStore`** — 内存实现，用于测试和单进程场景
- **`_percent(stage, status)`** — 阶段+状态 → 百分比的计算函数

**设计亮点**: 使用 Python Protocol 而非 ABC 定义接口，两个实现在运行时鸭子类型检查，无需显式继承。

#### `core/queue.py` — 文件队列

基于文件系统的原子操作实现的分片队列，核心类 `ShardQueue`：

- **`claim(worker)`** — 用 `Path.replace()`（原子 mv）认领一个 pending 分片，避免并发竞争
- **`complete(shard, state)`** — 将分片移动到 done/ 或 failed/
- **`requeue(name, state)`** — 将失败或运行中的分片移回 pending/

**并发安全设计**: 依赖文件系统的原子 rename 操作，无需锁。多个 Worker 同时 claim 时，只有一个能成功（先到先得）。

#### `core/jsonio.py` — JSON 读写封装

简单的 `read_json()`、`write_json()`、`read_jsonl()` 工具，统一了编码和错误处理。

#### `core/docker.py` — Docker CLI 封装

`image_exists(image)` — 检查本地 Docker 镜像是否存在，隔离了 subprocess 调用。

---

### 4.2 persistence/ — 持久化层

> 基于 SQLAlchemy + PostgreSQL 的数据持久化，使用仓库模式和短事务上下文管理器。

#### `persistence/engine.py` — 数据库引擎

- 从环境变量 `DATABASE_URL` 读取 PostgreSQL 连接串
- 自动加载项目根目录的 `.env` 文件（依赖 python-dotenv）
- 创建启用了 `pool_pre_ping=True` 的 SQLAlchemy 引擎（自动重连）

#### `persistence/session.py` — 事务管理

`transaction()` 上下文管理器是**数据库操作的标准入口**：

```python
with transaction(factory=session_factory) as session:
    repo = SomeRepository(session)
    repo.do_something()
    # 上下文退出时自动 commit 或 rollback
```

**内部机制**:
1. 进入时创建 session
2. `yield session` 交给调用方
3. 正常退出 → `commit()`
4. 异常退出 → `rollback()`
5. `finally` → `close()`

#### `persistence/models/` — ORM 模型

| 模型文件 | 对应的表 | 说明 |
|---------|---------|------|
| `base.py` | (基类) | SQLAlchemy DeclarativeBase |
| `progress.py` | `progress_events`, `progress_snapshots` | 进度事件和看板快照 |
| `research.py` | `generation_requests`, `research_runs`, `research_findings`, 等 | Research 流程的全部数据 |
| `challenge_designs.py` | `challenge_designs`, `design_versions` | 题目设计和版本管理 |
| `design_tasks.py` | `design_tasks`, `design_runs` | 设计任务和运行记录 |

#### `persistence/repositories/` — 仓库模式

每个 Repository 封装了对特定表的 CRUD 操作。关键设计：
- Repository 接收 `Session` 作为构造参数（不自己管理事务）
- 业务方法返回 DTO（数据传输对象）而非 ORM 对象，避免 session 泄漏

---

### 4.3 domain/ — 领域逻辑层

> 纯业务逻辑，不依赖数据库或外部系统。包含题目校验、断点恢复、种子管理等。

#### `domain/validation.py` — 题目校验器

`ChallengeValidator` 是题目质量的核心验证类：

- **`validate(challenge_ids)`** — 批量验证指定的题目
- **`validate_one(path)`** — 验证单个题目目录
- **`contract_errors(metadata, challenge_dir)`** — 检查 metadata.json 字段是否满足约定（contract）
- **`reference_solve_errors(path, timeout)`** — 执行参考解题脚本，验证题目可解
- **`elf_machine(path)`** — 解析 ELF 可执行文件的架构（x86_64/arm64/arm）
- **`is_elf(path)`** — 判断文件是否为 ELF 格式

**约定检查项** (contract):
- metadata 必需字段（id, title, difficulty, build_status, flag）
- category 字段是否属于合法类别
- 声明的架构与实际 ELF 文件架构是否匹配
- 容器化题目必须声明 port
- 题目目录下必须包含的文件（challenge 文件、flag.txt 等）

#### `domain/challenge_designs.py` — 题目设计 DTO

定义题目设计的数据结构：`ChallengeDesign`、`DesignVersion`、`DesignContract` 等。

#### `domain/design_tasks.py` — 设计任务 DTO

定义设计任务的运行状态：`DesignTask`、`DesignRun`、任务状态枚举等。

#### `domain/research.py` — Research DTO

定义 Research 流程的数据结构：
- `GenerationRequest` — 一次生成请求
- `ResearchRun` — 一次运行实例
- `ResearchFinding` — 一个研究发现的资料条目
- `DifficultyDistribution` — 难度分布配置

#### `domain/resume.py` — 断点恢复

当 shard 执行发生中断时，`resume` 机制允许从断点继续，而不是从头开始。核心逻辑：
- `compute_resume_plan(shard, events)` — 分析已有进度事件，生成恢复计划
- `ShardResumePlan` — 包含「哪些阶段已完成」「当前应在哪个阶段」
- `carry_forward_message()` — 生成「从此阶段继续」的状态消息

**典型场景**: shard 在执行 validate 阶段时崩溃，resume 识别出 design/implement/build 已 passed，直接从 validate 重新开始。

#### `domain/seeds.py` — 种子管理

种子是人工编写的题目原型数据：
- `SeedStore` — 种子的加载、保存、校验、入队
- `validate_seed()` — 校验种子数据字段是否合法（strip、lowercase 等规范化）
- `split_challenges()` — 将种子按类别分组成 shard

#### `domain/metrics.py` — 执行指标

从进度事件中计算各类指标：
- `duration_breakdown(events)` — 计算每个阶段的耗时
- `stage_timeline(events)` — 生成时间线数据

#### `domain/reports.py` — 报告合并

`merge_reports()` — 将多个 shard 的报告合并为一份总报告。

---

### 4.4 hermes/ — AI 代理交互层

> 通过子进程调用 Hermes CLI 与 AI 模型交互，处理 prompt 渲染、结果验证和质量门控制。

#### `hermes/process.py` — 子进程管理

模块级的子进程工具，**不依赖 ProjectPaths**，所有路径由调用方传入：

- **`hermes_arguments()`** — 解析 Hermes CLI 的可执行文件路径和参数
  - 优先级：`HERMES_CMD` 环境变量 > `which hermes` > `uvx` 安装 > 默认值
- **`invoke(arguments, cwd, env, log_path)`** — 简单执行（不捕获输出）
- **`invoke_capture(...)`** — 执行并捕获 stdout/stderr，支持超时和取消
  - 使用双线程分别读取 stdout 和 stderr（`_drain` 函数）
  - 支持 `cancel_event` (threading.Event) 外部取消
  - 超时/取消时先 SIGTERM 后 SIGKILL（`_terminate` 函数）
- **`HermesProcessResult`** — 返回结果：returncode、stdout、cancelled 标志
- **`profile_exists(name)`** — 检查 Hermes profile 是否存在

**线程模型**: 子进程执行在后台线程监控（非主线程），stdout/stderr 各有独立 drain 线程。

#### `hermes/prompt.py` — 提示词渲染

- `render_prompt()` — 渲染 shard 执行用的 prompt 模板
- `render_research_prompt()` — 渲染 Research 用的 prompt 模板

模板文件位于项目的 `prompts/` 目录下，包含占位符变量。

#### `hermes/runner.py` — Shard 执行器

`HermesRunner` 是 shard 执行流程的核心类，负责一个 shard 从 queued 到 complete 的完整生命周期：

1. **claim stage** — 认领 shard，写入 `queued running` 事件
2. **design stage** — 调用 Hermes 生成题目设计
3. **implement stage** — 调用 Hermes 生成题目代码
4. **build stage** — 调用 Hermes 构建并测试
5. **validate stage** — 官方校验器运行（调用 `ChallengeValidator`）
6. **document stage** — 生成文档
7. **complete** — 标记完成

每个阶段都有：
- 进度事件写入
- 断点恢复支持（如果上轮运行中断）
- 错误处理和重试机制

#### `hermes/validation.py` — 质量门校验

- `run_validation()` — 执行领域层的挑战校验
- `validate_gate()` — 质量门判断（当前阶段是否满足继续条件）
- `record_per_challenge_complete()` — 记录每个题目的完成状态

#### `hermes/design.py` — 设计服务函数

供 Challenge Design 流程使用的 Hermes 子进程调用。

#### `hermes/progress.py` / `hermes/report.py` — 进度报告

`update_report()` 和 `ensure_report()` — 在每个阶段完成后更新 JSON 报告文件。

---

### 4.5 services/ — 服务编排层

> 将 domain、hermes、persistence 层的能力编排成完整的业务流程。

#### `services/research_job_service.py` — Research 任务队列

**`ResearchJobService`** 管理 Research run 的状态机：

- **`submit_request()`** — 提交一个新的研究请求（同时创建 generation_request 和首个 run）
- **`claim_next_run()`** — 认领下一个 queued 的 run（含过期恢复）
  - **token-fencing**: 生成新的 `claim_token`，后续写入必须携带此 token
  - **lease**: 设置租约过期时间，防止 Worker 崩溃后 run 永远卡住
  - **expired recovery**: 恢复已过期 lease 的 run，重置为 queued
- **`complete_run_with_results()`** — 将一个 research run 标记为完成，写入 findings 和 sources
- **`abort_run()`** — 终止 run（带 token-fencing 校验）

**并发控制核心** — token-fencing + lease：

```
Worker A claim run → 获得 claim_token_A + lease_30s
Worker B 尝试 claim → 发现 lease 未过期，跳过
(30 秒后如果 Worker A 没有 complete)
Worker B claim → 恢复过期 run → 获得 claim_token_B + 新 lease
Worker A 尝试 complete → claim_token_A ≠ claim_token_B → 被拒绝！
```

这保证了即使在分布式环境下，同一时间只有一个 Worker 能修改 run 状态。

#### `services/research_agent_executor.py` — Research 执行器

**`ResearchAgentExecutor`** 负责执行一个已被 claim 的 research run：

1. 启动心跳线程（`heartbeat`），每 30 秒续约 lease
2. 调用 Hermes 执行 research prompt
3. 解析 Hermes 输出，提取 findings 和 sources
4. 调用 `complete_run_with_results()` 持久化结果

**取消机制**: 通过 `threading.Event` 实现优雅取消——心跳线程检测到取消信号后停止续约，主线程在 Hermes 子进程中设置 cancel_event。

#### `services/research_worker.py` — Research Worker

**`ResearchWorker`** 是一个可以持续运行的 Worker 进程：

- `run()` — 主循环：claim run → execute → repeat
- `run_single(run_id)` — 执行特定的 run
- 支持 SIGTERM 优雅退出（转换为 KeyboardInterrupt）
- 包含 backoff 和健康检查逻辑

#### `services/design_agent_executor.py` — 设计执行器

**`DesignAgentExecutor`** 类似 Research，但执行的是题目设计（design）run。

#### `services/design_task_planning_service.py` — 设计任务规划

**`DesignTaskPlanningService`** — 管理设计任务的生命周期，将生成请求拆分为多个可并行的设计任务。

#### `services/challenge_design_service.py` — 题目设计服务

**`ChallengeDesignService`** — 管理单个题目的设计方案、版本和状态流转。

#### `services/design_prompt.py` — 设计提示词

渲染设计流程专用的 prompt 模板。

---

### 4.6 packing/ — 打包导出层

> 将已生成的题目文件打包成可交付的格式。

#### `packing/packer.py` — 主打包器

**`Packer`** 类将 `build_status = "passed"` 的题目打包：

```python
class Packer:
    def pack_everything(self)        # VIP 打包（全量）
    def pack_public(self)            # 观众打包（不含写死flag的题目）
    def pack_onsite_finalist(self)   # 决赛选手打包
```

支持的类别映射 (`CATEGORY_PREFIXES`): crypto/web/pwn/re/reverse/misc/stego/forensics/ics/ai/cloud/mobile/blockchain/iot/auto/data/malware/osint

#### `packing/layout.py` — 目录布局

`_create_layout()` — 按类别和题目创建标准化的输出目录结构。

#### `packing/archive.py` — 归档格式

生成 zip 和 tgz 归档文件，支持选择性排除（如选手包排除官方解题文档）。

#### `packing/docker.py` — Docker 镜像导出

- `_is_containerized()` — 判断题目是否为容器化部署
- `_save_docker()` — 导出 Docker 镜像为 tar 文件
- `_should_emit_enclosure()` — 是否生成附页（如二维码、链接等）

#### `packing/pdf.py` — PDF 渲染

`_render_pdf()` — 将题目描述渲染为 PDF 文件（用于打印分发）。

#### `packing/workbooks.py` — Excel 报表

`_write_workbook()` — 生成挑战列表 Excel 文件，包含题目名称、类别、难度等。

#### `packing/selector.py` — 题目选择

`_selected_challenges()` — 筛选 `build_status = "passed"` 的题目用于打包。

---

### 4.7 web/ — Web 接口层

> 基于 FastAPI 的 Web Dashboard，提供实时进度查看和管理操作。

#### `web/server.py` — 服务器启动

- `create_app(service)` — 创建 FastAPI 应用
- `serve()` — 启动 uvicorn 服务器
- 注册了以下路由：

| 路径 | 方法 | 功能 |
|------|------|------|
| `/api/state` | GET | 获取当前全局状态 |
| `/api/logs/{name}` | GET | 获取日志文件内容 |
| `/api/actions/worker` | POST | 手动触发 worker 运行 |
| `/api/actions/validate` | POST | 手动触发校验 |
| `/api/seeds` | POST | 提交新的题目种子 |
| `/api/seeds` | GET | 列出所有种子 |
| `/api/research/...` | 多项 | Research 流程相关接口 |
| `/api/design-tasks/...` | 多项 | 设计任务相关接口 |
| `/api/build-attempts/...` | 多项 | 构建尝试列表、详情、重试接口 |

#### `web/dashboard.py` — 看板服务

`DashboardService` 聚合进度数据、日志、种子管理，为前端提供统一的数据接口。

#### `web/research_worker_manager.py` — Worker 管理器

`ResearchWorkerManager` — 管理多个 Research Worker 的启停和状态监控。

#### `web/research_endpoints.py` / `web/design_task_endpoints.py`

注册 Research 和 Design Task 的 REST API 端点。

---

## 五、核心流程解读

---

### 5.1 Research 研究流程

**目的**: AI 自动搜集某个类别（如 Web 安全）下特定主题的研究资料，生成 "findings"（研究条目）供后续题目设计使用。

```
用户/CLI                        JobService                    AgentExecutor                 Hermes子进程
  │                                │                               │                            │
  │ submit_request(category, topic)│                               │                            │
  ├───────────────────────────────>│                               │                            │
  │                                │ INSERT generation_request     │                            │
  │                                │ INSERT research_run (queued)  │                            │
  │<─────── (request, run) ────────┤                               │                            │
  │                                │                               │                            │
  │ claim_next_run(agent_id, lease)│                               │                            │
  ├───────────────────────────────>│                               │                            │
  │                                │ SELECT + FOR UPDATE (token-fenced)                │
  │                                │ UPDATE claim_token + lease   │                            │
  │<─────── claimed run ───────────┤                               │                            │
  │                                │                               │                            │
  │ execute(run, agent_id, lease)  │                               │                            │
  ├────────────────────────────────┼──────────────────────────────>│                            │
  │                                │                               │ 启动心跳线程(30s续约)     │
  │                                │                               │ 渲染 research prompt       │
  │                                │                               ├───────────────────────────>│
  │                                │                               │      Hermes 执行中...      │
  │                                │                               │<───────────────────────────┤
  │                                │                               │ 解析输出(JSON findings)    │
  │                                │                               │                            │
  │                                │                               │ complete_run_with_results  │
  │                                │                               ├───────────────────────────>│
  │                                │                               │<────── commit ─────────────│
  │                                │                               │ 停止心跳线程              │
  │<─────── 完成 ──────────────────┼───────────────────────────────┤                            │
```

**关键设计点**:
1. **token-fencing**: claim 时生成 UUID token，complete 时校验，防止过期 Worker 脏写
2. **lease 续约**: 长任务通过 heartbeat 线程定期延长租约，防止被其他 Worker 抢占
3. **过期恢复**: 如果 Worker 崩溃，lease 过期后 run 被其他 Worker 自动恢复

---

### 5.2 Challenge Design 题目设计流程

**目的**: 基于 Research 的结果或种子数据，AI 自动生成完整的 CTF 题目（代码、文档、flag等）。

流程与 Research 类似，但增加了 7 阶段管线：

```
queued → design → implement → build → validate → document → complete
 认领     设计      编码      构建      校验       写文档     完成
```

**关键是 validate 阶段**: 这是唯一不使用 Hermes AI 的阶段，而是由 Python 原生的 `ChallengeValidator` 执行确定性检查（参考解题、文件完整性、架构匹配等），保证题目质量。

**断点恢复 (resume)**: 如果 validate 阶段失败后重新执行，resume 机制自动跳过 design/implement/build，直接从 validate 继续。

### Build 构建编排步骤

Design Tasks 产生并通过质量门后，任务进入 `designed` 状态。此时可以在 Dashboard 的 **构建任务** view 中观察和控制构建，或在 Design Tasks view 里选择一个或多个 `designed` / `build_failed` 任务点击构建。批量提交使用：

```http
POST /api/design-tasks/build
Content-Type: application/json

{"design_task_ids": ["<uuid>", "..."]}
```

单个任务也可以调用 `POST /api/design-tasks/{id}/build`。服务会创建 `build_attempts` 行、写入带 `build_attempt_id` 的 shard 文件，并把设计任务置为 `building`。Worker 仍然通过文件队列认领 shard；`BuildReconciler` 负责把文件队列、`progress_events`、产物目录和 `build_attempts` 状态对齐。构建完成后，任务会进入 `built` 或 `build_failed`，失败或丢失的 latest attempt 可在构建详情页重试。

相关配置默认值：

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `BUILD_RECONCILER_POLL_SECONDS` | `5` | 后台 `BuildReconciler` 轮询间隔；缺失、非整数或非正数时回退为 5 |
| `BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT` | `100` | `/api/build-attempts` 未指定 `limit` 时的返回上限 |
| `BUILD_ATTEMPTS_LIST_MAX_LIMIT` | `500` | `/api/build-attempts` 允许的最大 `limit`，超过时返回该上限并设置 `X-Limit-Capped` |

---

### 5.3 打包导出流程

**目的**: 将 `build_status = "passed"` 的题目打包为最终交付物。

```
Packer
  │
  ├── 筛选 (selector): 遍历 work/challenges/*/*/metadata.json
  │        找出 build_status = "passed" 的题目
  │
  ├── 布局 (layout): 按 category/challenge_name 创建目录结构
  │
  ├── 归档 (archive):
  │    ├── zip 包 (选手版，不含解题文档)
  │    ├── tgz 包 (完整版)
  │    └── tools.zip (工具集)
  │
  ├── Docker (docker):
  │    └── 容器化题目的镜像 tar 导出
  │
  ├── PDF (pdf): 题目描述的 PDF 文件
  │
  └── Excel (workbooks): 题目清单报表
```

**三种打包模式**:
- `VIP` — 完整版（包含所有文件和解题文档）
- `Public` — 公开版（排除写死 flag 的题目，选手用）
- `Onsite Finalist` — 决赛选手版

---

## 六、数据模型速览

### Research 流程

```
generation_requests (生成请求)
  ├── id (UUID)
  ├── category (类别: web/pwn/re)
  ├── topic (主题)
  ├── target_count (目标题目数)
  ├── difficulty_distribution (JSON: 难度分布)
  ├── seed_urls (ARRAY: 种子URL)
  ├── max_attempts (最大重试次数)
  └── status (draft/submitted/running/completed/cancelled)

research_runs (运行实例)
  ├── id (UUID)
  ├── generation_request_id (FK → generation_requests)
  ├── attempt (第N次尝试)
  ├── status (queued/running/completed/failed)
  ├── claim_token (UUID, token-fencing)
  ├── claimed_by (Worker ID)
  ├── claimed_at / lease_expires_at
  └── completed_at

research_findings (研究发现)
  ├── id (UUID)
  ├── run_id (FK → research_runs)
  ├── category / topic
  ├── title / content / url
  └── relevance_score

research_sources (研究来源)
  ├── id (UUID)
  ├── finding_id (FK → research_findings)
  ├── url / title
  └── content_snippet
```

### Progress 进度

```
progress_events (进度事件，只追加不修改)
  ├── id (BIGINT, 自增)
  ├── shard / challenge_id
  ├── worker / stage / status / percent
  ├── message
  └── created_at

progress_snapshots (看板快照，每个 shard+challenge 只保留最新)
  ├── shard (PK)
  ├── challenge_id (PK)
  ├── worker / stage / status / percent
  ├── message
  └── updated_at
```

---

## 七、CLI 命令行参考

```bash
# 初始化工作目录
challenge-factory init

# 拆分题目矩阵为分片
challenge-factory split --matrix challenges.jsonl --size 3

# 认领一个待处理分片
challenge-factory claim --worker my-worker

# 用 Hermes 执行待处理分片
challenge-factory run --worker my-worker [--loop] [--dry-run]

# 查看进度看板
challenge-factory dashboard

# 查看日志
challenge-factory log <name>

# 校验题目
challenge-factory validate [challenge_ids...]

# 打包题目
challenge-factory pack [--public] [--onsite-finalist]

# 管理种子
challenge-factory seed add --file seed.json
challenge-factory seed list

# Research 相关
challenge-factory research submit --category web --topic "XSS" --count 5
challenge-factory research claim --agent my-agent
challenge-factory research status

# 启动 Web Dashboard
challenge-factory web

# Profile 管理
challenge-factory profile list
challenge-factory profile add --name my-profile
```

---

## 八、关键类/函数速查表

### 核心入口

| 类/函数 | 文件 | 一句话功能 |
|--------|------|----------|
| `ShardQueue` | `core/queue.py` | 基于文件系统的分片队列，支持 claim/complete/requeue |
| `HermesRunner` | `hermes/runner.py` | shard 执行的完整 7 阶段管线 |
| `ResearchJobService` | `services/research_job_service.py` | Research run 的状态机 + token-fencing 并发控制 |
| `ResearchAgentExecutor` | `services/research_agent_executor.py` | 执行单个 research run |
| `ResearchWorker` | `services/research_worker.py` | 持续运行的 Worker 进程 |
| `DesignAgentExecutor` | `services/design_agent_executor.py` | 执行单个 design run |
| `BuildOrchestrationService` | `services/build_orchestration_service.py` | 将已完成设计的任务提交为 shard 构建任务 |
| `BuildReconciler` | `services/build_reconciler.py` | 对齐 `build_attempts` 与文件队列、进度事件、产物目录状态 |
| `Packer` | `packing/packer.py` | 题目打包主类 |
| `ChallengeValidator` | `domain/validation.py` | 题目质量校验 |
| `SeedStore` | `domain/seeds.py` | 种子管理 |
| `DashboardService` | `web/dashboard.py` | Web Dashboard 数据服务 |

### 数据访问

| 类 | 文件 | 功能 |
|---|------|------|
| `ResearchRepository` | `persistence/repositories/research.py` | Research 数据的 CRUD |
| `ChallengeDesignRepository` | `persistence/repositories/challenge_designs.py` | 题目设计数据的 CRUD |
| `DesignTaskRepository` | `persistence/repositories/design_tasks.py` | 设计任务数据的 CRUD |
| `BuildAttemptsRepository` | `persistence/repositories/build_attempts.py` | 构建尝试的创建、查询、重试与状态折叠 |
| `PostgresProgressStore` | `persistence/repositories/progress.py` | 进度事件的 PostgreSQL 存储 |
| `InMemoryProgressStore` | `core/state.py` | 进度事件的内存存储（测试用） |
| `transaction()` | `persistence/session.py` | 短事务上下文管理器 |

### 工具函数

| 函数 | 文件 | 功能 |
|------|------|------|
| `split_challenges()` | `core/queue.py` | 按类别将题目列表拆分为 shard |
| `_percent(stage, status)` | `core/state.py` | 阶段+状态 → 进度百分比 |
| `compute_resume_plan()` | `domain/resume.py` | 计算断点恢复计划 |
| `contract_errors()` | `domain/validation.py` | 检查 metadata 字段约定 |
| `invoke_capture()` | `hermes/process.py` | 执行 Hermes 子进程并捕获输出 |
| `render_prompt()` | `hermes/prompt.py` | 渲染执行用的 AI prompt 模板 |
| `merge_reports()` | `domain/reports.py` | 合并多个 shard 的报告 |
| `duration_breakdown()` | `domain/metrics.py` | 计算各阶段耗时 |

### 常量定义

| 常量 | 值 | 含义 |
|------|-----|------|
| `STAGES` | 7 个阶段元组 | 进度管线阶段定义 |
| `STATUSES` | 4 种状态 | 每个阶段的可能状态 |
| `SUPPORTED_CATEGORIES` | `{"web", "pwn", "re"}` | 当前支持的题目类别 |
| `DEFAULT_HERMES_COMMAND` | `"hermes chat -Q --yolo -q"` | Hermes 默认命令 |
| `DEFAULT_HERMES_TIMEOUT` | 1500 秒 | Hermes 默认超时 |
| `HEARTBEAT_INTERVAL_SECONDS` | 30 秒 | 心跳续约间隔 |

---

*手册生成日期: 2026-06-18 | 基于 challenge-factory 源码完整分析*
