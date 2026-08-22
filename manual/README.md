# manual/ — human-collected data

Everything in this directory was collected by a person, not a script. Each
row carries a `source`:

- `collaborator-sheet` — hand research imported from the collaborator's
  tracking sheet (export of 2026-08-21). The one-time import tooling lives
  in git history (`scripts/import_sheet_export.py` and
  `scripts/bootstrap_manual_from_sheet.py`, removed after their single
  run); these files are now the source of truth.
- `manual-repo-edit` — a fact or correction entered directly here. Use this
  source for any new manual data, and add a `note` saying where it came from.

Files:

| file | contents |
|---|---|
| `grant_id_map.csv` | crosswalk: collaborator project_id ↔ generated grant (property + meeting date), with match method |
| `manual_grants.csv` | grants that predate the meeting archives (2022) — entire rows are manual |
| `grant_overrides.csv` | long-format per-field values that supplement or correct the generated grants (`project_id, field, value, source, note`) |
| `parcel_assignments.csv` | which parcels (AIN/APN) belong to which project — parcel identity is always a human judgment |
| `parcel_values_manual.csv` | assessment-roll values for parcels with no automated source (non-LA counties, and LA AINs in transition) |
| `generated_id_ledger.csv` | machine-maintained, append-only registry pinning pipeline-assigned project IDs (301+) to their grants — never edit or delete rows |

The pipeline (`scripts/build_dataset.py`) merges these with the generated
data and stamps every manual value into `output/dataset/provenance.csv`;
in the review workbook, manual values are the unfilled cells.

Machine-read conventions in these files:

- `grant_overrides.csv` `field: manual_status` takes `dead` or `stale`;
  `dead` excludes the grant from the missing-parcels, sibling-sweep, and
  unit-reconciliation checks.
- `parcel_assignments.csv` `notes` control the assignment checks: a note
  containing `no county situs`, `in transition`, `renumbered`,
  `common-area`, `portal map`, or `situs reviewed` marks the parcel
  `documented` (skips the situs-mismatch check); a note containing
  `units reviewed` suppresses the units-undercount check for that project;
  and any AIN written in a note (e.g. "siblings excluded: …") is never
  re-proposed by the missing-sibling sweep. Always say why in the note.
