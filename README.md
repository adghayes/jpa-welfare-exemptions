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
| `generated_id_ledger.csv` | machine-maintained, append-only registry of pipeline-assigned project IDs (301+) so they stay stable across refreshes — do not edit |

### Outputs

| output | contents |
|---|---|
| `output/dataset/grants.csv` | one row per authorization event; grant-level facts only |
| `output/dataset/parcels.csv` | one row per parcel, keyed by `project_id`; `operative_project_id` is the join key for spreadsheet formulas (sums each property once) |
| `output/dataset/provenance.csv` | long-format record of every manually-sourced value |
| `output/dataset/qa_findings.csv` | merge-time discrepancies (document-vs-manual conflicts, gaps, shared AINs) |
| `output/dataset/county_summary.csv` | per-county pivot over live operative grants (excluding dead/stale): project and SCC-confirmed counts, JPA fee sums, confirmed exemption + 1% tax reduction, estimated exemption + reduction (confirmed where on the roll, else assessed value × restricted ratio × k, with k the median confirmed ratio re-fitted each build — ~0.98 — capped at assessed value), unit and city-cut sums; TOTAL row last |
| `output/dataset/review.xlsx` | Grants + Parcels + County summary + QA findings + Legend tabs; **filled cells = automated, unfilled = manual**. Upload to Google Drive by hand (Open with Sheets → Save as Google Sheet). |

Grants matched to the collaborator's tracker keep its project IDs; newly
generated grants get IDs from 301 up. The pipeline computes per-grant
fields with fixed formulas (JPA fees, years since approval); aggregate
analytics (revenue loss, roll-value sums) remain sheet formulas.

Where a manual override disagrees with a generated value, the conflict
becomes an `override-conflict` QA finding for adjudication — except when
the generated value refutes itself (a generated `total_units` below the
generated `restricted_units`, the signature of a multi-building property
where the parser caught one building): there the manual value wins
silently. The reverse case — the impossible signature with no manual
rescue — is flagged as `units-inconsistent`.

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
7. scripts/check_parcel_assignments.py       validate assignments against the
                                             build, probe for missing siblings
