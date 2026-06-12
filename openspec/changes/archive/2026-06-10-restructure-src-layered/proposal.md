## Why

`src/` 下 12 个模块平铺，依赖横连：`hermes` 直 import `validation`，`dashboard` 直 import `shards`，没有依赖方向约束。`packing.py` 单文件 507 行混合了选片/布局/PDF/zip/docker/workbook 六类职责。后续还会引入 worker pool、任务级持久化等能力，必须先建立分层 + 物理护栏，避免新模块继续横连或把队列/任务/执行器揉在一起。

## What Changes

- 将 `src/` 重组为分层目录：`core/`、`domain/`、`packing/`（子包）、`hermes/`（子包）、`web/`；`cli.py` 保留为顶层 composition root，不拆分。
- 引入严格依赖方向（反向禁止）：
  - `cli -> {web, hermes, packing, domain, core}`
  - `web -> {domain, core}`
  - `hermes -> {domain, core}`
  - `packing -> core`
  - `domain -> core`
  - `core -> stdlib / third-party only`
  - 同一包内部 import 不受约束。
- 拆解 `packing.py` 为 `packing/` 子包：`packer.py` / `selector.py` / `layout.py` / `pdf.py` / `archive.py` / `docker.py` / `workbooks.py`，`__init__.py` re-export `Packer` / `PackerOptions` / `PackingError`。
- 拆解 `hermes.py` 为 `hermes/` 子包：`runner.py` / `prompt.py` / `progress.py`，`__init__.py` re-export `HermesRunner`。
- 迁移 `shards.py -> core/queue.py`、`paths.py / jsonio.py / state.py -> core/`、`seeds.py / validation.py / reports.py -> domain/`、`dashboard.py -> web/dashboard.py`、`webserver.py -> web/server.py`、`src/static/ -> src/web/static/`。
- **BREAKING (内部 API)**：删除 11 个旧顶层模块，不保留 re-export shim。外部 import 必须改用新路径（例如 `from core.paths import ProjectPaths`、`from core.queue import ShardQueue`、`from web.server import serve`）。`packing` 与 `hermes` 公共 API 通过子包 `__init__.py` re-export 保留。
- 修复两处路径锚点 bug：
  - `core/paths.py` 中 `root` 从 `Path(__file__).resolve().parents[1]` 改为 `parents[2]`。
  - `core/paths.py` 中 `static` property 改用 `Path(str(importlib.resources.files("web") / "static"))`。
- 审查并修正 `tools/scripts/prepare_hermes_home.py` 对旧顶层模块的 import。
- 新增 `tests/app/test_dependency_direction.py`，用 `ast` 解析所有 `src/` 模块的 import，强制依赖方向矩阵。
- 更新 `pyproject.toml`：`py-modules = ["cli"]` + `[tool.setuptools.packages.find] where = ["src"]` + `[tool.setuptools.package-data] web = ["static/*"]`。

## Capabilities

### New Capabilities

- `module-architecture`: `src/` 内部模块的分层结构与依赖方向矩阵作为系统不变量，由自动化测试持续强制。

### Modified Capabilities

（无）—— 三条业务规范的契约不变：`delivery-bundle` / `challenge-seed-management` / `re-target-platforms` 的 CLI 命令、HTTP 路由、SQLite schema、队列文件格式、交付包 v2 布局全部保持不变。

## Impact

- **代码**：`src/` 全量重组；11 个旧顶层模块文件删除；`packing.py` 拆为 7 个子文件；`hermes.py` 拆为 3 个子文件；`cli.py` 与 `tools/scripts/prepare_hermes_home.py` 的 import 语句更新；`core/paths.py` 中 `root` 与 `static` 属性的锚点修正。
- **测试**：`tests/app/` 中所有 `patch("...")` dotted path 需要随子模块结构更新（例如 `patch("hermes.shutil.which")` → `patch("hermes.runner.shutil.which")`、`patch("packing.subprocess.run")` → `patch("packing.docker.subprocess.run")`）；新增 `tests/app/test_dependency_direction.py` 物理护栏。
- **打包**：`pyproject.toml` 从 `py-modules` 平铺切换到 `packages.find` + `package-data`；console script `challenge-factory = "cli:main"` 不变；editable install 与 wheel install 路径解析需在切换前后各验证一次。
- **依赖**：无新增第三方依赖；`importlib.resources` 来自标准库。
- **文档**：`README.md` 的 Project Structure 段需要更新；`docs/architecture.md` 加入依赖方向矩阵图。
- **运行时数据**：`work/`、`.hermes/`、SQLite schema 完全不变；现有归档与 in-flight 状态文件不受影响。
- **风险点**：`ProjectPaths.root` 与 `ProjectPaths.static` 的锚点改变是本次唯一的运行时路径风险，迁移后必须立刻跑 `init` + `serve` 验证。
