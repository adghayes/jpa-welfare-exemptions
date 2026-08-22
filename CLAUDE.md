# CLAUDE.md

Read `README.md` first — it has the data model, pipeline, and update runbook.

## Conventions

- **`input/grants.csv` is the single source of truth** for the grants tracker. Never point scripts at the frozen `input/CMFA-grants-<date>*.csv` snapshots; they exist for provenance only.
- Use `./venv/bin/python` (Python 3.14 venv at repo root). No test suite exists; smoke-test by running `./venv/bin/python scripts/extract_all_meetings.py --quiet` and checking the grant count is stable (196 as of Jan 2026 data).
- `data/` is gitignored but required: `data/cmfa_scraping/meetings/` holds the scraped PDFs the extractors read. If missing, regenerate with `download_all_meetings()` from `src/cmfa_scraping/scraper.py`.
- Before editing `output/find_parcels/cmfa_parcels_final.csv`, copy it to `cmfa_parcels_final_backup_<YYYYMMDD_HHMMSS>.csv` in the same directory. It is hand-curated; no script regenerates it.
- `refetch_pers_prop.py` rewrites `cmfa_parcels_final.csv` **in place**.
- The `tra` column in `output/find_parcels/tra_summary.csv` is overloaded: 5-digit zero-padded LA TRA codes and city names for other counties.
- Money/percent columns in `input/grants.csv` are pre-formatted strings (`"$4,447,710.00"`, `1.1874%`); `Date` mixes 2- and 4-digit years — parse with `pd.to_datetime(..., format='mixed')`.
- External APIs (LA GIS geocoder/parcel server, LA Auditor TRA search) are public and unauthenticated; keep rate limits ≥ 0.3s. County tax portals are bot-protected — do not attempt to scrape tax bills.
- The Google Sheet copy of the tracker (work Drive) is a read-only publish target, updated from `input/grants.csv` — never treat the sheet as authoritative.
