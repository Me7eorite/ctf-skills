## Why

Reverse Engineering challenges are currently locked to `linux/amd64`: the
shard prompt says "Default to a Linux amd64 ELF" and explicitly flags
"a host-native macOS or aarch64 ELF is a failed build", and `validation.py`
only enforces architecture when the expected value is `amd64`/`x86_64`. The
ELF machine detector in the same file already knows arm and aarch64 but the
gate never opens for them. We want to support `linux/arm64` (and `linux/arm`)
re challenges so authors can ship cross-arch crackmes and so the matrix can
exercise non-x86 toolchains.

This is also the first end-to-end OpenSpec change in this project — the
scope is intentionally small so we get a real flow validation before tackling
larger churn.

## What Changes

- `ChallengeValidator.contract_errors` enforces the architecture gate for
  `arm64`/`aarch64` and `arm`/`armv7` expectations, not only `amd64`. Wrong
  machine codes for any of those expected values surface as a contract error
  with the same message shape as today.
- `prompts/shard_prompt.md` is updated to (a) keep `linux/amd64` as the
  default when `target_platform` is absent, (b) accept `linux/arm64` and
  `linux/arm` as valid `target_platform` values, and (c) drop the "aarch64
  ELF is a failed build" sentence in favor of the rule "must match the
  matrix-declared `target_platform`".
- `matrix.example.jsonl` gains one `re-0003` entry that targets
  `linux/arm64`, so the smoke flow covers the new path.
- Tests in `tests/test_validation.py` cover arm64-expected vs arm64-actual
  (passes), arm64-expected vs x86_64-actual (fails with the new error), and
  ensure the existing amd64 path keeps its behavior.

Not in scope: actually changing build infrastructure to run arm64
cross-compilers. Authors are still responsible for producing a real arm64
ELF; the project just stops blocking them.

## Capabilities

### New Capabilities

- `re-target-platforms`: covers which `target_platform` values RE challenges
  may declare in the matrix and how the validator enforces the produced ELF
  matches that declaration.

### Modified Capabilities

<!-- None — this is the first OpenSpec change so there are no existing specs to
amend. -->

## Impact

- Code: `src/validation.py` (architecture gate), `prompts/shard_prompt.md`
  (agent contract), `matrix.example.jsonl` (example entry).
- Tests: `tests/test_validation.py` (3 new cases).
- No new dependencies. No breaking changes — existing amd64 challenges
  continue to validate exactly as before.
- The hermes agent contract widens (more allowed `target_platform` values)
  but does not break any existing prompt invocation.
