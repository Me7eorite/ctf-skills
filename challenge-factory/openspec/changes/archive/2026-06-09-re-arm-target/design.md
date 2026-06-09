## Context

`ChallengeValidator.contract_errors` in `src/validation.py` is the only
runtime gate that ties a challenge's declared `architecture` /
`target_platform` to the actual ELF on disk. Today the gate is asymmetric:

- `elf_machine()` already returns `"x86_64"`, `"aarch64"`, `"arm"`, `"x86"`,
  or `machine_<n>`.
- The gate body at `validation.py:168-178` only triggers when the expected
  architecture is in `{"amd64", "x86_64"}`. Anything else — including
  `arm64` and `arm` — slips through unvalidated.

The shard prompt at `prompts/shard_prompt.md:96,115-122` doubles down on the
amd64 default and tells the agent "a host-native macOS or aarch64 ELF is a
failed build". On Apple Silicon dev machines this discourages authors from
trying arm64 outright, even when their toolchain could produce it.

The matrix already carries a `target_platform` field (`re-0001`,
`re-0002`), so widening the contract is just a matter of accepting more
values and gating them symmetrically.

## Goals / Non-Goals

**Goals:**

- Authors can declare `target_platform: linux/arm64` (or `linux/arm`) on a
  re challenge and have the validator enforce that the produced ELF actually
  matches.
- The prompt instructs the agent on the same rule, without making any
  particular architecture the default.
- One arm64 row in `matrix.example.jsonl` so `split` + `run --dry-run` smoke
  tests exercise the new path.

**Non-Goals:**

- Setting up arm64 cross-compilation infrastructure. The agent is still
  responsible for producing a real arm64 ELF (e.g. via QEMU, Docker
  `--platform`, or a Linux/arm64 host).
- Extending support to non-ELF target formats (wasm, jar, container).
- Pwn-category arm support. Pwn challenges share the gate but also need
  service-side arm runtime work; that's a separate change.

## Decisions

### D1. Architecture gate becomes table-driven

Replace the `if expected_architecture in {"amd64", "x86_64"}:` branch with a
mapping from expected token → set of acceptable ELF machine labels:

```python
ARCH_ACCEPTS = {
    "amd64": {"x86_64"},
    "x86_64": {"x86_64"},
    "arm64": {"aarch64"},
    "aarch64": {"aarch64"},
    "arm": {"arm"},
    "armv7": {"arm"},
}
```

When the expected token is in this map, every ELF artifact must report a
machine label in the corresponding set. Tokens outside the map (e.g.
`mips`, future architectures) are left unenforced today rather than
silently failing — the gate is permissive at the edges, strict where it
knows the answer. This preserves the current behavior for unknown values.

**Alternative considered:** a list of `(expected_pattern, machine_label)`
tuples. Rejected because the table maps cleanly to one-line lookups and
the asymmetry (amd64 → x86_64) is data, not control flow.

### D2. Error message stays in the existing shape

Today's failure says `"ELF artifact architecture is not x86_64: ..."`. The
new message uses the *expected* canonical label so test assertions stay
local to each architecture:

```
ELF artifact architecture is not {expected_canonical}: <paths>
```

where `expected_canonical` is the first item of `ARCH_ACCEPTS[expected]`
(picked deterministically). This keeps the error grep-able and lets the
existing amd64 test continue to match.

### D3. Prompt drops the amd64-or-die language but keeps amd64 as the default

The prompt currently has two coupled claims: "default to amd64 when
`target_format` is absent" and "aarch64 ELF is a failed build". We keep
the first (so absent values still have a defined behavior) and rewrite the
second to: "the produced ELF MUST match the matrix `target_platform`;
common values are `linux/amd64`, `linux/arm64`, `linux/arm`." This makes
the prompt's check symmetric with the validator gate from D1.

### D4. Matrix gains one arm64 entry, not a full sweep

We add a single `re-0003` row using arm64 + a different template, not a
mirror of every existing amd64 challenge. The point is flow coverage, not
content. Authors who want arm64 challenges in production will add their
own rows.

## Risks / Trade-offs

- **[Risk] An agent without an arm64 toolchain silently builds amd64.** →
  Mitigation: the gate from D1 fails the contract with a clear message,
  so the shard fails fast with a build-status entry the dashboard can
  surface. Authors should not commit such an artifact.
- **[Risk] `elf_machine` reports the host architecture for a macOS Mach-O
  that someone passed through.** → Already mitigated: `is_elf()` rejects
  non-ELF magic, so Mach-O / PE artifacts don't enter the gate.
- **[Trade-off] Unknown architectures stay unenforced.** Adding them later
  is a one-line `ARCH_ACCEPTS` change; until then we keep the current
  permissive behavior rather than guessing.

## Migration Plan

No migration needed. All existing matrix entries declare `linux/amd64`
which continues to validate identically. The change is purely additive.

If a downstream consumer was relying on the old error message verbatim,
the canonical-label suffix now varies by expected architecture. Internal
tests are updated in lockstep.
