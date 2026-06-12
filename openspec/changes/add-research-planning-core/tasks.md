## 1. Database schema and migration

- [ ] 1.1 Add Alembic revision `0002_research_tables` (revises `0001_baseline`) defining the three enum types: `generation_request_status (draft|researching|researched|failed)`, `research_run_status (queued|running|completed|failed)`, `research_finding_kind (technique|variant|scenario|prerequisite)`.
- [ ] 1.2 Add `generation_requests` table: `id uuid pk`, `topic text not null`, `target_count int not null check (target_count > 0)`, `difficulty_distribution jsonb not null`, `runtime_constraints jsonb not null default '{}'::jsonb`, `status generation_request_status not null default 'draft'`, `created_at timestamptz not null default now()`, `updated_at timestamptz not null default now()`.
- [ ] 1.3 Add `research_runs` table: `id uuid pk`, `generation_request_id uuid not null references generation_requests(id) on delete cascade`, `status research_run_status not null default 'queued'`, `started_at timestamptz`, `finished_at timestamptz`, `error text`, `hermes_log_path text`, `created_at timestamptz not null default now()`. Index on `generation_request_id`.
- [ ] 1.4 Add `research_sources` table: `id uuid pk`, `research_run_id uuid not null references research_runs(id) on delete cascade`, `url text not null`, `title text not null`, `summary text not null`, `content_hash text not null`, `fetched_at timestamptz not null`, `raw_text_path text`. Index on `(research_run_id, content_hash)`.
- [ ] 1.5 Add `research_findings` table: `id uuid pk`, `research_run_id uuid not null references research_runs(id) on delete cascade`, `kind research_finding_kind not null`, `label text not null`, `summary text not null`. Index on `research_run_id`.
- [ ] 1.6 Add `research_finding_sources` join table: `finding_id uuid references research_findings(id) on delete cascade`, `source_id uuid references research_sources(id) on delete cascade`, `primary key (finding_id, source_id)`.
- [ ] 1.7 Write reversible `downgrade()` that drops tables in reverse dependency order (join → findings → sources → runs → requests) and drops the three enum types.
- [ ] 1.8 Run `DATABASE_URL=...challenge_factory tools/scripts/db.sh up` then `tools/scripts/db.sh current` and confirm `0002_research_tables (head)`. Run `tools/scripts/db.sh down` and confirm only `alembic_version` remains.

## 2. Domain DTOs and validators

- [ ] 2.1 Create `src/domain/research.py` with `@dataclass(frozen=True)` DTOs: `GenerationRequest`, `ResearchRun`, `ResearchSource`, `ResearchFinding`, plus enums `GenerationRequestStatus`, `ResearchRunStatus`, `ResearchFindingKind`, and the allowed `DIFFICULTY_LABELS = ("easy","medium","hard","expert")`.
- [ ] 2.2 Add `validate_distribution(target_count, distribution)` raising a typed `ResearchValidationError` when sum mismatches or labels are unknown; message names the mismatch.
- [ ] 2.3 Add `validate_finding(kind, source_ids)` raising `ResearchValidationError` when `source_ids` is empty or contains duplicates; message names the rule violated.
- [ ] 2.4 Add `tests/app/test_research_domain.py` covering valid distribution, mismatch sum, unknown label, finding-without-source, finding-with-duplicate-sources.

## 3. Persistence models

- [ ] 3.1 Create `src/persistence/models/__init__.py` re-exporting the public model classes. Add a shared declarative `Base` (subclass of `sqlalchemy.orm.DeclarativeBase`) in `persistence/models/base.py` so future revisions can register tables here without circular imports.
- [ ] 3.2 Create `src/persistence/models/research.py` with SQLAlchemy 2 mapped classes: `GenerationRequest`, `ResearchRun`, `ResearchSource`, `ResearchFinding`, `ResearchFindingSource` matching the table definitions from task 1. Use `Mapped[...]` annotations and PEP 695 / `Annotated` typed columns.
- [ ] 3.3 Wire `target_metadata = Base.metadata` in `alembic/env.py` so future autogenerate revisions detect drift.

## 4. Persistence repositories

