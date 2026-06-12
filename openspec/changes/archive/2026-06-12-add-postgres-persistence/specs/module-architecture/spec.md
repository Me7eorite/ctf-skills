## MODIFIED Requirements

### Requirement: Source code is organized into layered packages

The system SHALL organize `src/` into the following layered packages, plus `cli.py` as composition root:

- `core/` — infrastructure leaves: paths, JSON I/O, SQLite state, shard queue mechanism.
- `domain/` — business rules: seeds, artifact validation, report aggregation.
- `packing/` — delivery bundle subsystem (subpackage).
- `hermes/` — Hermes process and prompt subsystem (subpackage).
- `persistence/` — PostgreSQL engine, session, and Alembic-driven schema lifecycle (subpackage).
- `web/` — HTTP server, dashboard service, and static assets.
- `cli.py` — top-level module at `src/cli.py`, not split.

Subpackages (`packing/`, `hermes/`, `persistence/`) SHALL expose their public API through `__init__.py` re-exports so external callers import via the package name (e.g., `from packing import Packer`, `from hermes import HermesRunner`, `from persistence import transaction`).

The 11 legacy top-level modules (`paths.py`, `jsonio.py`, `state.py`, `shards.py`, `seeds.py`, `validation.py`, `reports.py`, `packing.py`, `hermes.py`, `dashboard.py`, `webserver.py`) SHALL NOT remain in `src/`; no backwards-compatibility shim files are kept.

#### Scenario: Required packages exist

- **WHEN** the repository is checked out at HEAD
- **THEN** `src/core/`, `src/domain/`, `src/packing/`, `src/hermes/`, `src/persistence/`, `src/web/` each exist as Python packages with `__init__.py`
- **AND** `src/cli.py` exists at the top of `src/`
- **AND** none of the 11 legacy top-level modules exist under `src/`

#### Scenario: Public API re-exports succeed

- **WHEN** running `python -c "from cli import main; from packing import Packer, PackerOptions, PackingError; from hermes import HermesRunner; from persistence import create_engine_from_env, transaction; from web.server import serve"`
- **THEN** the command exits with status 0 and no import error

#### Scenario: Deep imports succeed

- **WHEN** running `python -c "from core.paths import ProjectPaths; from core.jsonio import read_json, write_json; from core.queue import ShardQueue, split_matrix; from core.state import StateStore; from domain.validation import ChallengeValidator; from domain.seeds import SeedStore; from domain.reports import merge_reports; from persistence.engine import create_engine_from_env; from persistence.session import SessionFactory"`
- **THEN** the command exits with status 0 and no import error

### Requirement: Inter-package dependency direction is enforced

Imports between packages under `src/` SHALL follow this directed acyclic matrix. Reverse imports and skip-level violations are forbidden. Same-package internal imports are unrestricted.

| Importer | Allowed targets |
| --- | --- |
| `cli` | `web`, `hermes`, `packing`, `persistence`, `domain`, `core` |
| `web` | `persistence`, `domain`, `core` |
| `hermes` | `domain`, `core` |
| `packing` | `core` |
| `persistence` | `domain`, `core` |
| `domain` | `core` |
| `core` | (stdlib and third-party only — no `src/` siblings) |

The system SHALL provide an automated test (`tests/app/test_dependency_direction.py`) that parses every `.py` file under `src/` with `ast`, computes each file's owning package, and asserts that none of its `import` / `from ... import` statements target a forbidden sibling package. The test SHALL produce a diagnostic listing the offending file, the offending import statement, and the violated edge when an assertion fails.

#### Scenario: Conforming codebase passes the guardrail

- **WHEN** the dependency direction test runs against the current `src/`
- **THEN** it discovers all package directories including `persistence` and `cli.py`
- **AND** the test passes

#### Scenario: Reverse import is rejected

- **WHEN** a hypothetical `src/core/paths.py` contains `from domain.validation import ChallengeValidator` and the dependency direction test runs
- **THEN** the test fails
- **AND** the failure message identifies the file `src/core/paths.py`, the import line, and that `core` is not permitted to import `domain`

#### Scenario: Hermes cannot import persistence

- **WHEN** a hypothetical `src/hermes/runner.py` contains `from persistence import transaction` and the dependency direction test runs
- **THEN** the test fails because `hermes` is only allowed to import `domain` and `core`

#### Scenario: Persistence cannot import web

- **WHEN** a hypothetical `src/persistence/session.py` contains `from web.server import serve` and the dependency direction test runs
- **THEN** the test fails because `persistence` is only allowed to import `domain` and `core`

#### Scenario: Skip-level import is rejected

- **WHEN** a hypothetical `src/web/dashboard.py` contains `from packing import Packer` and the dependency direction test runs
- **THEN** the test fails because `web` is only allowed to import `persistence`, `domain`, and `core`

#### Scenario: Same-package import is allowed

- **WHEN** `src/packing/packer.py` contains `from packing.pdf import _render_pdf` and the dependency direction test runs
- **THEN** the test passes (intra-package imports are unrestricted)
