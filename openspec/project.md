# Challenge Factory — Project Context

Read this file before writing any OpenSpec change proposal. It captures the
constraints, conventions, and non-obvious facts an AI agent needs to make
sensible design decisions for this project.

## What this project is

Challenge Factory is a file-backed queue + PostgreSQL-observed control plane that
drives the **hermes agent** to generate synthetic Web / Pwn / Reverse
Engineering CTF challenges. It:

1. Splits a JSONL matrix of requested challenges into shards under
   `work/shards/pending/`.
2. Stores approved challenge designs as `design_tasks`, then submits selected
   tasks into build attempts that create attributed shard files.
3. Lets one or more workers atomically claim shards, render a prompt, and run
   `hermes chat` in a subprocess to author the challenges.
4. Records per-stage progress events (queued → design → implement → build →
   validate → document → complete) into PostgreSQL `progress_events` and
   `progress_snapshots`.
5. Reconciles `build_attempts` against shard queue placement, worker progress,
   and produced artifact directories.
6. Validates each generated challenge by running `validate.sh` and checking
   that the recovered flag matches `metadata.json`.
7. Exposes a FastAPI dashboard at `http://127.0.0.1:4173` showing queue state,
   per-challenge pipeline, logs, and shard requeue controls.

The challenge artifacts produced must conform to `docs/delivery-formats/ctf-v2/`
(separate "delivery format" spec — that's product output, not dev process).

## Tech stack

- **Python ≥3.11**, managed with `uv`. Ruff targets `py312` syntax; pyright
  type-checks against 3.11.
- **FastAPI + uvicorn** for the dashboard HTTP layer (`src/web/server.py`,
  registered via `web.research_endpoints` / `web.design_task_endpoints`).
- **PostgreSQL + SQLAlchemy 2.x + Alembic** for the relational store
  (`src/persistence/`), holding research requests, runs, design tasks,
  challenge designs, and append-only progress events via
  `progress_events` / `progress_snapshots`. Connection is configured by
  `DATABASE_URL`; missing or unreachable PG is a hard failure for code paths
  that read persisted progress or domain repositories. Agent-side progress
  writes may use `--best-effort`, which warns and skips the write without
  creating any fallback store.
- **subprocess + atomic file renames** for the shard queue
  (`src/core/queue.py`), no external broker.
- **Hand-rolled CSS** under `src/web/static/css/` using a token + component
  layer system (`css/tokens.css`, `css/components/*.css`, `css/views/*.css`).
  No Tailwind.
- **Hermes agent CLI** (external, via `hermes` / `uvx hermes-agent`) is the
  thing this project drives — we don't own it, we render prompts and read
  back its outputs.

## Source layout

Current layout under `src/`:

| Module          | Responsibility |
| --------------- | -------------- |
| `cli.py`        | argparse entrypoints and command wiring |
| `core/`         | filesystem paths, JSON helpers, shard queue, reports, progress state |
| `domain/`       | DTOs and validation rules shared across adapters |
| `hermes/`       | prompt rendering and Hermes subprocess invocation |
| `persistence/`  | PostgreSQL engine/session setup, SQLAlchemy models, Alembic-backed repositories |
| `services/`     | cross-subsystem orchestration with transaction boundaries, for example research submit/claim/execute workflows |
| `web/`          | FastAPI dashboard app, static UI, and read-only HTTP adapters |

Application tests live under `tests/app/`; skill structure and security tests
live under `tests/skills/`. `pyproject.toml` configures pytest with
`pythonpath = ["src", "."]`. Run the suite with `uv run pytest`.

## Non-obvious conventions

- **Don't put dependencies in a second compose service.** The shard prompt and
  `validation.py` both assume a single-service `docker-compose.yml`; DBs /
  caches / queues belong in the base image or `_files/start.sh`.
- **Progress percent is computed from `(stage, status)` in `core/state.py` and
  reused by `persistence/repositories/progress.py`.**
  `failed/complete` is intentionally capped at 99 so a UI "stuck at 99" is a
  fingerprint for `complete + failed`, not a literal progress reading. Don't
  change this formula without checking `_percent` callers.
- **Hermes timeouts that come AFTER artifacts are produced are recoverable.**
  `HermesRunner.process_one` checks `_artifacts_complete` on returncode 124
  and lets the validator be the source of truth. If you change the timeout
  path, keep that recovery behavior.
- **Worker safety:** workers only claim shards under `work/shards/pending/`.
  An atomic `Path.replace` move into `running/` is the lock. The dashboard
  refuses to start a local worker if the pending queue is empty.
- **`progress` CLI subcommand is part of the hermes contract.** The shard
  prompt instructs the agent to call it before/after every stage. Don't
  rename it without updating `prompts/shard_prompt.md`.
- **CLI argparse must boot without a database.** `init`, `split`, `claim`,
  `validate`, `merge-reports`, and `pack` do not touch PG. `run`,
  `progress`, `durations`, and `serve` construct a PostgreSQL-backed
  `ProgressStore` only in their command handlers. `research` and `profile`
  query PostgreSQL only when the user actually enters those groups. Don't
  reintroduce a top-level PG lookup at `main()` or at argparse-build time.
- **DB-backed category codes are authoritative for research.**
  `challenge_categories.code` drives `research submit --category` choices;
  `core.queue.SUPPORTED_CATEGORIES` is the legacy hardcoded set for the shard
  pipeline. Divergence is real and surfaced by `_check_category_consistency`
  inside the `research` dispatcher.
- **Two unrelated "spec" directories exist.** `docs/delivery-formats/ctf-v2/` is product
  output format. `openspec/` (this directory) is dev-process change tracking.
  Don't conflate them.

## Workflows

- Quick smoke run: `uv sync && uv run challenge-factory init &&
  uv run challenge-factory split --matrix matrix.example.jsonl --size 3 &&
  uv run challenge-factory run --worker dry-01 --dry-run`.
- Dashboard: `uv run challenge-factory serve` (FastAPI on 4173).
- Tests: `uv run pytest`.

## Configuration knobs

- `BUILD_RECONCILER_POLL_SECONDS=5`: interval for the background build
  reconciler; missing, non-integer, or non-positive values fall back to 5.
- `BUILD_ATTEMPTS_LIST_DEFAULT_LIMIT=100`: default row cap for
  `GET /api/build-attempts` when the request omits `limit`.
- `BUILD_ATTEMPTS_LIST_MAX_LIMIT=500`: maximum accepted build-attempt list
  limit; larger requests are capped and return `X-Limit-Capped`.
- `RESEARCH_FAMILY_OTHER_WARN_RATIO=0.30`: research report threshold for a
  neutral warning when derived/stored `technique_family=other` exceeds the
  ratio.
- `generation-profiles.json` per-category `technique_quota`: soft cap for how
  many generated design tasks should use the same technique family before the
  planner records `family_quota_exceeded`.
- `generation-profiles.json` per-category `cooldown_window`: family cooldown
  window used by the greedy design-task planner before it relaxes to the next
  stable fallback.

## Areas with known churn (good change-proposal candidates)

- Hermes pipeline reliability (timeouts, claim handoff, partial-success
  classification). Bug 1 fixed; more to come.
- Dashboard surface (auth, multi-user, richer per-stage views).
- Future delivery format versions — add them under `docs/delivery-formats/`.
- New category support (IoT / Mobile / Blockchain / Crypto) beyond the
  current web/pwn/re trio.
- Multi-agent orchestration if we split design vs. implementation vs.
  validation across separate hermes sessions.

## When writing a change proposal

- Touch only `src/`, `tests/`, `prompts/`, `static/`, `docs/`, `tools/scripts/`
  unless the change explicitly targets `openspec/` itself.
- New features must come with a test in `tests/`.
- Don't add helper abstractions or backwards-compat shims that aren't earning
  their keep — the codebase prefers small, direct functions.
- Keep diffs small enough to review in one sitting. Split a big proposal into
  multiple sequenced changes if needed.
