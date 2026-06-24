## Why

Medium/hard challenge designs can still inflate difficulty by listing multiple
techniques without proving that each step produces a concrete asset or
capability required by the next step. The design contract needs a deterministic
asset-flow gate before build/review layers can make reliable decisions.

## What Changes

- Extend structured Design output guidance with:
  - `difficulty_reason`
  - `asset_flow`
  - `shortcut_closure`
  - `fingerprint`
- Make medium/hard/expert validation reject missing or vague asset-flow
  evidence.
- Treat generic assets such as `access`, `data`, `result`, or `permission` as
  non-effective transitions.
- Keep easy designs compatible with direct observe -> exploit -> flag flows.

## Capabilities

### Modified Capabilities

- `structured-challenge-designs`

## Impact

- **Code**:
  - `src/domain/design/difficulty.py`
  - `src/services/design_prompt.py`
  - `tests/app/test_challenge_design_domain.py`
  - `tests/app/test_challenge_design_service.py`
- **Database**: none. The new fields live in the existing validated design
  payload JSON.
- **Compatibility**: easy designs may omit asset flow. Medium+ designs generated
  after this change must satisfy the stricter contract.

