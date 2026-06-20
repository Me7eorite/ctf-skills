## ADDED Requirements

### Requirement: Design tasks expose governed deletion

The dedicated Design Task resource SHALL expose deletion through
`DELETE /api/design-tasks/{id}` and through Delete actions on its dashboard
list and detail surfaces. Cascade, active-work, artifact retention, response,
and confirmation behavior SHALL conform to the `resource-deletion` capability.
Queue, archive, design, build, and read contracts SHALL remain unchanged.

#### Scenario: Design Task detail offers deletion

- **WHEN** the dashboard renders an existing Design Task detail
- **THEN** it exposes a Delete action governed by the shared confirmation dialog
- **AND** active conflicts are displayed without removing the task from the view

#### Scenario: Design Task list offers deletion alongside existing actions

- **WHEN** a Design Task row renders its action group
- **THEN** Delete is available without removing Queue, Archive, Design, Build, or Details actions
