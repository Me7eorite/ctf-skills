## ADDED Requirements

### Requirement: Completion view defaults to all challenges
The completion view SHALL initialize with the `all` category filter selected when the view is entered. Switching away from the view and back again SHALL restore the `all` filter unless the user changes it in the current visit.

#### Scenario: Entering completion view resets the filter
- **WHEN** the user opens the completion view
- **THEN** the `all` filter is selected by default

#### Scenario: Returning to completion view does not preserve a stale category
- **WHEN** the user switches to another view and later returns to the completion view
- **THEN** the completion view shows the `all` filter unless the user has changed it after returning

### Requirement: Completion view presents per-challenge delivery download
The completion view SHALL render an explicit download action for each completed and delivery-ready challenge. The action SHALL be visually associated with the challenge entry and SHALL not require leaving the list to access the download.

#### Scenario: Single challenge download is visible
- **WHEN** a completed challenge is delivery-ready
- **THEN** the UI shows a per-challenge download action in the row or card for that challenge

### Requirement: Single challenge delivery download produces a scoped package
The server SHALL provide a download endpoint that produces a delivery archive for exactly one completed challenge when requested with that challenge identifier. The archive SHALL reuse the existing delivery format and SHALL fail if the identifier does not resolve to exactly one delivery-ready challenge.

#### Scenario: Downloading one challenge succeeds
- **WHEN** the user requests a delivery download for one completed challenge
- **THEN** the response is a zip archive containing only that challenge's delivery artifacts

#### Scenario: Non-delivery-ready challenge is rejected
- **WHEN** the user requests a delivery download for a challenge that is not build-passed and solve-passed
- **THEN** the server rejects the request with an error

### Requirement: Completion summary emphasizes delivery-relevant counts
The completion view SHALL present counts that reflect completed challenges and delivery-ready challenges, and SHALL avoid misleading placeholder wording for the main state indicators.

#### Scenario: Summary shows delivery counts
- **WHEN** the dashboard data contains completed and delivery-ready challenges
- **THEN** the completion summary shows the total completed count and the delivery-ready count

#### Scenario: Placeholder state is not misleading
- **WHEN** a completion-related field has not yet been generated
- **THEN** the UI uses a neutral placeholder wording instead of implying the first attempt or a similar false history
