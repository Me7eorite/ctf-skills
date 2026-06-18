## MODIFIED Requirements

### Requirement: Source code is organized into layered packages

The system SHALL organize `src/` into the following layered packages, plus `cli.py` as composition root:

- `core/` — infrastructure leaves: paths, JSON I/O, shard queue mechanism, and the storage-agnostic `ProgressStore` protocol plus its `ProgressEventInput` DTO and `InMemoryProgressStore` test double.
- `domain/` — business rules: seeds, artifact validation, report aggregation, research DTOs and validators.
- `packing/` — delivery bundle subsystem (subpackage).
- `hermes/` — Hermes process and prompt subsystem (subpackage), including the shared subprocess plumbing reused by shard execution and the Research Agent. The runner accepts a `ProgressStore` at construction time; it does NOT import `persistence`.
- `persistence/` — PostgreSQL engine, session, Alembic-driven schema lifecycle, ORM models, and repositories (subpackage). Includes `repositories/progress.py` (the production `PostgresProgressStore` implementation of `core.state.ProgressStore`) and `models/progress.py` (the `progress_events` and `progress_snapshots` ORM mappings).
- `services/` — orchestration layer that owns transactions spanning multiple subsystems (subpackage).
- `web/` — HTTP server, dashboard service, and static assets.
- `cli.py` — top-level module at `src/cli.py`, not split.

Subpackages (`packing/`, `hermes/`, `persistence/`, `services/`) SHALL expose their public API through `__init__.py` re-exports so external callers import via the package name (e.g., `from packing import Packer`, `from hermes import HermesRunner`, `from persistence import transaction`, `from services import ResearchJobService, ResearchAgentExecutor, ResearchWorker`).

The 11 legacy top-level modules (`paths.py`, `jsonio.py`, `state.py`, `shards.py`, `seeds.py`, `validation.py`, `reports.py`, `packing.py`, `hermes.py`, `dashboard.py`, `webserver.py`) SHALL NOT remain in `src/`; no backwards-compatibility shim files are kept. The legacy class name `StateStore` SHALL NOT exist anywhere under `src/` or `tests/` after this change.

#### Scenario: Required packages exist

- **WHEN** the repository is checked out at HEAD
- **THEN** `src/core/`, `src/domain/`, `src/packing/`, `src/hermes/`, `src/persistence/`, `src/services/`, `src/web/` each exist as Python packages with `__init__.py`
- **AND** `src/cli.py` exists at the top of `src/`
- **AND** none of the 11 legacy top-level modules exist under `src/`
- **AND** `src/core/state.py` exports `ProgressStore`, `ProgressEventInput`, and `InMemoryProgressStore` and does NOT export `StateStore`
- **AND** `src/persistence/repositories/progress.py` and `src/persistence/models/progress.py` exist

#### Scenario: Public API re-exports succeed

- **WHEN** running `python -c "from cli import main; from packing import Packer, PackerOptions, PackingError; from hermes import HermesRunner; from persistence import create_engine_from_env, transaction; from services import ResearchJobService, ResearchAgentExecutor, ResearchWorker; from web.server import serve"`
- **THEN** the command exits with status 0 and no import error

#### Scenario: Deep imports succeed

- **WHEN** running `python -c "from core.paths import ProjectPaths; from core.jsonio import read_json, write_json; from core.queue import ShardQueue, split_matrix; from core.state import ProgressStore, ProgressEventInput, InMemoryProgressStore; from domain.validation import ChallengeValidator; from domain.seeds import SeedStore; from domain.reports import merge_reports; from persistence.engine import create_engine_from_env; from persistence.session import SessionFactory; from persistence.models.research import GenerationRequest, ResearchRun; from persistence.models.progress import ProgressEvent, ProgressSnapshot; from persistence.repositories.research import ResearchRepository; from persistence.repositories.progress import PostgresProgressStore; from services.research_job_service import ResearchJobService; from services.research_agent_executor import ResearchAgentExecutor; from services.research_worker import ResearchWorker"`
- **THEN** the command exits with status 0 and no import error
