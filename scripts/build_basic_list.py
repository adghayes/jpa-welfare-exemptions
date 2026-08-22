"""Build the combined CMFA + CSCDA "basic list" of grant items from agency
meeting documents, with per-field coverage stats and a comparison against
the collaborator sheet.

Sources:
  CMFA:  output/cmfa_scraping/all_grants_extracted.csv
         (produced by scripts/extract_all_meetings.py from agendas, staff
         reports, and minutes)
  CSCDA: data/cscda_scraping/meetings/*/agenda.pdf, parsed directly
         (agendas only — resolutions/units live in the packets, not yet parsed)

Output:
  output/pipeline/basic_list.csv
  console coverage report

Usage:
    python scripts/build_basic_list.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.cscda_scraping.agenda_parser import parse_agenda_pdf  # noqa: E402

CMFA_EXTRACTED = Path("output/cmfa_scraping/all_grants_extracted.csv")
CSCDA_MEETINGS = Path("data/cscda_scraping/meetings")
SHEET = Path("input/grants.csv")
OUT = Path("output/pipeline/basic_list.csv")

COLUMNS = [
    "agency", "property_name", "entity", "city", "county", "resolution",
    "meeting_date", "item_type", "investor_1", "nonprofit_partner",
    "total_units", "rent_restricted_pct", "term_years", "city_cut",
    "grant_description", "source",
]


def norm_name(name: str) -> str:
    return " ".join(str(name).lower().replace(".", "").split())


def load_cmfa() -> pd.DataFrame:
    df = pd.read_csv(CMFA_EXTRACTED, dtype=str).fillna("")
    df["agency"] = "CMFA"
    df["source"] = "cmfa-meeting-docs"
    return df


def load_cscda() -> pd.DataFrame:
    rows = []
    for d in sorted(CSCDA_MEETINGS.iterdir()):
        agenda = d / "agenda.pdf"
        if not agenda.exists():
            continue
        for g in parse_agenda_pdf(agenda):
            rows.append({
                "agency": "CSCDA",
                "property_name": g.property_name,
                "entity": g.entity,
                "city": g.city,
                "county": g.county,
                "resolution": g.resolution,
                "meeting_date": d.name,
                "item_type": g.item_type,
                "source": "cscda-agenda",
            })
    df = pd.DataFrame(rows)
    # Same property re-considered at a later meeting: keep the latest occurrence
    # (mirrors the CMFA dedup rule).
    df["_key"] = df["property_name"].map(norm_name)
    df = df.sort_values("meeting_date").drop_duplicates("_key", keep="last")
    return df.drop(columns="_key")


def coverage(df: pd.DataFrame, label: str) -> None:
    n = len(df)
    print(f"\n{label}: {n} grants")
    for col in COLUMNS[1:-1]:
        if col not in df.columns:
            continue
        filled = (df[col].astype(str).str.strip() != "").sum()
        if filled:
            print(f"  {col:22} {filled:3}/{n}  ({filled/n:4.0%})")


def main() -> None:
    cmfa, cscda = load_cmfa(), load_cscda()
    combined = pd.concat([cmfa, cscda], ignore_index=True)
    combined = combined.reindex(columns=COLUMNS).fillna("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUT, index=False)
    print(f"wrote {OUT}: {len(combined)} rows")

    coverage(cmfa, "CMFA (agendas + staff reports + minutes)")
    coverage(cscda, "CSCDA (agendas only)")

    # comparison vs collaborator sheet
    sheet = pd.read_csv(SHEET, dtype=str).fillna("")
    sheet["_key"] = sheet["Property Name"].map(norm_name)
    combined["_key"] = combined["property_name"].map(norm_name)
    sheet_keys = set(sheet["_key"])
    gen_keys = set(combined["_key"])
    print(f"\nSheet rows matched by generated list (exact name): "
          f"{len(sheet_keys & gen_keys)}/{len(sheet_keys)}")
    missing = sheet[~sheet["_key"].isin(gen_keys)]
    print(f"Sheet rows NOT generated ({len(missing)}):")
    for _, r in missing.iterrows():
        print(f"  [{r['Project ID']}] {r['Property Name']} ({r['Agency']}, {r['Date']})")
    extra = combined[~combined["_key"].isin(sheet_keys)]
    print(f"\nGenerated but not in sheet ({len(extra)}):")
    for _, r in extra[extra["item_type"] != "preliminary_only"].iterrows():
        print(f"  {r['property_name']} ({r['agency']}, {r['meeting_date']}, {r['item_type']})")
    n_prelim = (extra["item_type"] == "preliminary_only").sum()
    print(f"  (+ {n_prelim} preliminary_only items, expected absent from sheet)")


if __name__ == "__main__":
    main()
