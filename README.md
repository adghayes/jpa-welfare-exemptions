# CMFA / CSCDA Tax Break Analysis

Generates a dataset of **property-tax revenue loss from welfare-exemption
grants** sponsored by two California JPAs — CMFA (California Municipal
Finance Authority) and CSCDA (California Statewide Communities Development
Authority). Both authorities sponsor deals that earn apartment properties a
county welfare exemption; this pipeline extracts every grant from their
public board documents, attaches assessed parcel values, and cleanly
separates what automation produced from what humans collected.

Because the welfare exemption offsets only the 1% statewide base levy,
revenue loss is simply `exempted value × 1%` — computed downstream in the
review spreadsheet, not here.

## Design

**Generated data** comes from primary sources and is reproducible by
re-running scripts. **Manual data** lives in `manual/` (see
`manual/README.md`), each row credited to its source. The merge stamps every
manual value into a provenance record, so a reviewer can verify the split.

| output | contents |
|---|---|
| `output/dataset/grants.csv` | one row per authorization event; grant-level facts only |
| `output/dataset/parcels.csv` | one row per parcel, keyed by `project_id` — link target for spreadsheet formulas |
| `output/dataset/provenance.csv` | long-format record of every manually-sourced value |
| `output/dataset/qa_findings.csv` | merge-time discrepancies (document-vs-manual conflicts, gaps, shared AINs) |
| `output/dataset/review.xlsx` | Grants + Parcels + QA + Legend tabs; **filled cells = automated, unfilled = manual**. Upload to Google Drive by hand (Open with Sheets → Save as Google Sheet). |

Grain: a property re-granted under a new resolution gets one row per
authorization (e.g. Alexandria II under Res 25-487, then again under
26-005). Grants matched to the collaborator's tracker keep its project IDs;
newly generated grants get IDs from 301 up.

## Pipeline

```
1. scripts/download_meetings          (CMFA: src/cmfa_scraping/scraper.py — cmfa-ca.com
                                       CSCDA: python -m src.cscda_scraping.scraper — cscda.org)
2. scripts/extract_all_meetings.py    CMFA agendas+staff reports+minutes -> all_grants_extracted.csv
3. scripts/build_basic_list.py        + CSCDA agendas/packets (cached) -> output/pipeline/basic_list.csv
4. scripts/fetch_la_roll_values.py    LA County parcel API roll values for assigned AINs
5. scripts/build_dataset.py           merge generated + manual/ -> output/dataset/
6. scripts/publish_review_sheet.py    -> output/dataset/review.xlsx
```

Steps 2–3 parse PDFs (minutes take a lag to be posted; CSCDA parse results
are cached by file mtime). Step 4 hits the public, unauthenticated LA County
GIS (`cache.gis.lacounty.gov`); AINs that return nothing are usually
renumbered/in-transition parcels — their values fall back to
`manual/parcel_values_manual.csv`.

### Source quirks worth knowing

- CSCDA assigns **no per-grant resolution numbers** in its public documents
  (packet resolutions are blank `26H-__` templates).
- Agendas contain errors; the **adopted minutes are the corrected record**
  (the parser prefers minutes city/county — e.g. Trails at San Dimas was
  agendized under the wrong county).
- CSCDA's site occasionally links the wrong packet file (2025-12-18 serves
  the 2026-01-08 packet).
- Parser typo aliases live in `scripts/validate_meeting.py`
  (`TYPO_ALIASES`, `CANONICAL_ALIASES`); new meetings occasionally need new
  aliases.
- Parcel coverage: LA County fully automated; other counties' values are
  manual pending per-county automation (largest first: Solano, Santa Clara,
  San Diego, San Mateo).

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

`data/` (scraped documents, ~250MB) is gitignored and regenerable via the
scrapers. `scripts/validate_meeting.py YYYY-MM-DD` debugs extraction for a
single meeting. `scripts/find_parcels.py "<address>"` is an ad-hoc LA parcel
lookup.
