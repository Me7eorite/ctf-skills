## Context

`src/` 当前是 12 个平铺单文件模块（共 ~2323 行），从扁平化提交 `5ba7afd` 起放在仓库根目录下的 `src/`。模块间没有依赖方向约束，已经出现 `hermes` 直 import `validation`、`dashboard` 直 import `shards` 这类横连。`packing.py` 单文件 507 行涵盖六类完全独立的职责（选片 / 布局 / PDF / zip / docker / workbook）；`hermes.py` 400 行混合了 prompt 渲染、子进程运行、进度回写。

实测依赖图（每个模块从兄弟模块的 import）：

```
cli      -> hermes, packing, paths, reports, shards, state, validation, webserver
hermes   -> jsonio, paths, shards, state, validation
dashboard -> jsonio, paths, seeds, shards, state
webserver -> dashboard, paths
packing  -> jsonio, paths
seeds    -> jsonio, paths, shards
shards   -> jsonio, paths
validation -> jsonio, paths
reports  -> jsonio
state    -> paths
paths    -> (无)
jsonio   -> (无)
```

业务功能契约保持不变：CLI 命令集、HTTP 路由、SQLite schema、`work/shards/` 队列文件格式、`work/资源包/` 交付包 v2 布局均不修改。归档过的三条主规范（`delivery-bundle`、`challenge-seed-management`、`re-target-platforms`）均不需要 delta。

未来计划：worker pool、租约、任务持久化系统。这些**不在本次实现**，但需要本次规划好它们将来的物理位置，否则要么后续重新返工分层、要么直接被塞回横连旧模式。

## Goals / Non-Goals

**Goals:**

- 在 `src/` 内引入 5 个分层包（`core` / `domain` / `packing` / `hermes` / `web`），将现有 12 个模块按职责归位。
- 用 `__init__.py` re-export 维持 `packing` 与 `hermes` 的公共 API 表面（`Packer`、`HermesRunner` 等），子包内部拆分对消费者透明。
- 通过 ast 物理护栏强制依赖方向矩阵，让未来的违反在 CI 即报错。
- 修复因目录层级变化引入的两处 `paths.py` 锚点 bug（`root` 与 `static`）。
- 记录未来的 `worker/` 与 `domain/tasks.py` 在依赖矩阵中的位置，作为后续变更的契约基线。

**Non-Goals:**

- 不改任何 CLI 命令名或参数。
- 不改 HTTP 路由或 dashboard 行为。
- 不改 SQLite schema 或 `work/shards/` 队列文件格式。
- 不改交付包 v2 布局。
- 不引入新第三方依赖（`importlib.resources` 是标准库）。
- 不实现 worker pool、租约、任务状态机——本次只在 design 中记录它们将来的层位，不预建空目录。
- 不为旧顶层模块名保留 re-export shim（即不允许 `from paths import ProjectPaths` 在迁移后继续工作）。
- 不修改任何 spec：本次无 delta，三条主规范契约保持原样。

## Decisions

### D1 五层结构：core / domain / packing / hermes / web + cli

**Decision**: 按下表归位：

| 当前模块 | 目标位置 | 理由 |
| --- | --- | --- |
| `paths.py` | `core/paths.py` | 纯基础设施叶子，无业务依赖 |
| `jsonio.py` | `core/jsonio.py` | 同上 |
| `state.py` | `core/state.py` | 只依赖 paths，SQLite 观测层属基础设施 |
| `shards.py` | `core/queue.py` | 队列原语本质是 infra；改名为 `queue` 与未来 `domain/tasks.py` 拉开语义 |
| `seeds.py` | `domain/seeds.py` | 业务规则（前缀-类别一致性、端口校验） |
| `validation.py` | `domain/validation.py` | 业务规则（制品 + EXP 验证） |
| `reports.py` | `domain/reports.py` | 业务聚合 |
| `packing.py` | `packing/`（拆 7 个文件） | 6 类独立职责，子包内单一职责 |
| `hermes.py` | `hermes/`（拆 3 个文件） | prompt / runner / progress 三段语义 |
| `dashboard.py` | `web/dashboard.py` | 只被 webserver 使用 |
| `webserver.py` | `web/server.py` | HTTP 传输层 |
| `static/` | `web/static/` | 与 server 同层，通过 package-data 安装 |
| `cli.py` | `cli.py`（不动） | composition root，不属于任何层 |

**Alternatives considered**:

