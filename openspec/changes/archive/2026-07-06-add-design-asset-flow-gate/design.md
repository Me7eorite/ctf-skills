## Context

The repository already had the first version of `asset_flow` and
`actual_solution_type` validation. This change completes the first asset-flow
gate by requiring medium+ designs to also explain difficulty, close expected
shortcuts, and expose a shape-level fingerprint for later dedup/review work.

## Decisions

### D1 - Medium+ designs must explain the chain

Medium, hard, and expert designs SHALL provide a substantive
`difficulty_reason`. The explanation must describe why the required
asset/capability chain matches the claimed tier.

### D2 - Asset transitions must be concrete

A transition only counts when:

- `produced_asset_or_capability` is concrete;
- `why_next_stage_requires_it` is specific;
- the stage is not merely story filler.

Generic terms such as `access`, `data`, `result`, and `permission` do not count
as effective assets by themselves.

### D3 - Shortcut closure is required for medium+

Medium+ designs SHALL include `shortcut_closure` entries describing how direct
flag access, client-side gates, guessable tokens/URLs/IDs/seeds, public flag
exposure, or similar collapse paths are blocked.

### D4 - Fingerprint is required for later governance

Medium+ designs SHALL include a local `fingerprint` with:

- `entrypoint_type`
- `asset_flow_shape`
- `flag_access_model`
- `scenario_type`

This change validates shape locally; cross-design comparison is left to later
pattern/fingerprint governance.

