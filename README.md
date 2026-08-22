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

## Data Sources

The dataset mixes two kinds of data, kept strictly separate: **automated
data** is fetched or parsed from public sources by scripts and is
reproducible by re-running them; **manual data** is human-collected, lives
in `manual/`, and every row carries its source. The merge stamps each manual
value into a provenance record — in the review workbook, automated cells are
tinted and manual cells are not, so a reviewer always knows which is which.

### Automated sources

| source | provides |
|---|---|
| `cmfa-ca.com/resources/meetings/` | CMFA board agendas, staff reports, and minutes (2023–) — the grant listing of record |
| `cscda.org/agendas/` | CSCDA agendas and combined staff-report/minutes packets (2025–) |
| LA County parcel GIS (`cache.gis.lacounty.gov`) | assessed values, welfare exemptions, situs, year built, by AIN |
| Solano County assessor portal (`ca-solano.publicaccessnow.com`) | per-year assessed values and net taxable (exemption derived) |
| SANDAG parcels layer (`geo.sandag.org`) | San Diego County assessed values by APN (no exemption field) |
| BOE open data portal (`boe.ca.gov/dataportal/`) | the statewide Supplemental Clearance Certificate list (welfare-exemption eligibility filings) |

### Manual data (`manual/`, see `manual/README.md`)

| file | contents |
|---|---|
| `grant_id_map.csv` | crosswalk from the collaborator's project IDs to generated grants (and duplicate-row rulings) |
| `manual_grants.csv` | the four grants that predate the meeting archives — entire rows are manual |
| `grant_overrides.csv` | per-field values that supplement or correct generated grants, each with a source and note |
| `parcel_assignments.csv` | which parcels (AIN/APN) belong to which project — parcel identity is always a human judgment |
| `parcel_values_manual.csv` | assessed values for parcels with no automated source (most non-LA counties, in-transition AINs) |

### Outputs

| output | contents |
|---|---|
| `output/dataset/grants.csv` | one row per authorization event; grant-level facts only |
| `output/dataset/parcels.csv` | one row per parcel, keyed by `project_id`; `operative_project_id` is the join key for spreadsheet formulas (sums each property once) |
| `output/dataset/provenance.csv` | long-format record of every manually-sourced value |
| `output/dataset/qa_findings.csv` | merge-time discrepancies (document-vs-manual conflicts, gaps, shared AINs) |
| `output/dataset/review.xlsx` | Grants + Parcels + QA findings + Legend tabs; **filled cells = automated, unfilled = manual**. Upload to Google Drive by hand (Open with Sheets → Save as Google Sheet). |

Grants matched to the collaborator's tracker keep its project IDs; newly
generated grants get IDs from 301 up. Derived analytics (revenue loss,
roll-value sums) are computed by formulas in the review spreadsheet, never
in the pipeline.

## Pipeline

```
1. python -m src.cmfa_scraping.scraper /     download meeting documents
   python -m src.cscda_scraping.scraper
2. scripts/build_doc_manifest.py             per-meeting source-document URLs
3. scripts/extract_all_meetings.py           parse CMFA documents
4. scripts/build_basic_list.py               + parse CSCDA documents (cached)
5. scripts/fetch_la_roll_values.py           assessed values by county:
   scripts/fetch_solano_roll_values.py         LA GIS API, Solano assessor
   scripts/fetch_san_diego_roll_values.py      portal, SANDAG parcel layer
   scripts/fetch_scc_certificates.py         BOE Supplemental Clearance
                                             Certificate list (OData)
6. scripts/build_dataset.py                  merge generated + manual/ -> output/dataset/
7. scripts/publish_review_sheet.py           -> output/dataset/review.xlsx
```

### Grant Scraping

Both agencies publish their board documents on plain, unauthenticated web
pages, and both scrapers are incremental — a file already on disk is never
re-downloaded, so routine runs only fetch documents for new meetings. CMFA
lists every meeting on a single index page
(`cmfa-ca.com/resources/meetings/`), one list entry per meeting whose links
are classified by their text into three documents: the **agenda**, the
**staff reports**, and the **minutes**. These land in
`data/cmfa_scraping/meetings/YYYY-MM-DD/{agenda,staff_report,minutes}.pdf`,
covering meetings from 2023 on. Minutes are adopted at the following
meeting, so the latest meeting's minutes are typically absent for a few
weeks — the pipeline treats their absence as "outcome not yet known", and a
re-run after they post fills the gap.