- *4 层（合并 packing 与 hermes 到 domain）*：会让 domain 突破 600 行单职责边界，PDF / docker / subprocess 调用挤进业务规则层。拒绝。
- *6 层（独立 observability 层把 state.py 拆出）*：state 只依赖 paths，独立成层只为目录树形好看，不解任何耦合。拒绝。
- *把 cli 也拆进 web*：cli 与 web 共享部分参数但 cli 是 composition root，需要 import 所有层；放进 web 会让 web 自身的依赖矩阵失去边界。拒绝。

### D2 依赖方向矩阵（反向禁止 + 同层任意）

**Decision**:

```
cli      -> {web, hermes, packing, domain, core}
web      -> {domain, core}
hermes   -> {domain, core}
packing  -> {core}
domain   -> {core}
core     -> stdlib / third-party only
同一包内部 import 不受约束
```

**Alternatives considered**:

- *允许 web -> hermes*：dashboard 不调用 hermes runner（实际通过队列触发 worker 子进程），允许会诱发"web 直接跑 hermes"反模式。拒绝。
- *允许 hermes -> packing*：hermes 在运行结束后不打包；packing 是独立 CLI 命令的子系统。允许会让两个子系统循环。拒绝。
- *用 import-linter 工具替代自写 ast 测试*：增加新依赖。本项目规模 5 个包足够手写 ~80 行 ast 测试，不值得引入工具。拒绝。

### D3 packing.py 拆分粒度

**Decision**: 拆为 `packer.py` / `selector.py` / `layout.py` / `pdf.py` / `archive.py` / `docker.py` / `workbooks.py`，公共 API 通过 `packing/__init__.py` re-export。

| 子文件 | 内容 | 当前来源 |
| --- | --- | --- |
| `packer.py` | `Packer` 协调器、`PackerOptions`、`PackingError` | line 79–148 |
| `selector.py` | `_selected_challenges` | line 173–181 |
| `layout.py` | `_prepare_output`、`_create_layout`、`_safe_name`、`_pack_challenge` 顶层骨架 | line 148–172, 469–475 |
| `pdf.py` | `_render_pdf`、`_escape_pdf_text` | line 259–371, 476–483 |
| `archive.py` | `_write_zip`、`_tree_members`、`_enclosure_members`、`_write_tools_zip` | line 236–258, 484–507 |
| `docker.py` | `_save_docker`、`_is_containerized`、`_should_emit_enclosure` | line 416–468 |
| `workbooks.py` | `_write_workbook`、`_overview_row` | line 372–415 |

`_pack_challenge` 留在 `packer.py` 作为协调器，它调用 `archive` / `pdf` / `docker` 子模块。子文件目标行数 50–150 行，单一职责。

### D4 hermes.py 拆分粒度

**Decision**: 拆为 `runner.py` / `prompt.py` / `progress.py`，`HermesRunner` 通过 `hermes/__init__.py` re-export。

- `runner.py`: `HermesRunner` 主类、子进程编排、claim/complete 生命周期。
- `prompt.py`: shard prompt 渲染（读取模板 + 注入变量）。
- `progress.py`: 阶段事件回写 `state`、运行日志聚合。

### D5 不保留旧顶层 shim

**Decision**: 删除全部 11 个旧顶层模块文件，外部 import 必须改用新路径。

**Alternatives considered**:

- *保留 `paths.py` 等 11 个文件作为 `from core.paths import *` 兼容层*：会让两套 import 路径共存、`mypy` / IDE 无法判断标准位置、依赖方向测试需要排除 shim 文件、CLAUDE.md 明确反对此类兼容 hack。拒绝。

风险点：`cli.py`、所有 `tests/`、`scripts/prepare_hermes_home.py` 中的 import 都必须改对，少改一处就报 `ModuleNotFoundError`——这正是迁移期间 grep+pytest 双保险要覆盖的范围。

### D6 path 锚点修复策略

**Decision**:

- `ProjectPaths.root` 改为 `Path(__file__).resolve().parents[2]`（`core/paths.py` 比原 `paths.py` 多一级 `core/`）。
- `ProjectPaths.static` 改为 `Path(str(importlib.resources.files("web") / "static"))`，同时在 `pyproject.toml` 加 `[tool.setuptools.package-data] web = ["static/*"]`。

**Alternatives considered**:

