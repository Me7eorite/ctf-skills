## Why

The repo already has two halves of the delivery story but the bridge between
them is missing:

- `delivery-spec/交付格式规范.md` v2 defines a precise `资源包/` layout: per
  challenge zip bundles under `工具/` and `题库资源/deploy/{,enclosure/,report/}`,
  PDF writeups, docker image tars under `虚拟机资源/docker-tar/`, and two xlsx
  inventories.
- `work/challenges/<cat>/<id>-<slug>/` holds everything an author needs as
  raw material — `challenge.yml`, `metadata.json`, `writeup/wp.md`,
  `solve/`, `validate.sh`, `dist/<artifact>` for re, and the `deploy/` tree
  for web/pwn.

There is no code today that converts the second into the first. A grep over
`src/` and `scripts/` for `ctf-overview`, `镜像模板`, `docker-tar`,
`enclosure`, `资源包` returns nothing. Operators are expected to zip and
render PDFs by hand, which is tedious for a single shard and unworkable for
the 50-100 challenge batches the project is designed for. The end-to-end
"hermes generates → validator confirms → handoff" flow stops one step short.

This change introduces a single packing step that walks `work/challenges/`,
emits the `资源包/` tree per spec v2, and produces both xlsx inventories.

## What Changes

- New `src/packing.py` module that:
  - Walks `work/challenges/<cat>/<id>-<slug>/` for every challenge with a
    `metadata.json`.
  - For each challenge, emits a `工具/js-{prefix}-{id}exp.zip` containing
    `wp.md` (from `writeup/`) and the contents of `solve/`.
  - Emits an `题库资源/deploy/enclosure/js-{prefix}-{id}.zip` according to the
    delivery-spec category table (crypto/re/stego/forensics/misc/etc.:
    required; web: skipped; pwn: optional via flag).
  - For containerized categories (web, pwn), zips the working `deploy/`
    tree into `题库资源/deploy/js-{prefix}-{id}.zip` AND emits a tar under
    `虚拟机资源/docker-tar/<id>[<port>]-<YYYYMMDD>.tar` via `docker save`.
    Containerized packs are skipped with a clear warning when the docker
    CLI is unavailable; the rest of the bundle still produces.
  - Renders `writeup/wp.md` to a Chinese-safe PDF at
    `题库资源/deploy/report/js-{prefix}-{id}.pdf`. Prefixes follow the
    delivery table, including `reverse` for the internal `re` category.
- Aggregates:
  - `题库资源/ctf-overview.xlsx` — one row per challenge with the columns
    declared in spec §4.6.1.
  - `虚拟机资源/镜像模板.xlsx` — one row per docker-tar file.
- New CLI subcommand: `challenge-factory pack [--out PATH] [--include-pwn-attachments] [--skip-docker] [--require-docker]`.
  Default `--out` is `work/资源包/` so the bundle ships under the
  existing gitignored `work/` tree.
- New tests in `tests/test_packing.py` covering: per-category zip emission,
  enclosure inclusion/exclusion rules, xlsx columns, PDF file is produced
  and non-empty, docker-save skip path.

Not in scope: changing the agent's working-tree format, regenerating tars
when the underlying image hasn't been rebuilt, or signing/checksumming the
bundle. Those are follow-up changes.

## Capabilities

### New Capabilities

- `delivery-bundle`: covers the conversion of the agent's working tree under
  `work/challenges/` into the `资源包/` layout defined by delivery format
  v2, including per-challenge zip bundles, PDF writeup rendering, docker
  image tar export, and the two xlsx inventories.

### Modified Capabilities

<!-- None. The existing re-target-platforms capability stays untouched. -->

## Impact

- Code: new `src/packing.py`, additions to `src/cli.py` (new subcommand)
  and `src/paths.py` (output root). No changes to `hermes.py`,
  `validation.py`, `state.py`, `webserver.py`, `dashboard.py`.
- Dependencies: add `openpyxl` (xlsx) and ReportLab (PDF). The design records
  why ReportLab's built-in CJK CID font is preferred over renderers that need
  system libraries or external executables.
- Runtime: new step requires the docker daemon for containerized
  categories. The packer must degrade gracefully when docker is absent.
- Tests: `tests/test_packing.py` adds roughly 8 cases. `pyproject.toml`
  gains the two new runtime dependencies.
- No breaking changes — `pack` is additive. Existing `run`, `validate`,
  `serve` commands behave identically.
