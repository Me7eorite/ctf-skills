# add-design-task-planning

Convert researched generation requests into database-backed design task rows.

Scope:

- one `design_tasks` row per future challenge
- compatible with the existing shard `challenges[]` row shape
- no `design_batches`
- no design worker
- no persisted prompt input