- *static 用 `Path(__file__).resolve().parents[1] / "web" / "static"`*：editable install 工作正常，但 wheel install（未来如果用 `uv build` 分发）找不到 static——`__file__` 此时在 site-packages 中，相对路径不再指向源 `web/static/`。拒绝。
- *static 用 `paths.root / "src" / "web" / "static"`*：依赖 `root` 是仓库根，但 wheel 安装后没有 `src/web/static/` 这条路径。拒绝。

### D7 包配置切换

**Decision**: `pyproject.toml` 改为：

```toml
[tool.setuptools]
package-dir = {"" = "src"}
py-modules = ["cli"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
web = ["static/*"]
```

`cli` 仍是单文件根模块，因此保留 `py-modules`；其余通过 `packages.find` 自动发现。console script `challenge-factory = "cli:main"` 不变。

### D8 未来层位约定（Deferred Layers）

本次不实现，但在设计层面钉死：

- `worker/`：介于 `web` 与 `{hermes, packing, domain}` 之间。允许 `worker -> {hermes, domain, core}`。包含 `pool` / `leases` / `scheduler`。`cli` 允许 import `worker` 用于 CLI 启动 worker 入口。
- `domain/tasks.py`：任务领域模型（状态机、合法状态流转、业务校验），独立于 `core.queue` 的存储机制。
- 边界契约（写在这里，未来变更只需引用）：
  - `core.queue` 只负责 shard 队列存储机制、文件格式、claim/complete/requeue。
  - `domain.tasks` 负责任务领域模型与合法流转规则。
  - `worker.*` 负责并发、调度、租约、重试、超时回收。
  - `hermes.runner` 只执行一个已领取的 shard / task，不直接操作队列目录结构。
  - `web` / `dashboard` / `cli` 通过 `core` / `domain` 公共 API 读写任务，不直接操作底层文件布局。

这些层只有真正被实现时才在代码中出现，**不预建空目录、不放占位文件**。

### D9 物理护栏测试

**Decision**: 新增 `tests/app/test_dependency_direction.py`，行为：

- 遍历 `src/` 下所有 `.py` 文件，跳过 `__init__.py` 与 `static/` 子目录。
- 用 `ast.parse` 抽取 top-level `import` / `from ... import` 节点。
- 对每个文件计算其所属包（`src/core/...` → `core`），按 D2 矩阵断言其外部 import 不违反方向。
- 同包 import（如 `from packing.pdf import _render_pdf`）放行。
- 第三方 import（`fastapi`、`openpyxl` 等）由 stdlib + 依赖白名单或反向逻辑跳过。

测试失败信息必须打印违规文件路径、违规 import 语句、违反的方向。

## Risks / Trade-offs

- **`setuptools.packages.find` 与 `py-modules` 混用** → 切换前后各跑一次 `uv pip install -e .` 并验证 `.venv/bin/challenge-factory` 存在且可执行。
- **测试 mock 的 dotted path 失效** → 在 import 重写 task 中显式列出全扫 `grep -rn 'patch("' tests/` + 修正子模块路径（如 `hermes.runner.shutil.which`、`packing.docker.subprocess.run`）。
- **`__init__.py` re-export 漏导出** → 在 acceptance 中加入两组 import 烟囱（深路径 + 浅路径），CI 即时拦截。
- **`ProjectPaths.root` 与 `static` 锚点错改** → 迁移 `paths.py` 后立刻跑 `uv run challenge-factory init` 验证 work 目录创建到仓库根（不是 `src/`），并 `serve` + `curl /static/<asset>` 验证 static 解析。
- **未来 `core.queue` 被吸收业务逻辑** → 三层约束（命名为 `queue` 不为 `tasks`、依赖方向测试在 CI 强制、design D8 边界契约）共同压制。
- **git blame 在 GitHub UI 断点** → 接受。`git log --follow` 与 GitHub "View blame prior to..." 按钮可补救。
- **scripts/prepare_hermes_home.py 旧 import** → 在 import 重写 task 中显式列入审查清单，并在 acceptance 加 `python -c "import scripts.prepare_hermes_home"` 烟囱。

## Migration Plan

按 `tasks.md` 顺序执行。回滚策略：

- 任意单步失败 → `git stash` + 回到上一个 task 完成态。
- 全量回滚 → 该变更落在独立分支，`git checkout main` + 删除分支。
- 因本变更**无 spec delta、无 schema 变更、无运行时数据迁移**，回滚仅涉及代码与配置文件，没有数据兼容性问题。
