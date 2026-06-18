# CTF Challenge Factory

Challenge Factory is a file-backed queue and PostgreSQL-observed control plane for
generating synthetic Web, Pwn, and Reverse Engineering challenges with Hermes,
backed by PostgreSQL for the research and design planning subsystems.

It combines the repository's `skills/design-challenges` guidance with explicit
technology profiles, parallel shard processing, artifact validation, and a
hand-rolled token-CSS dashboard.

## Project Structure

```text
ctf-skills/
├── src/
│   ├── cli.py              # command composition root (argparse)
│   ├── core/               # paths, JSON I/O, progress contracts, shard queue
│   ├── domain/             # DTOs, seeds, validation, report aggregation
│   ├── hermes/             # prompt rendering and Hermes subprocess execution
│   ├── packing/            # delivery bundle packing subsystem
│   ├── persistence/        # PostgreSQL engine, SQLAlchemy models, repositories
│   ├── services/           # cross-subsystem orchestration (research, design tasks)
│   └── web/                # FastAPI dashboard, HTTP adapters, static UI
├── alembic/                # PostgreSQL schema migrations
├── skills/                 # reusable CTF authoring skills
├── docs/
│   ├── architecture.md
│   └── delivery-formats/   # versioned delivery specifications and samples
│       └── ctf-v2/
├── tests/
│   ├── app/                # application unit tests
│   └── skills/             # skill structure and security tests
├── prompts/                # Hermes authoring contracts
├── tools/                  # bundled build tools and maintenance scripts
│   └── scripts/
├── openspec/               # project specifications and changes
├── work/                   # generated runtime state
├── .hermes/                # local Hermes state and credentials
├── generation-profiles.json
├── matrix.example.jsonl
└── pyproject.toml
```

`src/` uses a layered package layout with `cli.py` as the composition root.
Lower-level packages do not import higher-level adapters. `tools/scripts/`
contains application setup and repository maintenance tools. Generated challenges, logs,
reports, and queue files remain under the gitignored `work/` directory; local
Hermes state remains under the gitignored `.hermes/`.

## Quick Start

```bash
uv sync
uv run challenge-factory init
uv run python tools/scripts/prepare_hermes_home.py

uv run challenge-factory split \
  --matrix matrix.example.jsonl \
  --size 3

uv run challenge-factory run \
  --worker hermes-01 \
  --loop \
  --validate

uv run challenge-factory merge-reports
uv run challenge-factory pack --skip-docker
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

The **种子配置** view manages the matrix-compatible inputs used for generation.
Create or edit Web/Pwn/Reverse seeds, put category-specific values such as
`runtime`, `framework`, `compiler`, `mitigations`, or `target_platform` in the
advanced JSON field, choose a shard size, then click **生成分片**. The resulting
files enter the normal pending queue and can be processed by **启动 Worker**.

Hermes reports `design`, `implement`, `build`, `validate`, and `document`
events through the local `progress` command. Runner-owned claim, failure, and
completion events provide a fallback even when an agent exits unexpectedly.
The append-only events and latest snapshots are stored in PostgreSQL; shard
directories remain the queue source of truth. After pulling the progress-store
migration, run `uv run alembic upgrade head` and then
`uv run python tools/scripts/cleanup_sqlite_state.py` to remove legacy local
SQLite state files. Historical progress events are not migrated or
reconstructed.

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

## Delivery Bundle

```bash
uv run challenge-factory pack
uv run challenge-factory pack --out work/资源包 --skip-docker
uv run challenge-factory pack --include-pwn-attachments --require-docker
```

The packer selects challenges with `build_status: passed` and emits the v2
handoff layout under `work/资源包/`: per-challenge tool, deployment,
enclosure, and PDF files plus `ctf-overview.xlsx`, Docker image tars, and
`镜像模板.xlsx`. Docker absence is a warning unless `--require-docker` is set.

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
