# Challenge Factory — Project Context

Read this file before writing any OpenSpec change proposal. It captures the
constraints, conventions, and non-obvious facts an AI agent needs to make
sensible design decisions for this project.

## What this project is

Challenge Factory is a file-backed queue + SQLite-observed control plane that
drives the **hermes agent** to generate synthetic Web / Pwn / Reverse
Engineering CTF challenges. It:

1. Splits a JSONL matrix of requested challenges into shards under
   `work/shards/pending/`.
2. Lets one or more workers atomically claim shards, render a prompt, and run
   `hermes chat` in a subprocess to author the challenges.
3. Records per-stage progress events (queued → design → implement → build →
   validate → document → complete) into `work/state.sqlite3`.
4. Validates each generated challenge by running `validate.sh` and checking
   that the recovered flag matches `metadata.json`.
5. Exposes a FastAPI dashboard at `http://127.0.0.1:4173` showing queue state,
   per-challenge pipeline, logs, and shard requeue controls.

The challenge artifacts produced must conform to `docs/delivery-formats/ctf-v2/`
(separate "delivery format" spec — that's product output, not dev process).

## Tech stack

- **Python 3.13** (project requires ≥3.11), managed with `uv`.
- **FastAPI + uvicorn** for the dashboard HTTP layer
  (`src/webserver.py`, recently migrated from stdlib `http.server`).
- **SQLite** (WAL mode) for the append-only progress event store
  (`src/state.py`), with a temp-dir fallback when `work/` is not writable.
- **subprocess + atomic file renames** for the shard queue
  (`src/shards.py`), no external broker.
- **Tailwind** for the dashboard UI under `src/static/`.
- **Hermes agent CLI** (external, via `hermes` / `uvx hermes-agent`) is the
  thing this project drives — we don't own it, we render prompts and read
  back its outputs.

## Source layout

Flat module layout under `src/`:

| Module          | Responsibility |
| --------------- | -------------- |
| `cli.py`        | argparse entrypoints only |
| `paths.py`      | filesystem locations (`ProjectPaths`) |
| `jsonio.py`     | JSON read/write helpers |
| `shards.py`     | matrix splitting + atomic queue transitions |
| `hermes.py`     | prompt rendering + subprocess invocation |
| `state.py`      | SQLite progress events + snapshots |
| `validation.py` | artifact contract + reference-solve validation |
| `reports.py`    | per-shard report aggregation |
| `dashboard.py`  | read model + local task manager |
| `webserver.py`  | FastAPI app |
| `static/`       | dashboard UI |

Application tests live under `tests/app/`; skill structure and security tests
live under `tests/skills/`. `pyproject.toml` configures pytest with
`pythonpath = ["src", "."]`. Run the suite with `uv run pytest`.

## Non-obvious conventions

- **Don't put dependencies in a second compose service.** The shard prompt and
  `validation.py` both assume a single-service `docker-compose.yml`; DBs /
  caches / queues belong in the base image or `_files/start.sh`.
- **Progress percent is computed from `(stage, status)` in `state.py`.**
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
- **Two unrelated "spec" directories exist.** `docs/delivery-formats/ctf-v2/` is product
  output format. `openspec/` (this directory) is dev-process change tracking.
  Don't conflate them.

## Workflows

- Quick smoke run: `uv sync && uv run challenge-factory init &&
  uv run challenge-factory split --matrix matrix.example.jsonl --size 3 &&
  uv run challenge-factory run --worker dry-01 --dry-run`.
- Dashboard: `uv run challenge-factory serve` (FastAPI on 4173).
- Tests: `uv run pytest`.

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
