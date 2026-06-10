## 1. Define the delivery-bundle capability

- [x] 1.1 Add a spec delta covering challenge selection, canonical paths and names, category-specific enclosure rules, PDF/XLSX output, and Docker degradation.
- [x] 1.2 Resolve proposal inconsistencies: use the delivery spec's `js-reverse` prefix and the `--require-docker` option documented by the design.

## 2. Implement the packer

- [x] 2.1 Add `src/packing.py` with deterministic zip creation, PDF rendering, XLSX inventories, and optional Docker export.
- [x] 2.2 Add `ProjectPaths.delivery_bundle`.
- [x] 2.3 Add the `pack` CLI subcommand and JSON summary output.
- [x] 2.4 Add runtime dependencies and package metadata.

## 3. Tests and documentation

- [x] 3.1 Add `tests/test_packing.py` for selection, names, zip contents, enclosure rules, PDF, XLSX, and Docker skip behavior.
- [x] 3.2 Document the pack command in `README.md`.
- [x] 3.3 Run the full test suite and Ruff. Packing tests pass; the full suite has one pre-existing Windows-only HOME lookup failure in `test_hermes.py`.

## 4. Validate and archive

- [x] 4.1 Run `openspec validate pack-delivery-bundle --strict`.
- [ ] 4.2 Archive only after implementation review and validation succeed.