- [ ] 4.1 Create `src/persistence/repositories/__init__.py` re-exporting the public repository classes.
- [ ] 4.2 Create `src/persistence/repositories/research.py::ResearchRepository(session)` with: `create_generation_request(...) -> GenerationRequest`, `get_generation_request(id) -> GenerationRequest | None`, `list_generation_requests(*, status=None) -> list[GenerationRequest]`, `create_research_run(generation_request_id) -> ResearchRun`, `mark_run_running(run_id, started_at) -> ResearchRun`, `mark_run_completed(run_id, finished_at, log_path) -> ResearchRun`, `mark_run_failed(run_id, finished_at, error, log_path) -> ResearchRun`, `add_source(run_id, *, url, title, summary, content_hash, fetched_at, raw_text_path=None) -> ResearchSource`, `create_finding(run_id, *, kind, label, summary, source_ids) -> ResearchFinding`, `get_run(id) -> ResearchRun | None`, `list_sources(run_id) -> list[ResearchSource]`, `list_findings(run_id) -> list[ResearchFinding]`. The repository SHALL call into `domain.research.validate_*` before any insert.
- [ ] 4.3 `create_generation_request` SHALL persist via the supplied session without committing. Caller owns the transaction. Same rule for every other write method.
- [ ] 4.4 `create_finding` SHALL insert the `research_findings` row and the `research_finding_sources` join rows together; if the session raises during join inserts, the caller's transaction rolls back the whole composite write.
- [ ] 4.5 Add `tests/app/test_research_repository.py` marked `@pytest.mark.postgres`: round-trip insert and list, cascade-delete from generation request through findings, finding-without-source rejection, finding-with-unknown-source-id integrity error, distribution validation propagation.

## 5. Filesystem layout for raw text and logs

- [ ] 5.1 Add `core/paths.py::ProjectPaths.research_sources` and `ProjectPaths.research_logs` pointing at `work/research/sources/` and `work/research/logs/`.
- [ ] 5.2 `ProjectPaths.initialize()` SHALL `mkdir(parents=True, exist_ok=True)` for both new directories so the first `research submit` does not race directory creation.

## 6. Hermes subprocess plumbing extraction (behavior-preserving)

- [ ] 6.1 Create `src/hermes/process.py` exposing `hermes_arguments() -> list[str]`, `apply_legacy_custom_provider(hermes_home: Path, environment: dict[str, str]) -> bool`, `remove_conflicting_custom_pool(hermes_home: Path) -> bool`, and `invoke(prompt: str, *, arguments: list[str], log_path: Path, cwd: Path, environment: dict[str, str], timeout: int) -> int`. These are the verbatim bodies extracted from `hermes/runner.py`.
- [ ] 6.2 Replace `HermesRunner._invoke`, `_hermes_arguments`, `_apply_legacy_custom_provider`, `_remove_conflicting_custom_pool` with calls into `hermes.process`. The runner keeps its current public method shape.
- [ ] 6.3 Run the full `uv run pytest tests/` suite and confirm zero regressions in `tests/app/test_hermes_runner*.py` / `tests/app/test_runner_*.py`.

## 7. Hermes Research Agent

- [ ] 7.1 Add `prompts/research_prompt.md` describing the input fields (topic, target_count, difficulty_distribution, seed_urls, runtime_constraints) and the output contract: a single JSON object with `sources[]` and `findings[]`. Document the schema of each entry and require every `findings[i].source_indices` to be a non-empty list of valid integer indices into `sources[]`. Include a worked example with at least one source and one finding referencing it.
- [ ] 7.2 Add `src/hermes/prompt.py::render_research_prompt(generation_request, seed_urls)` returning the rendered text. Reuse the existing prompt-rendering style.
- [ ] 7.3 Add `src/hermes/research.py::invoke_research_agent(prompt: str, *, log_path: Path, timeout: int, paths: ProjectPaths) -> tuple[int, str]` returning `(returncode, captured_stdout_text)`. Uses `hermes.process.invoke` underneath; stdout capture is separate from the log file (log file holds the same stream for operator inspection).
- [ ] 7.4 Add `tests/app/test_research_prompt.py` asserting the rendered output contains the JSON schema description, the source-index constraint phrase, and the worked example.

## 8. Services layer

- [ ] 8.1 Create `src/services/__init__.py` re-exporting `ResearchRunner`.
- [ ] 8.2 Create `src/services/research_runner.py::ResearchRunner(paths, repository_factory, hermes_invoke=hermes.research.invoke_research_agent)`. Method `execute(generation_request_id, *, seed_urls, timeout) -> ResearchRun`: open one `transaction()`, load the request, create a queued run, transition to running, render the prompt, call `hermes_invoke`, parse stdout JSON, validate every finding has ≥ 1 source via `domain.research.validate_finding`, persist all sources + findings via the repository, mark the run completed, return the loaded `ResearchRun` snapshot. On any exception inside the transaction: roll back, open a new transaction, persist the run as `failed` with the error message and the captured log path, re-raise nothing (the run id is the contract; status is the failure signal).
- [ ] 8.3 The runner SHALL write the Hermes log to `paths.research_logs / f"{run_id}.log"` and pass that path to `hermes.research.invoke_research_agent`.
- [ ] 8.4 The runner SHALL NOT import from `web`. Module-level imports SHALL only include stdlib, third-party, `persistence`, `hermes`, `domain`, and `core`.

