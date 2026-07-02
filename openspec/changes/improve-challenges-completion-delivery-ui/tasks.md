## 1. Backend delivery download

- [x] 1.1 Add a scoped delivery download endpoint for a single completed challenge.
- [x] 1.2 Reuse the existing packer so the single-challenge archive follows the same delivery format.
- [x] 1.3 Validate that the requested challenge is delivery-ready and resolve to exactly one result.

## 2. Completion view UI

- [x] 2.1 Add a per-challenge delivery download action to the completed challenges list.
- [x] 2.2 Refine the completion summary and row/card metadata to emphasize delivery-relevant counts.
- [x] 2.3 Reset the completion view filter to `all` when the view is entered.

## 3. Verification

- [x] 3.1 Add or update tests for the single-challenge download path and completion-view defaults.
- [x] 3.2 Run the relevant test subset and verify the UI renders the new action without breaking existing downloads.
