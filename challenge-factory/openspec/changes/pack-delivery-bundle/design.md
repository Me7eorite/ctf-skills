## Context

`delivery-spec/交付格式规范.md` v2 (in this repo's sibling `delivery-spec/`)
is the contract for what gets handed to organizers. The contract is rich:
six output slots (`工具/`, three under `题库资源/deploy/`, two under
`虚拟机资源/`), specific naming with category prefixes (`js-{type}-{id}.zip`),
a docker tar filename template (`{name}[{port}]-{YYYYMMDD}.tar`), two xlsx
inventories whose column lists are spelled out, and a hard requirement that
the writeup output be in Chinese.

What the project has today:

- `work/challenges/<cat>/<id>-<slug>/` — produced by the hermes runner; per
  the shard prompt it always contains `metadata.json`, `validate.sh`,
  `writeup/wp.md`, `solve/solve.py`, plus category-specific bits
  (`deploy/...` for web/pwn, `dist/<artifact>` for re).
- A `ProjectPaths` dataclass that owns every filesystem location.
- A flat `src/` layout with one module per concern, exercised by
  `tests/test_<module>.py`.

What the project does not have:

- Any pdf renderer, zip builder, or xlsx writer.
- Any reference to `docker save`, `资源包`, or `镜像模板`.

The shape of the new code should match the existing project: one new module,
one new CLI subcommand, one new test module.

## Goals / Non-Goals

**Goals:**

- A single invocation `challenge-factory pack` produces a complete `资源包/`
  tree that conforms to delivery spec v2 for every challenge currently
  under `work/challenges/` with `metadata.json` and `build_status: passed`.
- Per-category rules from spec §4.3 are honored (crypto/re/stego required
  enclosure, web skipped, pwn optional).
- The PDF writeup renders Chinese correctly out of the box on a clean
  developer machine — no manual font fiddling for the common case.
- The packer degrades gracefully when docker is unavailable (skips tar +
  `镜像模板.xlsx` rows, prints a clear warning, still emits the rest).
- Tests cover the rules above using fixture challenges similar to the
  ones already used in `tests/test_validation.py`.

**Non-Goals:**

- Incremental packing (only re-pack changed challenges). Pack is idempotent
  but full each run.
- Signing, checksums, or upload to a release server.
- Backfilling tars for already-built images that aren't in the local
  docker daemon's image list.
- Validating the rendered PDF's visual quality beyond "file exists, has
  bytes, opens as a valid PDF object stream".

## Decisions

### D1. New module is `src/packing.py`; the CLI gains a `pack` subcommand

Matches the existing layout. The module exposes one `Packer` class that
takes a `ProjectPaths` and a `PackerOptions` and runs to completion. The
CLI subcommand wires argparse → `Packer.pack(out_dir)`.

**Alternative considered:** a `scripts/pack_delivery.py` standalone. Rejected
because the project's convention puts production code under `src/`; `scripts/`
is reserved for one-off prep (see `scripts/prepare_hermes_home.py`). A
script-only approach also makes testing harder and hides the new entry
point from the dashboard.

### D2. PDF renderer is ReportLab with a built-in CJK CID font

Four candidates considered:

| Renderer | Pros | Cons |
| --- | --- | --- |
| WeasyPrint | Strong HTML/CSS rendering | Requires Pango/GTK shared libraries on clean Windows and some CI images |
| pandoc + xelatex | High-quality typesetting, theming | Heavy install (LaTeX), CJK fonts a per-platform nightmare, shells out |
| markdown-pdf (chromium) | Pretty defaults | Needs a chromium binary; brittle in CI |
| **ReportLab** | Pure Python wheel, built-in `STSong-Light` CJK CID font, no subprocess or system library | Simpler Markdown styling |

We pick **ReportLab** because an implementation smoke test on a clean Windows
machine proved that the WeasyPrint wheel still needs `libgobject`/Pango.
ReportLab renders headings, paragraphs, and fenced code directly from the
Markdown source and uses its built-in `STSong-Light` CID font for Chinese.
The accepted trade-off is deliberately modest Markdown styling in exchange
for a renderer that works after `uv sync` on Windows, macOS, Linux, and CI.

### D3. The 'name' segment of zip filenames is the challenge ID

Spec examples use `js-crypto-rsa_wiener_001.zip` — the `rsa_wiener_001` part
is descriptive. The repo's matrix only stores `id` (e.g. `re-0001`) and
`title` ("Xor Badge"). We use `id` verbatim — stable, kebab-case-safe, free
of CJK title noise. An author who wants a different display name in the
zip can set `delivery_name` in `metadata.json`; the packer prefers that
when present.

**Alternative considered:** slug from directory name (`re-0001-xor-badge`).
Rejected — the directory slug duplicates `id` and adds title noise that
the spec example does not show.

### D4. Docker tar is `docker save` against `{id}:{date}`; degrade if docker missing

The shard prompt expects the image to be tagged `{name}:{date}` after a
`docker load`. We use the same convention on the producer side: the packer
shells out `docker save -o ...tar {id}:{date}`. If the docker CLI is not
on PATH, the packer:

1. Logs a single warning at the start of containerized processing.
2. Skips both the `docker-tar/...` files and the corresponding rows in
   `镜像模板.xlsx`.
3. Still emits the rest of the bundle.

A `--require-docker` flag turns the skip into a hard error for CI use.

### D5. Output root defaults to `work/资源包/`

`work/` is already gitignored and operators are used to seeing run-time
output there. Letting users override with `--out` keeps it scriptable. We
do NOT default to a repo-relative path that pollutes the working tree.

### D6. Each challenge's enclosure follows the spec §4.3 category table

A small `ENCLOSURE_RULES` dict in `packing.py`:

```python
ENCLOSURE_RULES = {
    "crypto": "required",
    "web": "skip",
    "pwn": "optional",
    "re": "required",
    "stego": "required",
    ...
}
```

Building an enclosure means: collect everything an author placed under
`dist/` or `attachments/` (excluding `metadata.json`, `solve/`, `writeup/`,
`validate.sh`, and the `deploy/` tree) into a single zip. For re-category
challenges that's typically just `dist/<artifact>`. The `--include-pwn-
attachments` flag is the explicit opt-in for pwn (default skip).

## Risks / Trade-offs

- **[Risk] ReportLab does not implement arbitrary Markdown extensions.** →
  Mitigation: support the delivery writeup's common headings, paragraphs,
  lists/tables as readable text, and fenced code. The source `wp.md` remains
  the canonical richly formatted artifact in the tools zip.
- **[Risk] An author hand-edited `wp.md` to English.** → Mitigation:
  the packer scans the rendered text for at least one CJK code-point and
  emits a P1 warning naming the file. It does not block the run — the
  delivery spec says "正文必须中文" but enforcing it strictly belongs in
  the validator, not the packer.
- **[Risk] Local docker images have stale tags.** → Mitigation: the
  packer reads the build tag from `metadata.docker_image` when present,
  otherwise constructs `{id}:{YYYYMM}`. A missing image fails the tar
  step for that challenge and is reported in the run summary, not the
  silent skip.
- **[Trade-off] Full re-pack each run.** Acceptable while batches are
  ≤100 challenges. If runtime becomes an issue, a `--changed-since`
  flag is the natural extension.
- **[Trade-off] New dependency surface (ReportLab, openpyxl).**
  The project deliberately stayed small until now. The spec v2 mandates
  PDF + xlsx; either we wear the deps or we tell operators to do it by
  hand. We pick the deps.

## Migration Plan

No state migration. The packer is a new endpoint; nothing else changes.
Operators run `uv sync` after pulling this change (to pick up the new
deps), then `uv run challenge-factory pack` whenever they want a fresh
bundle.

Rollback is removing the `pack` subcommand and the `packing.py` module;
no other module depends on them.

## Open Questions

- Should the packer also emit a top-level `README.md` summarizing the
  bundle (challenge count, categories, generated_at)? Lean toward yes,
  but it's not in delivery spec v2. Decision deferred until the first
  real operator uses the output.
- Does spec v2 want a stable hash file alongside each zip for integrity?
  Not currently — out of scope here, easy follow-up.
