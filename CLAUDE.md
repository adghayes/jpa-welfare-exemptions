# CLAUDE.md

Read `README.md` first — pipeline, outputs, and source quirks. `manual/README.md`
documents the manual-data contract.

## Conventions

- **Generated vs manual is the core invariant.** Scripts may only write
  generated/derived files (`output/`). Human-collected facts go in `manual/`
  CSVs with `source: manual-repo-edit` and a note — never hardcoded into
  scripts (exception: parser typo aliases, which are extraction corrections).
- **No derived analytics in code.** Revenue loss, estimates, and roll-value
  sums are computed by formulas in the review spreadsheet, not the pipeline.
- **Grain**: one grants row per authorization event. Project IDs from the
  collaborator's tracker are preserved verbatim and are append-only
  (they have been reshuffled once historically — never reuse an ID);
  pipeline-assigned IDs start at 301 and are pinned in
  `manual/generated_id_ledger.csv` (machine-maintained, append-only).
- Use `./venv/bin/python` (Python 3.14 venv at repo root). No test suite;
  smoke-test with the full chain: `extract_all_meetings.py --quiet &&
  build_basic_list.py && build_dataset.py && check_parcel_assignments.py &&
  build_dataset.py && publish_review_sheet.py` (build → check → rebuild:
  the check validates against the previous build). Expected magnitudes:
  ~305 grants, ~394 parcels, ~1,150 provenance rows; extraction ~275 CMFA
  grants pre-merge.
- `data/` is gitignored but required (scraped PDFs). Regenerate via
  `src/cmfa_scraping/scraper.py` / `python -m src.cscda_scraping.scraper`.
  Downloads are incremental; parsing is the slow part. Both parse stages are
  cached in `output/pipeline/{cmfa,cscda}_parse_cache.json`: a meeting
  re-parses when a document mtime changes, and the whole cache
  self-invalidates when parser source files change (content hash).
- External APIs (LA GIS parcel server, geocoder) are public and
  unauthenticated; keep rate limits ≥ 0.3s. County tax portals are
  bot-protected — do not attempt to scrape tax bills.
- `output/dataset/review.xlsx` is uploaded to Google Drive **manually** by
  the user; do not build Drive-upload automation.
- The one-time sheet-import tooling (`bootstrap_manual_from_sheet.py`,
  `import_sheet_export.py`) lives in git history only; `manual/` is now
  first-class source data.
