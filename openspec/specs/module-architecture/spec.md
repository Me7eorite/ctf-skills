# module-architecture Specification

## Purpose
TBD - created by archiving change restructure-src-layered. Update Purpose after archive.

## Requirements
### Requirement: Source code is organized into layered packages

The system SHALL organize `src/` into the following layered packages, plus `cli.py` as composition root:

- `core/` — infrastructure leaves: paths, JSON I/O, SQLite state, shard queue mechanism.
- `domain/` — business rules: seeds, artifact validation, report aggregation.
- `packing/` — delivery bundle subsystem (subpackage).
- `hermes/` — Hermes process and prompt subsystem (subpackage).
- `web/` — HTTP server, dashboard service, and static assets.
- `cli.py` — top-level module at `src/cli.py`, not split.

Subpackages (`packing/`, `hermes/`) SHALL expose their public API through `__init__.py` re-exports so external callers import via the package name (e.g., `from packing import Packer`, `from hermes import HermesRunner`).

The 11 legacy top-level modules (`paths.py`, `jsonio.py`, `state.py`, `shards.py`, `seeds.py`, `validation.py`, `reports.py`, `packing.py`, `hermes.py`, `dashboard.py`, `webserver.py`) SHALL NOT remain in `src/`; no backwards-compatibility shim files are kept.

#### Scenario: Required packages exist

- **WHEN** the repository is checked out at HEAD
- **THEN** `src/core/`, `src/domain/`, `src/packing/`, `src/hermes/`, `src/web/` each exist as Python packages with `__init__.py`
- **AND** `src/cli.py` exists at the top of `src/`
- **AND** none of the 11 legacy top-level modules exist under `src/`

#### Scenario: Public API re-exports succeed

- **WHEN** running `python -c "from cli import main; from packing import Packer, PackerOptions, PackingError; from hermes import HermesRunner; from web.server import serve"`
- **THEN** the command exits with status 0 and no import error

#### Scenario: Deep imports succeed

- **WHEN** running `python -c "from core.paths import ProjectPaths; from core.jsonio import read_json, write_json; from core.queue import ShardQueue, split_matrix; from core.state import StateStore; from domain.validation import ChallengeValidator; from domain.seeds import SeedStore; from domain.reports import merge_reports"`
- **THEN** the command exits with status 0 and no import error

### Requirement: Inter-package dependency direction is enforced

Imports between packages under `src/` SHALL follow this directed acyclic matrix. Reverse imports and skip-level violations are forbidden. Same-package internal imports are unrestricted.

| Importer | Allowed targets |
| --- | --- |
| `cli` | `web`, `hermes`, `packing`, `domain`, `core` |
| `web` | `domain`, `core` |
| `hermes` | `domain`, `core` |
| `packing` | `core` |
| `domain` | `core` |
| `core` | (stdlib and third-party only — no `src/` siblings) |

The system SHALL provide an automated test (`tests/app/test_dependency_direction.py`) that parses every `.py` file under `src/` with `ast`, computes each file's owning package, and asserts that none of its `import` / `from ... import` statements target a forbidden sibling package. The test SHALL produce a diagnostic listing the offending file, the offending import statement, and the violated edge when an assertion fails.

#### Scenario: Conforming codebase passes the guardrail

- **WHEN** the dependency direction test runs against the current `src/`
- **THEN** it discovers all package directories and `cli.py`
- **AND** the test passes

#### Scenario: Reverse import is rejected

- **GIVEN** a hypothetical `src/core/paths.py` that contains `from domain.validation import ChallengeValidator`
- **WHEN** the dependency direction test runs
- **THEN** the test fails
- **AND** the failure message identifies the file `src/core/paths.py`, the import line, and that `core` is not permitted to import `domain`

#### Scenario: Skip-level import is rejected

- **GIVEN** a hypothetical `src/web/dashboard.py` that contains `from packing import Packer`
- **WHEN** the dependency direction test runs
- **THEN** the test fails because `web` is only allowed to import `domain` and `core`

#### Scenario: Same-package import is allowed

- **GIVEN** `src/packing/packer.py` contains `from packing.pdf import _render_pdf`
- **WHEN** the dependency direction test runs
- **THEN** the test passes (intra-package imports are unrestricted)

### Requirement: Path anchors resolve relative to the new layout

`ProjectPaths.root` SHALL resolve to the repository root regardless of where `paths.py` lives inside `src/`. With `paths.py` located at `src/core/paths.py`, the implementation SHALL use `Path(__file__).resolve().parents[2]`.

`ProjectPaths.static` SHALL resolve to the packaged `web/static/` directory in both editable installs and wheel installs. The implementation SHALL use `importlib.resources.files("web") / "static"` (converted to `Path`). The build configuration SHALL declare `[tool.setuptools.package-data] web = ["static/*"]` so the assets ship with the wheel.

#### Scenario: Root resolves to repository

- **WHEN** `ProjectPaths.discover()` is called
- **THEN** `paths.root` equals the absolute path of the repository root (the parent of `src/`)
- **AND** `paths.work` resolves to `<repo>/work`
- **AND** `paths.generation_profile` resolves to `<repo>/generation-profiles.json`

#### Scenario: Static resolves under editable install

- **GIVEN** the project installed via `uv pip install -e .`
- **WHEN** the dashboard server resolves a request for `/static/<asset>`
- **THEN** the asset is served from `src/web/static/<asset>` and the response status is 200
