# CTF Challenge Factory

Challenge Factory is a file-backed queue and SQLite-observed control plane for
generating synthetic Web, Pwn, and Reverse Engineering challenges with Hermes.

It combines the repository's `skills/design-challenges` guidance with explicit
technology profiles, parallel shard processing, artifact validation, and a
Tailwind dashboard.

## Project Structure

```text
challenge-factory/
├── src/
│   ├── cli.py          # command definitions only
│   ├── paths.py        # project filesystem locations
│   ├── jsonio.py       # JSON persistence helpers
│   ├── shards.py       # matrix splitting and queue transitions
│   ├── hermes.py       # prompt rendering and Hermes execution
│   ├── validation.py   # artifact and EXP validation
│   ├── reports.py      # report aggregation
│   ├── dashboard.py    # dashboard queries and task actions
│   ├── webserver.py    # HTTP transport
│   └── static/         # Tailwind UI
├── tests/                  # package unit tests
├── docs/                   # architecture documentation
├── scripts/
│   └── prepare_hermes_home.py
├── prompts/                # Hermes authoring contracts
├── work/                   # generated runtime state
├── generation-profiles.json
├── matrix.example.jsonl
└── pyproject.toml
```

`src/` uses a flat application-module layout so reviewers can see every major
component immediately without another package-directory level. `scripts/` is
reserved for one-off environment preparation. Generated challenges, logs,
reports, and queue files remain under `work/`.

## Quick Start

```bash
uv sync
uv run challenge-factory init
uv run python scripts/prepare_hermes_home.py

uv run challenge-factory split \
  --matrix matrix.example.jsonl \
  --size 3

uv run challenge-factory run \
  --worker hermes-01 \
  --loop \
  --validate

uv run challenge-factory merge-reports
```

Dry-run prompt generation:

```bash
uv run challenge-factory run --worker dry-01 --dry-run
```

## Dashboard

```bash
uv run challenge-factory serve
```

Open [http://127.0.0.1:4173](http://127.0.0.1:4173).

The dashboard provides queue, challenge, build, validation, log, and live
per-challenge pipeline views. It can also start one local worker, re-run
validation, and retry failed shards.

Hermes reports `design`, `implement`, `build`, `validate`, and `document`
events through the local `progress` command. Runner-owned claim, failure, and
completion events provide a fallback even when an agent exits unexpectedly.
The append-only events and latest snapshots are stored in
`work/state.sqlite3`; shard directories remain the queue source of truth.
If `work/` is not writable, the server and workers use the same deterministic
database under the operating-system temporary directory and show a warning in
the progress view.

Workers only claim `pending` shards. The shard view provides a requeue action
for failed shards and for orphaned `running` shards when no local task is
active.

Manual progress event:

```bash
uv run challenge-factory progress \
  --shard web-0001-0003.worker.json \
  --challenge web-0001 \
  --worker worker-01 \
  --stage build \
  --status running \
  --message "Building the pinned Docker image"
```

## Validation

```bash
uv run challenge-factory validate
uv run challenge-factory validate --filter re-0001
```

Validation checks:

1. Web challenges include the required single-service `deploy/` structure.
2. Re and Pwn ELF challenges contain an actual compiled ELF artifact.
3. `metadata.json` records a successful build.
4. `validate.sh` runs the real reference exploit or solver.
5. The recovered flag matches `metadata.json`.

## Parallel Workers

Workers claim shards through atomic file moves, so separate shells can process
different shards safely:

```bash
uv run challenge-factory run --worker hermes-01 --loop --validate
uv run challenge-factory run --worker hermes-02 --loop --validate
```

Recommended shard sizes:

- Web: 5-10
- Reverse: 5-10
- Pwn: 3-5

See [docs/architecture.md](docs/architecture.md) for module responsibilities
and dependency direction.