CSCDA publishes on `cscda.org/agendas/`: a date heading per meeting followed
by document links, of which two matter — the **agenda** and a combined
**"Staff Reports and Meeting Minutes" packet** (the packet contains the
staff report for each agenda item plus the adopted minutes of the *previous*
meeting). These land in
`data/cscda_scraping/meetings/YYYY-MM-DD/{agenda,packet}.pdf`, covering 2025
on (when CSCDA's grant program began). Separately,
`scripts/build_doc_manifest.py` fetches only the two index pages and records
every document's URL per meeting, which become the `agenda_url`,
`staff_report_url`, and `minutes_url` review links on every grant row (for
CSCDA the packet fills `staff_report_url`, and `minutes_url` points at the
NEXT meeting's packet, where that meeting's adopted minutes are published). All scraped material (~250MB) is gitignored and regenerable.

### Grant Parsing

Three document types feed the dataset, with a clear division of authority:
the **agenda** is the listing of record (which grants were considered, for
whom, where), the **staff report** enriches each item (investor, nonprofit
partner, unit counts, restricted percentage, regulatory term, address,
estimated closing), and the **minutes** are the record of what actually
happened — including corrections, since agendas contain errors (Trails at
San Dimas was agendized under the wrong county; the adopted minutes fixed
it, and the parser prefers minutes city/county). For CMFA all three are
separate PDFs parsed with regexes tuned to its agenda formats; for CSCDA the
agenda is parsed the same way and the packet supplies both the staff-report
details and the previous meeting's minutes outcomes. CSCDA assigns no
per-grant resolution numbers in any public document (its packet resolutions
are blank `26H-__` templates), so CSCDA rows have empty `resolution` by
design.

Three output fields encode what the documents establish:

- **`item_type`** comes from the agenda's section wording. `authorize` means
  the item appeared under a grant-authorization section ("authorize the
  giving of a charitable grant" / "regulatory agreement and grant");
  `preliminary_only` means the property only ever appeared under an
  acceptance-of-applications/preliminary-approval section and was never
  authorized.
- **`minutes_status`** is the per-item outcome parsed from the adopted
  minutes: `approved`, `pulled` (the minutes say "this item was pulled from
  the agenda" — a resolution number appearing in minutes is *not* approval),
  `continued`, or blank when minutes aren't posted yet or the outcome text
  wasn't found. CMFA minutes use three dialects the parser handles:
  per-item motions (2023–2025), per-section block votes, and the 2026
  consent calendar ("Consent Items 4, 5, … were approved together").
- **`authorization_status`** is the merge-time rollup across each
  *property*: the agencies re-run the full approval when a deal slips past
  its closing date, and a property that changes sponsors gets a fresh grant,
  so one property can carry several authorization events. The latest
  minutes-approved one is `operative` — the row to count; earlier approvals
  are `superseded` (with `superseded_by` pointing at the operative row);
  minutes-pulled attempts are `pulled` and never count; `preliminary` marks
  the preliminary-only rows. Parcels carry `operative_project_id` so
  downstream sums count each property exactly once.

### BOE welfare-exemption filings

A limited partnership must hold a **Supplemental Clearance Certificate**
(SCC) from the State Board of Equalization before a county assessor can
grant the welfare exemption on its low-income housing property — so the SCC
list is the paper trail connecting a grant to an exemption actually being
sought. The BOE publishes the full statewide list (~6,500 certificates:
limited partnership, managing general partner, SCC number, county, issue
date, first fiscal year qualified) in its open data portal as an OData
endpoint, refreshed by the BOE roughly annually;
`scripts/fetch_scc_certificates.py` downloads it in full.

The merge matches each grant's entity against the certificate list — exact
match after stripping legal suffixes, preferring a certificate filed in the
grant's county when a name matches several — and sets `scc_filed`,
`scc_number`, and `scc_issue_date`, placed beside the status fields since
they're evaluated together. Near-miss names (e.g. a sister LP of the same
sponsor at a neighboring address) are never matched automatically; they
become `scc-possible-match` QA findings for human review.

### Parcels and values

Parcel **identity** (which AIN belongs to which project) is always a human
judgment, recorded in `manual/parcel_assignments.csv`. Parcel **values** are
fetched automatically where a county exposes them: Los Angeles (public
parcel GIS API, explicit welfare-exemption field), Solano (assessor's
PublicAccessNow portal; exemption derived as assessed − net taxable), and
San Diego (SANDAG parcel layer; no exemption field, so manual exemptions
carry over per-field). Santa Clara's portals are bot-walled and the
remaining counties expose no public value service — their values are
hand-entered in `manual/parcel_values_manual.csv`. AINs that return nothing
from a county source are usually renumbered or newly created parcels; their
values fall back to manual data and self-heal on later runs.

### Source quirks worth knowing

- CSCDA's site occasionally links the wrong packet file (2025-12-18 serves
  the 2026-01-08 packet).
- Parser typo aliases live in `scripts/validate_meeting.py`
  (`TYPO_ALIASES`, `CANONICAL_ALIASES`) and property-name aliases in
  `scripts/build_basic_list.py` (`PROP_ALIASES`); new meetings occasionally
  need new entries.
- CSCDA parse results are cached by file mtime
  (`output/pipeline/cscda_parse_cache.json`); only new or changed PDFs are
  re-parsed.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

`scripts/validate_meeting.py YYYY-MM-DD` debugs extraction for a single
meeting. `scripts/find_parcels.py "<address>"` is an ad-hoc LA parcel
lookup.