8. scripts/build_dataset.py                  re-run to fold check findings in
9. scripts/publish_review_sheet.py           -> output/dataset/review.xlsx
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
on (when CSCDA's grant program began). All scraped material (~340MB) is
gitignored and regenerable.

For both agencies, `scripts/build_doc_manifest.py` fetches only the two
index pages and records every document's URL per meeting; these become the
`agenda_url`, `staff_report_url`, and `minutes_url` review links on every
grant row. For CSCDA the packet fills `staff_report_url`, and `minutes_url`
points at the NEXT meeting's packet, where that meeting's adopted minutes
are published.

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
  wasn't found. CMFA minutes use four dialects the parser handles:
  per-item motions (2023–2025), per-section block votes, batch adoption of
  an explicit resolution list ("…in a single motion: Resolution 25-551 …
  adopted … the resolutions listed above", with pulls by agenda letter —
  "Item 9.d. was pulled"), and the 2026 consent calendar ("Consent Items
  4, 5. a., … were approved together"). Resolution numbers that wrap
  across a line break ("Resolution 25- 349") are normalized, and when an
  agenda printed a blank resolution template the number is backfilled from
  the minutes by property name.
- **`restricted_pct`** is the overall percent of units rent-restricted,
  extracted as a clean number from the canonical statement in each source
  (CMFA staff reports: "Percent of Restricted Rental Units in the Project:
  NN%"; CSCDA packets: "NN% of the units will be rent-restricted"). It
  complements `rent_restricted_pct`, which keeps the full AMI tier text
  ("20% at 60% AMI; 80% at 80% AMI"). `restricted_units` sums the AMI tier
  counts and is deliberately not derived from the header — a
  `restricted-pct-mismatch` QA finding fires when the stated percentage and
  the unit ratio disagree by more than 5 points *and* more than 2 units
  (slack for whole-percent rounding and a manager's unit), checked once per
  property on the operative row.
- **`jpa_closing_fee` / `jpa_annual_fee`** are computed from each JPA's
  published fee schedule applied to the grant's final `total_units`
  (blank when units are unknown). CMFA (Charitable Affordable Housing):
  closing $600/unit, minimum $10,000; annual $150/unit, minimum $5,000.
  CSCDA: closing $600/unit up to 300 units and $300/unit thereafter,
  minimum $20,000; annual $150/unit up to 300 and $75/unit thereafter,
  minimum $5,000.
- **`years_since_approval`** — full years elapsed (anniversary-based, as of
  the build date) since the approving meeting; blank for pulled and
  preliminary items, which were never approved.
- **`authorization_status`** is the merge-time rollup across each
  *property*: one property can carry several authorization events. Some
  re-approvals are documented amendments (the staff report cites a reason —
  an ownership purchase, a financing restructure); most are silent full
  re-runs of the same deal a few months later with a slipped estimated
  closing and no stated reason — the documents never say why, or whether
  approvals formally lapse. The latest
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

Manually-researched `scc_filed` values are never overwritten, but because
the BOE list is authoritative they are checked against it: an exact entity
match corroborates the manual value and fills in the certificate number and
issue date; no match becomes an `scc-manual-unmatched` QA finding, with the
closest BOE name shown so a human can adjudicate.

### Property Addresses

The address is the bridge between the two halves of the pipeline: grants
come from meeting documents, parcels come from county records, and the
address is what connects a grant to its parcels. It is the pipeline's most
manual field, by deliberate choice. Extraction produces addresses where the
documents state them cleanly — CSCDA packets do (96%), CMFA staff reports
only sometimes (46%) — but of the 266 grants with an address, 204 display a
manual value and 62 a generated one. Roughly half of those manual values
have a generated address underneath that agrees on the street but differs in
formatting detail (the manual value usually carries ZIP and neighborhood);
where the two provably matched, the manual copy was retired, and where they
differ, the manual value stands rather than chasing regex edge cases.
New grants trend automated (CSCDA-era addresses arrive from packets), and
the address→parcel step that follows is audited regardless of where the
address came from.

### Parcel Determination & Validation

Determining which parcels (AIN/APN) belong to a property is the pipeline's
one human-owned step: it involves exactly the judgments machines get wrong —
multi-parcel assemblages, odd lots, parcels renumbered mid-transaction,
marketing addresses that differ from registered situs. Every assignment
lives in `manual/parcel_assignments.csv` as the decision record, whether it
originated with the collaborator's research, a scripted situs/geocode match
that a human accepted, or an assessor-portal map lookup (each
`assignment_source` says which).

Because assignments are asserted rather than generated, every one is audited
on every run by `scripts/check_parcel_assignments.py` (it reads the
last-built grants.csv, so run it after a build and rebuild to fold findings
in). Three noise-gated checks:

- **assignment-mismatch** — no assigned parcel's county situs matches the
  grant's address at the street level, or the grant's city appears in no
  situs. House-number near-misses pass (new construction routinely carries a
  situs adjacent to its marketing address), documented portal-map picks are
  skipped, and complexes are judged per property, not per parcel.
- **possible-missing-parcel** — an unassigned parcel shares an assigned
  parcel's exact situs (and ZIP, in LA; street + jurisdiction in San Diego):
  the signature of a multi-parcel property that was only partially captured.
  AINs mentioned in an assignment's notes (reviewed exclusions) are never
  re-proposed.
- **units-undercount** — county unit records (LA `Units1–5`, SANDAG
  `unitqty`) sum to well under the grant's documented unit count, checked
  only on apartment-use parcels where those fields are trustworthy.

Each checkable parcel carries a per-AIN verdict in `assignment_check`
(`situs-match` / `mismatch` / `no-situs` / `no-address` / `documented`;
legacy-redundant and blank-AIN rows get none); in the review workbook,
AIN/APN cells are tinted only when machine-verified (`situs-match`) — the
rest stay unfilled as unverified human assertions.

Parcel rows carry both ids on purpose: `project_id` is the stable key into
the human-owned assignment (which authorization the research was done
under), while `operative_project_id` is derived each build and moves when
a re-authorization takes over — sheet sums key on it so each property
counts once. When the same AIN was researched under two authorizations of
one property, the build keeps the operative grant's own row and marks the
rest `legacy_redundant` in the output (each with a `parcel-dedup` QA
finding), so operative-keyed sums never double-count; sum formulas should
exclude `legacy_redundant` rows.

### Parcel Tax Value Lookup

Once a parcel is assigned, its assessed values are fetched automatically
wherever a county exposes them, always onto the current roll:

- **Los Angeles** — public parcel GIS API; land/improvement values, an
  explicit welfare-exemption field, situs, year built, and unit counts.
- **Solano** — the assessor's PublicAccessNow portal (two JSON endpoints:
  a search resolving parcel→record key, then per-year value history);
  exemption derived as assessed − net taxable.
- **San Diego** — SANDAG's parcels layer (maintained from county assessor
  data); no exemption field, so a manually-known exemption carries over
  field-level with its own provenance.

Santa Clara's portals are bot-walled and the remaining counties expose no
public value service — their values are hand-entered in
`manual/parcel_values_manual.csv`. AINs that return nothing from a county
source are usually renumbered or newly created parcels; their values fall
back to manual data, are flagged as `fetch-fallback` in QA, and self-heal on
later runs once the county publishes the parcel.

### Source quirks worth knowing

- CSCDA's site occasionally links the wrong packet file (2025-12-18 serves
  the 2026-01-08 packet).
- Parser typo aliases live in `scripts/validate_meeting.py`
  (`TYPO_ALIASES`, `CANONICAL_ALIASES`) and property-name aliases in
  `scripts/build_basic_list.py` (`PROP_ALIASES`); new meetings occasionally
  need new entries.
- Parse results for both agencies are cached
  (`output/pipeline/{cmfa,cscda}_parse_cache.json`): only new or changed
  PDFs (by mtime) are re-parsed, and each cache self-invalidates entirely
  when its parser source files change (content hash) — no manual cache
  busting needed.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

`scripts/validate_meeting.py YYYY-MM-DD` debugs extraction for a single
meeting. `scripts/find_parcels.py "<address>"` is an ad-hoc LA parcel
lookup.
