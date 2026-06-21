## REMOVED Requirements

### Requirement: Claimed workspace output is promoted for existing validation

**Reason**: this requirement was introduced by
`add-execution-workspace-and-profile-per-category` as an explicit
**compatibility bridge** with a contract that the next change would remove
it. The publisher Requirements added by this change to
`worker-pool-execution` take over the entire `./output/` →
`work/challenges/` boundary, including identity-field hard check,
quarantine, atomic rename, change-policy diff, output manifest hash, and
retention sweep.

**Migration**: callers that imported `promote_claimed_outputs` from
`src/hermes/workspace.py` MUST migrate to
`publish_workspace_output` in `src/services/build_publisher.py`. The
helper functions kept in `workspace.py` (`_match_claimed_id`,
`_reject_nonconforming_output`, `_reject_tree_symlinks`,
`_matching_directories`) remain available as internal building blocks for
the publisher; they do NOT change.

Tests previously written against the bridge wording are migrated to
publisher-named tests (`test_publisher_*`) in
`tests/app/test_build_publisher.py`. All previous behavior (claimed-id
matching, quarantine path, atomic rename, identity-field checks) is
preserved by the publisher.
