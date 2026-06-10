## ADDED Requirements

### Requirement: Dashboard seeds are matrix-compatible

The system SHALL persist Web, Pwn, and Reverse seed objects using the same
field names consumed by the existing shard prompt. Common fields SHALL be
editable directly and category-specific fields SHALL be preserved.

#### Scenario: Save a Web seed

- **WHEN** an operator saves a valid Web seed with runtime-specific fields
- **THEN** the seed appears after dashboard reload with all fields preserved

#### Scenario: Invalid seed is rejected

- **WHEN** required fields are absent, an ID prefix disagrees with its
  category, or a container seed has no valid port
- **THEN** the API returns a validation error and does not persist the seed

### Requirement: Seeds can enter the existing queue

The dashboard SHALL split saved seeds by category using the requested shard
size and SHALL write the existing `{"challenges": [...]}` pending shard format.
It SHALL NOT overwrite an existing pending shard with the same filename.

#### Scenario: Enqueue mixed categories

- **WHEN** saved Web and Pwn seeds are enqueued
- **THEN** separate category shards appear under `work/shards/pending/`

#### Scenario: Pending shard collision

- **WHEN** enqueue would replace an existing pending shard
- **THEN** the API returns a conflict and leaves the existing shard unchanged
