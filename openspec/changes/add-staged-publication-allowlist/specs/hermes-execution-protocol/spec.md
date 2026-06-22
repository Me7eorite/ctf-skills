## REMOVED Requirements

### Requirement: Claimed workspace output is promoted for existing validation

**Reason**: this requirement was introduced by
`add-execution-workspace-and-profile-per-category` as an explicit
**compatibility bridge** with a contract that the next change would remove
it. The publisher Requirements added by this change to
`worker-pool-execution` take over the entire `./output/` →
`work/challenges/` boundary, including identity-field hard check,
quarantine, serialized journaled rename, change-policy diff, output manifest
hash, and retention sweep.

**Migration**: callers that imported `promote_claimed_outputs` (the function)
from `src/hermes/workspace.py` MUST create a host-owned `PublicationContract`
before Hermes invocation, then migrate to
`publish_workspace_output(..., contract=contract)` in
`src/hermes/build_publisher.py`. The exception class
`WorkspacePromotionError` is NOT renamed or removed — it remains as the base
class of `WorkspacePublishError`, and runner/publisher continue to import the
exception name. Only the function and its deprecation stub are deleted before
archive. The helper functions kept in `workspace.py` (`_match_claimed_id`,
`_reject_nonconforming_output`, `_reject_tree_symlinks`,
`_matching_directories`) remain available as internal building blocks for
the publisher; they do NOT change.

The bridge function name SHALL NOT be retained as a silent compatibility
forwarder. If any in-tree caller still references the `promote_claimed_outputs`
function when the publisher lands, the migration replaces the public name with a
deprecation stub that raises `WorkspacePromotionError`. The stub itself is
deleted before this change archives; archival is gated on the function symbol no
longer being importable. The CI grep guard SHALL match the function call
pattern (e.g. `\bpromote_claimed_outputs\s*\(`) and SHALL NOT match the
`WorkspacePromotionError` exception class name.

Tests previously written against the bridge wording are migrated to
publisher-named tests (`test_publisher_*`) in
`tests/app/test_build_publisher.py`. Claimed-id matching, quarantine location,
identity-field checks, and ordinary rollback behavior are preserved. The new
publisher additionally serializes overlapping writers, journals batch commit,
re-verifies host-owned input, and defers workspace cleanup until terminal
validation success.