## 9. CLI

- [ ] 9.1 Add `challenge-factory research` argparse subparser group in `src/cli.py`.
- [ ] 9.2 Subcommand `research submit --topic STR --count INT --difficulty LABEL:N[,LABEL:N...] [--seed-url URL]... [--timeout INT]`: creates a `generation_requests` row via the repository, transitions it to `researching`, spawns a `ResearchRunner.execute(...)` synchronously, prints the run id and final status to stdout, and exits 0 on `completed` / 1 on `failed`.
- [ ] 9.3 Subcommand `research show <generation_request_id>`: prints request fields, run status, source count, finding count grouped by `kind`, and the latest run's log path. Unknown id prints a clear error and exits 2.
- [ ] 9.4 Subcommand `research list [--status STATUS]`: prints id, topic, target_count, status, created_at, one per line.
- [ ] 9.5 Add `tests/app/test_research_cli.py` covering `--difficulty` parsing (valid + invalid), `submit` happy path with a stubbed runner, `submit` exit 1 on failed run, `show` and `list` rendering.

## 10. Web read endpoints

- [ ] 10.1 Add `GET /api/research/requests?status=...` to `src/web/server.py` returning a JSON array of generation requests.
- [ ] 10.2 Add `GET /api/research/requests/{id}` returning the request, the latest run, the source list, and the finding list (grouped by kind) as one JSON object. 404 on unknown id.
- [ ] 10.3 Add `tests/app/test_research_api.py` using the existing FastAPI test client to assert response shapes against a stubbed repository.

## 11. Dependency direction guardrail

- [ ] 11.1 Extend `tests/app/test_dependency_direction.py::INTERNAL_ROOTS` with `services`.
- [ ] 11.2 Extend `ALLOWED_IMPORTS` with `services: {persistence, hermes, domain, core}` and update `cli`, `web` to include `services` in their allow-lists.
- [ ] 11.3 Add rejection tests for `hermes -> services`, `services -> web`, `domain -> services`. Reuse the `find_violations()` helper introduced in the previous change.

## 12. Tests against the dev database

- [ ] 12.1 Add `tests/app/test_research_alembic.py` marked `@pytest.mark.postgres` mirroring `test_alembic_migrations.py`: upgrade to `0002_research_tables`, assert the five tables and three enums exist via `inspect`, downgrade to `0001_baseline`, assert only `alembic_version` remains.
- [ ] 12.2 Add `tests/app/test_research_runner.py` marked `@pytest.mark.postgres`: stub `hermes_invoke` to return canned JSON; assert state transitions and persisted row counts on the happy path; stub it to return invalid JSON and assert `research_runs.status == failed` with no source/finding rows.

## 13. Docs and project notes

- [ ] 13.1 Update `docs/persistence.md` adding a "Schema" section pointing at the new tables and the "≥1 source per finding" repository invariant.
- [ ] 13.2 Update `openspec/project.md` `Source layout` section to mention the new `services` and `persistence` packages and one-sentence description of when to use `services` (cross-subsystem orchestration with a transaction boundary).

## 14. Verification

- [ ] 14.1 Run `uv run ruff check`.
- [ ] 14.2 Run `uv run pytest tests/` and confirm Postgres-marked tests skip cleanly when `TEST_DATABASE_URL` is unset; non-Postgres tests pass.
- [ ] 14.3 Export `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.6.150:5432/challenge_factory_test`. Run `uv run pytest tests/ -m postgres` and confirm zero failures, zero skips.
- [ ] 14.4 Run `openspec validate add-research-planning-core --strict`.
- [ ] 14.5 Run the dry-run smoke: `challenge-factory research submit --topic "SQL injection sample" --count 4 --difficulty easy:2,medium:2 --seed-url https://example.com/sqli --timeout 60`. Either: a real Hermes runs end-to-end against the dev DB OR a stub Hermes binary returns canned JSON. Confirm a `research_runs` row appears with the expected status.
