## ADDED Requirements

### Requirement: Generation requests expose governed deletion

The research request resource SHALL expose deletion through
`DELETE /api/research/requests/{id}` and through Delete actions on its dashboard
list and detail surfaces. Cascade, active-work, artifact retention, response,
and confirmation behavior SHALL conform to the `resource-deletion` capability.
Research submission, claim, retry, and read contracts SHALL remain unchanged.

#### Scenario: Request detail offers deletion

- **WHEN** the dashboard renders an existing generation request detail
- **THEN** it exposes a Delete action governed by the shared confirmation dialog
- **AND** the artifact checkbox is unchecked initially

#### Scenario: Request list offers deletion without navigation

- **WHEN** an operator activates Delete for a request row
- **THEN** the same governed confirmation is shown
- **AND** confirming deletes that row without first opening its detail view
