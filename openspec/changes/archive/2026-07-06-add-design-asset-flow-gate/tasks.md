## 1. Prompt and Schema

- [x] 1.1 Extend `src/services/design_prompt.py` output schema with
  `difficulty_reason`, `shortcut_closure`, and `fingerprint`.
- [x] 1.2 Keep existing `asset_flow` schema and update guidance to reject
  generic assets as effective transitions.
- [x] 1.3 Update prompt invariants so medium+ designs must populate
  `difficulty_reason`, `shortcut_closure`, and `fingerprint`.

## 2. Validation

- [x] 2.1 Extend `src/domain/design/difficulty.py` to require medium+
  `difficulty_reason`.
- [x] 2.2 Extend `src/domain/design/difficulty.py` to require medium+
  `shortcut_closure`.
- [x] 2.3 Extend `src/domain/design/difficulty.py` to require medium+
  `fingerprint`.
- [x] 2.4 Reject generic/filler produced assets and generic dependency text
  when counting effective asset-flow transitions.

## 3. Tests

- [x] 3.1 Add tests rejecting medium without `difficulty_reason`.
- [x] 3.2 Add tests rejecting medium without `shortcut_closure`.
- [x] 3.3 Add tests rejecting medium without complete `fingerprint`.
- [x] 3.4 Add tests rejecting generic assets such as `access`.
- [x] 3.5 Update service-level fake Design output to satisfy the new medium+
  contract.

## 4. Verification

- [x] 4.1 Run focused design-domain/prompt/collapse/service tests.
- [x] 4.2 Run focused Ruff checks for touched files.
- [ ] 4.3 Run mypy successfully.
  - Blocked: current environment reports unrelated SQLAlchemy missing-stub and
    existing project type errors outside this change.
- [x] 4.4 Run `uv run openspec validate add-design-asset-flow-gate --strict`.

