## 1. Widen the architecture gate in the validator

- [x] 1.1 Add an `ARCH_ACCEPTS` table at module level in `src/validation.py` mapping `{amd64, x86_64, arm64, aarch64, arm, armv7}` to the matching ELF machine label sets.
- [x] 1.2 Replace the `if expected_architecture in {"amd64", "x86_64"}:` branch in `contract_errors` with a lookup against `ARCH_ACCEPTS`; unknown tokens stay unenforced.
- [x] 1.3 Update the error message to use the canonical expected label (e.g. `"ELF artifact architecture is not aarch64: ..."`) so each architecture has its own greppable string.

## 2. Update the shard prompt

- [x] 2.1 In `prompts/shard_prompt.md` rewrite the Reverse rules block to (a) keep `linux/amd64` as the default and (b) list `linux/arm64` and `linux/arm` as valid `target_platform` values.
- [x] 2.2 Remove the standalone sentence claiming `aarch64 ELF is a failed build`; replace it with "the produced ELF MUST match the matrix-declared `target_platform`".

## 3. Add an arm64 example to the matrix

- [x] 3.1 Append one `re-0003` row to `matrix.example.jsonl` with `target_platform: linux/arm64`, distinct from the existing two re entries.

## 4. Tests

- [x] 4.1 In `tests/test_validation.py` add an arm64-passes case using a fake ELF with the aarch64 machine byte.
- [x] 4.2 Add an arm64-expected-but-x86_64-built case asserting the new error message contains `aarch64`.
- [x] 4.3 Add an arm-expected-but-aarch64-built case asserting the error message contains `arm`.
- [x] 4.4 Confirm the existing amd64 case still passes unchanged.
- [x] 4.5 Run `.venv/bin/python -m pytest tests/` — all tests must pass.

## 5. Flow smoke test

- [x] 5.1 Run `uv run challenge-factory split --matrix matrix.example.jsonl --size 3` and confirm a shard containing `re-0003` lands in `work/shards/pending/`.
- [x] 5.2 Run `uv run challenge-factory run --worker dry-01 --dry-run` and confirm the rendered prompt under `work/logs/` mentions `linux/arm64`.

## 6. Archive

- [x] 6.1 `openspec validate re-arm-target` — must pass.
- [x] 6.2 `openspec archive re-arm-target` — moves the change under `openspec/changes/archive/` and syncs `openspec/specs/re-target-platforms/spec.md`.
