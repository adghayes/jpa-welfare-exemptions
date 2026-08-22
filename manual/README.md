# manual/ — human-collected data

Everything in this directory was collected by a person, not a script. Each
row carries a `source`:

- `collaborator-sheet` — hand research imported from the collaborator's
  tracking sheet (export of 2026-08-21). The one-time import tooling lives
  in git history (`scripts/bootstrap_manual_from_sheet.py`, removed after
  its single run); these files are now the source of truth.
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

The pipeline (`scripts/build_dataset.py`) merges these with the generated
data and stamps every manual value into `output/dataset/provenance.csv`;
in the review workbook, manual values are the unfilled cells.
