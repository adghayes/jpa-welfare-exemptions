# CMFA Tax Break Analysis

Quantifies property-tax revenue loss from **CMFA (California Municipal Finance Authority) welfare-exemption grants**. CMFA sponsors deals that get apartment properties a county welfare exemption; this repo tracks each granted project from CMFA board-meeting documents, finds its parcels, and estimates lost revenue as `assessed value × tax rate`.

## Data model

| File | Role | Tracked in git |
|---|---|---|
| `input/grants.csv` | **Master grants tracker** — one row per project. The single source of truth; published to a read-only Google Sheet for viewing. | yes |
| `data/cmfa_scraping/meetings/YYYY-MM-DD/` | Scraped agendas, staff reports, minutes (~230MB) | no (regenerable) |
| `output/cmfa_scraping/all_grants_extracted.csv` | Grants extracted from meeting PDFs, deduplicated | yes |
| `output/find_parcels/cmfa_parcels_final.csv` | Curated parcel roster (LA County), one row per parcel, keyed by `project_id` | yes |
| `output/find_parcels/tra_summary.csv` | Tax rates: 5-digit LA TRA codes + city names for other counties (`tra` column is overloaded) | yes |
| `data/parcel_lookup/parcel_cache.db` | SQLite cache of geocode/parcel API responses (7-day TTL) | no |

Historical `input/CMFA-grants-<date>*.csv` files are frozen exports of the old Google Sheet, kept for provenance only. **Do not point scripts at them** — everything reads `input/grants.csv`.

## Pipeline

```
cmfa-ca.com/resources/meetings
  → src/cmfa_scraping/scraper.py            (download meeting docs)
  → scripts/extract_all_meetings.py         (parse PDFs → all_grants_extracted.csv,
                                             validate against input/grants.csv)
  → scripts/process_cmfa_parcels.py         (LA County GIS: address → parcels; interactive)
  → output/find_parcels/cmfa_parcels_final.csv   (hand-curated; refetch_pers_prop.py
                                                  refreshes pers-prop/fixture values in place)
  → tax rates (per county, see below) → tra_summary.csv
  → revenue loss = net assessed value × TRA rate  (columns in input/grants.csv)
```

### Scripts

- `scripts/extract_all_meetings.py [--csv PATH] [--quiet]` — parse all meetings ≥ 2023-07-01, dedupe, validate against the tracker.
- `scripts/validate_meeting.py YYYY-MM-DD` — parse and validate a single meeting.
- `scripts/cmfa_scrape.py {download,compare,all}` — legacy scraper CLI (flat PDF layout). Prefer calling `download_all_meetings()` from `src/cmfa_scraping/scraper.py` for the per-meeting-folder layout.
- `scripts/process_cmfa_parcels.py` — interactive parcel finder for LA County grants (geocode → tract → parcels; auto-accepts unambiguous apartment matches).
- `scripts/find_parcels.py "<address>"` — one-off parcel lookup for a single address.
- `scripts/refetch_pers_prop.py` — refresh `Roll_PersPropValue`/`Roll_FixtureValue` on `cmfa_parcels_final.csv` in place.
- `scripts/extract_santa_clara_tax_rates.py`, `extract_san_mateo_tax_rates.py` — parse county tax-rate-book PDFs in `input/`, append city rows to `tra_summary.csv`.
- `scripts/lookup_parcels.py`, `lookup_ains.py` — older address/AIN lookup utilities (cache-backed).

### County coverage

- **Los Angeles** (majority of grants): fully automated parcel lookup via public CAMS geocoder + `LACounty_Parcel` MapServer. TRA rates from the Auditor API — `POST https://onlineapps.auditor.lacounty.gov/TRA/TRA/Search` with `Area` (TRA without leading zeros) and `FiscalYearID` (**36 = FY 2025/26; increment each fiscal year**). See `notes/tax_rate_lookup.md`.
- **Santa Clara, San Mateo**: scripted PDF extraction from rate books in `input/`.
- **San Diego, Orange, Contra Costa, Sacramento, Alameda**: rates were gathered ad-hoc (JSON scrape, PDFs, screenshots in `input/alameda/`); no extractor scripts. Re-verify when rate books roll to a new fiscal year.
- Direct tax-bill scraping is **not possible** — LA TTC/assessor portals sit behind Incapsula bot protection (`src/parcel_lookup/tax_fetcher.py` is a stub). Revenue loss is always estimated from assessed value × rate.

## Update runbook (adding new meetings)

1. Download new meeting docs: `python -c "from src.cmfa_scraping.scraper import download_all_meetings; download_all_meetings()"` (skips existing folders; minutes for recent meetings post with a lag).
2. `python scripts/extract_all_meetings.py` — review `extracted_not_in_csv.csv` for genuinely new grants.
3. Add new grants to `input/grants.csv` (assign new `Project ID`s).
4. For new LA grants: `python scripts/process_cmfa_parcels.py`, then merge accepted parcels into `output/find_parcels/cmfa_parcels_final.csv` (back it up first — convention is `cmfa_parcels_final_backup_<timestamp>.csv`).
5. Non-LA grants: manual parcel research (county assessor portals).
6. Fill roll values / tax rates / revenue-loss columns in `input/grants.csv` from the parcel and rate tables.
7. Publish `input/grants.csv` to the Google Sheet (work Drive; done via Claude session with Drive access).

Fiscal-year rollover (each July–October): new assessment roll values appear (`roll_year` bump), county rate books republish, and the LA Auditor `FiscalYearID` increments.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Parsing quirks: agenda/staff-report parsers are regex-based (`src/cmfa_scraping/`); known agenda typos are aliased in `scripts/validate_meeting.py` and `scripts/extract_all_meetings.py` (`TYPO_ALIASES`) — new meetings occasionally need new aliases.
