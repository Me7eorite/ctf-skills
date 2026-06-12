## 1. Skeleton and file moves

- [x] 1.1 Create empty packages: `src/core/`, `src/domain/`, `src/packing/`, `src/hermes/`, `src/web/`, each with an `__init__.py`.
- [x] 1.2 `git mv src/paths.py src/core/paths.py`; `git mv src/jsonio.py src/core/jsonio.py`; `git mv src/state.py src/core/state.py`.
- [x] 1.3 `git mv src/shards.py src/core/queue.py` (rename to `queue.py`); update any string references to the old filename.
- [x] 1.4 `git mv src/seeds.py src/domain/seeds.py`; `git mv src/validation.py src/domain/validation.py`; `git mv src/reports.py src/domain/reports.py`.
- [x] 1.5 `git mv src/dashboard.py src/web/dashboard.py`; `git mv src/webserver.py src/web/server.py`; `git mv src/static src/web/static`.
- [x] 1.6 Split `src/packing.py` into `src/packing/packer.py` (Packer + PackerOptions + PackingError + `_pack_challenge` coordinator), `selector.py` (`_selected_challenges`), `layout.py` (`_prepare_output`, `_create_layout`, `_safe_name`), `pdf.py` (`_render_pdf`, `_escape_pdf_text`), `archive.py` (`_write_tools_zip`, `_write_zip`, `_tree_members`, `_enclosure_members`), `docker.py` (`_save_docker`, `_is_containerized`, `_should_emit_enclosure`), `workbooks.py` (`_write_workbook`, `_overview_row`); delete the original `src/packing.py`.
- [x] 1.7 Set `src/packing/__init__.py` to re-export `Packer`, `PackerOptions`, `PackingError`.
- [x] 1.8 Split `src/hermes.py` into `src/hermes/runner.py` (HermesRunner main class and lifecycle), `prompt.py` (template rendering), `progress.py` (state event emission); delete the original `src/hermes.py`.
- [x] 1.9 Set `src/hermes/__init__.py` to re-export `HermesRunner`.
- [x] 1.10 Confirm no legacy top-level modules remain: `ls src/*.py` shows only `cli.py`.

## 2. Path anchor fixes (must-do)

- [x] 2.1 In `src/core/paths.py`, change `root = Path(__file__).resolve().parents[1]` to `parents[2]`. Update `repository` accordingly if it shares the anchor.
- [x] 2.2 In `src/core/paths.py`, replace the `static` property body with `return Path(str(importlib.resources.files("web") / "static"))` and add `from importlib.resources import files` (or use the inline form). Remove the previous `Path(__file__).resolve().parent / "static"` form.
- [x] 2.3 Verify by inspection that no other `paths.py` property uses `__file__`-based anchors that became wrong (e.g. `design_skill`, `prompt_template`, `generation_profile` should all be derived from `root`).

## 3. Import rewiring

- [x] 3.1 Rewrite `src/cli.py` imports to use new paths: `from core.queue import ShardQueue, split_matrix`; `from core.state import STAGES, STATUSES, StateStore`; `from core.paths import ProjectPaths`; `from domain.validation import ChallengeValidator`; `from domain.reports import merge_reports`; `from packing import Packer, PackerOptions`; `from hermes import HermesRunner`; `from web.server import serve`.
- [x] 3.2 Rewrite intra-`src/` imports in every moved file: `from jsonio import ...` → `from core.jsonio import ...`; `from paths import ...` → `from core.paths import ...`; `from state import ...` → `from core.state import ...`; `from shards import ...` → `from core.queue import ...`; business modules (`seeds`, `validation`, `reports`) referenced from within `domain/` use sibling imports; `dashboard.py` references go to `web.dashboard`.
- [x] 3.3 Audit `tools/scripts/prepare_hermes_home.py` for legacy top-level imports and rewrite to the new package paths.
- [x] 3.4 `grep -rn 'from jsonio\|from paths\|from state\|from shards\|from seeds\|from validation\|from reports\|from dashboard\|from webserver' src/ tools/scripts/ tests/` returns no results.
- [x] 3.5 Confirm `python -c "import cli, core, domain, packing, hermes, web"` from project root succeeds (after `uv sync`).

## 4. Packaging configuration

- [x] 4.1 Update `pyproject.toml` `[tool.setuptools]` to `py-modules = ["cli"]` only (drop the 11 legacy entries).
- [x] 4.2 Add `[tool.setuptools.packages.find]` section with `where = ["src"]`.
- [x] 4.3 Add `[tool.setuptools.package-data]` with `web = ["static/*"]`.
- [x] 4.4 Run `uv pip install -e .` and confirm `.venv/bin/challenge-factory` exists and `uv run challenge-factory --help` returns CLI help.

## 5. Test alignment and guardrail

- [x] 5.1 `grep -rn 'patch("' tests/` and rewrite each dotted path that references a moved module: `patch("hermes.shutil.which")` → `patch("hermes.runner.shutil.which")`; `patch("packing.subprocess.run")` → `patch("packing.docker.subprocess.run")`; similar substitutions for `patch("hermes.X")`, `patch("packing.X")`, `patch("dashboard.X")`, `patch("webserver.X")`.
- [x] 5.2 Update any `from <module> import` lines in tests to the new package paths.
- [x] 5.3 Create `tests/app/test_dependency_direction.py` implementing the ast-based enforcement per `module-architecture` spec: walk `src/**/*.py`, compute owning package, assert `import` / `from ... import` statements respect the matrix in design D2, with intra-package imports allowed.
- [x] 5.4 Run `uv run pytest tests/` and confirm all tests pass (including the new guardrail).

## 6. Smoke verification

- [x] 6.1 `uv run challenge-factory init` succeeds and creates `work/` directories at the repository root (not under `src/`).
- [x] 6.2 `uv run challenge-factory split --matrix matrix.example.jsonl --size 3` succeeds and produces pending shard files in `work/shards/pending/`.
- [x] 6.3 `uv run challenge-factory run --worker dry-01 --dry-run` succeeds and renders a prompt without invoking Hermes.
- [x] 6.4 `uv run challenge-factory pack --skip-docker` succeeds against an empty/passing challenge set (or documents that no challenges qualify) and the command exits 0.
- [x] 6.5 `uv run challenge-factory serve` starts and `curl http://127.0.0.1:4173/` returns 200; `curl http://127.0.0.1:4173/static/<known-asset>` returns 200 (use a real asset filename observed in `src/web/static/`).
- [x] 6.6 `uv run python -c "import tools.scripts.prepare_hermes_home"` exits 0.
- [x] 6.7 Run both import smoke commands from design D6 / Acceptance: `python -c "from cli import main; from packing import Packer; from hermes import HermesRunner; from web.server import serve"` and `python -c "from core.paths import ProjectPaths; from core.queue import ShardQueue; from domain.validation import ChallengeValidator"`; both exit 0.

## 7. Cleanup and documentation

- [x] 7.1 Delete `src/__pycache__/`, `src/challenge_factory.egg-info/`, and any per-module `__pycache__/` left from old layout.
- [x] 7.2 Update `README.md` Project Structure section to reflect the new layered tree.
- [x] 7.3 Update `docs/architecture.md` with the dependency-direction matrix from design D2 and a one-line summary of each package's role.
- [x] 7.4 Run `openspec validate restructure-src-layered --strict` and confirm it passes.
- [x] 7.5 Run `uv run pytest tests/` one final time to confirm green after docs and cleanup changes.
