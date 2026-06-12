# CTF Challenge Factory

Challenge Factory is a file-backed queue and SQLite-observed control plane for
generating synthetic Web, Pwn, and Reverse Engineering challenges with Hermes.

It combines the repository's `skills/design-challenges` guidance with explicit
technology profiles, parallel shard processing, artifact validation, and a
Tailwind dashboard.

## Project Structure

```text
ctf-skills/
├── src/
│   ├── cli.py              # command composition root
│   ├── core/               # paths, JSON I/O, SQLite state, shard queue
│   ├── domain/             # seeds, validation, report aggregation
│   ├── hermes/             # prompt rendering and Hermes execution
│   ├── packing/            # delivery bundle packing subsystem
│   └── web/                # dashboard service, HTTP transport, static UI
├── skills/                 # reusable CTF authoring skills
├── delivery-format/        # delivery specification and sample resources
├── tests/
│   ├── app/                # application unit tests
│   └── skills/             # skill structure and security tests
├── docs/                   # architecture documentation
├── scripts/
│   ├── prepare_hermes_home.py
│   └── skill_security_auditor.py
├── prompts/                # Hermes authoring contracts
├── tools/                  # bundled build tools
├── openspec/               # project specifications and changes
├── work/                   # generated runtime state
├── .hermes/                # local Hermes state and credentials
├── generation-profiles.json
├── matrix.example.jsonl
└── pyproject.toml
```

`src/` uses a layered package layout with `cli.py` as the composition root.
Lower-level packages do not import higher-level adapters. `scripts/` contains
application setup and repository maintenance tools. Generated challenges, logs,
reports, and queue files remain under the gitignored `work/` directory; local
Hermes state remains under the gitignored `.hermes/`.

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
  --timeout 2500

uv run challenge-factory merge-reports
uv run challenge-factory pack --skip-docker
```

Validation is mandatory for non-dry-run `run`; the legacy `--validate` flag has
been removed. Use `--timeout SECONDS` to override the default 2500-second
Hermes subprocess timeout (precedence: CLI flag > `HERMES_TIMEOUT` env > 2500).

Dry-run prompt generation:

```bash
uv run challenge-factory run --worker dry-01 --dry-run
```

## Dashboard

```bash
uv run challenge-factory build-ui
uv run challenge-factory serve
```

Open [http://127.0.0.1:4173](http://127.0.0.1:4173).

## Frontend development

The console SPA lives in `frontend/` (Vue 3 + Vite + TypeScript + Tailwind). Node 20
LTS is pinned via `.nvmrc`.

```bash
nvm use
cd frontend && npm install
npm run dev       # vite dev server, proxies /api/* to FastAPI on 4173
npm run build     # emits hashed assets to src/web/static/dist/
npm run test      # vitest unit tests
```

The built output under `src/web/static/dist/` is committed so operators who only
run `uv sync` can serve the SPA without Node installed. `Makefile` exposes
`ui-dev`, `ui-build`, `ui-test` shorthands at the repo root.

The dashboard provides queue, challenge, build, validation, log, live
per-challenge pipeline, and agent trace views. It can also start one local
worker, re-run validation, and retry failed shards.

The frontend is a static Next.js export. The generated files under
`src/web/static/dist/` are not committed; run `uv run challenge-factory
build-ui` before `serve` on a fresh clone. If the build artifact is missing,
`serve` still starts and `GET /` shows the exact build command.

Frontend development runs as two processes:

```bash
uv run challenge-factory serve

cd web
npm install
npm run dev
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). The Next dev server
proxies `/api/*` to FastAPI on port 4173.

Live demo mode:

```bash
uv run challenge-factory serve --demo
```

Demo mode replays a built-in matrix through the normal SQLite state plane and
trace stream without requiring Hermes or Docker. Mutating dashboard endpoints
return `409` with `{"ok": false, "message": "Demo mode is read-only"}`.

The **种子配置** view manages the matrix-compatible inputs used for generation.
Create or edit Web/Pwn/Reverse seeds, put category-specific values such as
`runtime`, `framework`, `compiler`, `mitigations`, or `target_platform` in the
advanced JSON field, choose a shard size, then click **生成分片**. The resulting
files enter the normal pending queue and can be processed by **启动 Worker**.

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
uv run challenge-factory run --worker hermes-01 --loop
uv run challenge-factory run --worker hermes-02 --loop
```

Recommended shard sizes:

- Web: 5-10
- Reverse: 5-10
- Pwn: 3-5

See [docs/architecture.md](docs/architecture.md) for module responsibilities
and dependency direction.
