"""Build the combined CMFA + CSCDA "basic list" of grant items from agency
meeting documents, with per-field coverage stats.

Sources:
  CMFA:  output/cmfa_scraping/all_grants_extracted.csv
         (produced by scripts/extract_all_meetings.py from agendas, staff
         reports, and minutes)
  CSCDA: data/cscda_scraping/meetings/*/{agenda,packet}.pdf, parsed directly
         (with an mtime-keyed parse cache; CSCDA assigns no per-grant
         resolution numbers in its public documents)

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
from src.cscda_scraping.packet_parser import (  # noqa: E402
    parse_packet_grants,
    parse_packet_minutes,
)

import logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)

CMFA_EXTRACTED = Path("output/cmfa_scraping/all_grants_extracted.csv")
CSCDA_MEETINGS = Path("data/cscda_scraping/meetings")
OUT = Path("output/pipeline/basic_list.csv")

COLUMNS = [
    "agency", "property_name", "entity", "city", "county", "resolution",
    "meeting_date", "item_type", "minutes_status", "investor_1",
    "nonprofit_partner", "total_units", "rent_restricted_pct", "term_years",
    "city_cut", "grant_description", "address", "estimated_closing", "source",
]


ABBREV = {"boulevard": "blvd", "street": "st", "avenue": "ave", "drive": "dr",
          "road": "rd", "place": "pl", "and": "&"}

# Known name variants of the same property across documents/sheet
# (agenda typos, renames, long/short forms). Applied after normalization.
PROP_ALIASES = {
    "2330 3rd": "2330 e 3rd",
    "bella vista": "bella vista at hilltop",
    "569 w 6th st": "569 w 6th",
    "569th w 6th": "569 w 6th",
    "685 w 4th st": "685 w 4th",
    "del norte": "del norte pl",
    "the heltsley apartments fka sofi redwood park": "sofi redwood park",
    "the heltsley": "sofi redwood park",
    "coliseum transit village": "coliseum connections transit village",
    "coliseum connections": "coliseum connections transit village",
    "hansen illage": "hansen village",
}


def norm_name(name: str) -> str:
    words = str(name).lower().replace(".", "").replace(",", "").split()
    s = " ".join(ABBREV.get(w, w) for w in words)
    s = s.replace("kinglsey", "kingsley")
    for suffix in (" apartments", " apartment"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return PROP_ALIASES.get(s, s)


def load_cmfa() -> pd.DataFrame:
    df = pd.read_csv(CMFA_EXTRACTED, dtype=str).fillna("")
    df["agency"] = "CMFA"
    df["source"] = "cmfa-meeting-docs"
    # unify with CSCDA's minutes_status: per-item outcome parsed from the
    # meeting minutes (approved / pulled / continued); blank means the
    # minutes weren't posted yet or the item's outcome wasn't found
    df["minutes_status"] = df.get("minutes_outcome", "")
    return df


CSCDA_CACHE = Path("output/pipeline/cscda_parse_cache.json")


def _parse_cscda_meeting(d: Path) -> dict:
    """Parse one meeting dir into a JSON-serializable dict."""
    entry = {"agenda": [], "details": [], "outcomes": []}
    agenda, packet = d / "agenda.pdf", d / "packet.pdf"
    if agenda.exists():
        entry["agenda_mtime"] = agenda.stat().st_mtime
        for g in parse_agenda_pdf(agenda):
            entry["agenda"].append({
                "property_name": g.property_name, "entity": g.entity,
                "city": g.city, "county": g.county, "resolution": g.resolution,
                "item_type": g.item_type,
            })
    if packet.exists():
        entry["packet_mtime"] = packet.stat().st_mtime
        for pg in parse_packet_grants(packet, d.name):
            entry["details"].append({
                "property_name": pg.property_name,
                "total_units": pg.total_units or "",
                "rent_restricted_pct": pg.rent_restricted_pct,
                "term_years": pg.term_years or "",
                "nonprofit_partner": pg.nonprofit_partner,
                "address": pg.address,
                "estimated_closing": pg.estimated_closing,
                "grant_description": (
                    f"Grant of ${pg.grant_amount:,}" if pg.grant_amount else ""),
            })
        for o in parse_packet_minutes(packet):
            entry["outcomes"].append({
                "property_name": o.property_name, "minutes_status": o.status,
                "city": o.city, "county": o.county,
            })
    return entry


def load_cscda() -> pd.DataFrame:
    import json

    cache = {}
    if CSCDA_CACHE.exists():
        cache = json.loads(CSCDA_CACHE.read_text())

    rows = []
    details: dict[str, dict] = {}   # norm name -> staff-report fields
    outcomes: dict[str, dict] = {}  # norm name -> minutes outcome

    dirty = False
    for d in sorted(CSCDA_MEETINGS.iterdir()):
        if not d.is_dir():
            continue
        agenda, packet = d / "agenda.pdf", d / "packet.pdf"
        entry = cache.get(d.name)
        stale = (entry is None
                 or (agenda.exists() and entry.get("agenda_mtime") != agenda.stat().st_mtime)
                 or (packet.exists() and entry.get("packet_mtime") != packet.stat().st_mtime))
        if stale:
            entry = _parse_cscda_meeting(d)
            cache[d.name] = entry
            dirty = True

        for g in entry["agenda"]:
            rows.append({"agency": "CSCDA", "meeting_date": d.name,
                         "source": "cscda-agenda", **g})
        for pg in entry["details"]:
            details[norm_name(pg["property_name"])] = {
                **{k: v for k, v in pg.items() if k != "property_name"},
                "source": "cscda-agenda+packet",
            }
        for o in entry["outcomes"]:
            # the adopted minutes are the corrected record when the agenda
            # misstates a location (e.g. Trails at San Dimas)
            outcomes[norm_name(o["property_name"])] = {
                "minutes_status": o["minutes_status"],
                "city": o["city"], "county": o["county"],
            }

    if dirty:
        CSCDA_CACHE.parent.mkdir(parents=True, exist_ok=True)
        CSCDA_CACHE.write_text(json.dumps(cache))

    df = pd.DataFrame(rows)
    # Same property re-considered at a later meeting: keep the latest occurrence
    # (mirrors the CMFA dedup rule).
    df["_key"] = df["property_name"].map(norm_name)
    df = df.sort_values("meeting_date").drop_duplicates("_key", keep="last")

    for col in ["minutes_status", "total_units", "rent_restricted_pct",
                "term_years", "nonprofit_partner", "address",
                "estimated_closing", "grant_description"]:
        df[col] = ""
    from fuzzywuzzy import fuzz

    def lookup(table: dict, key: str) -> dict:
        if key in table:
            return table[key]
        best, score = None, 0
        for k in table:
            s = max(fuzz.ratio(key, k), fuzz.token_sort_ratio(key, k))
            if s > score:
                best, score = k, s
        return table[best] if score >= 85 else {}

    for idx, row in df.iterrows():
        key = row["_key"]
        for col, val in lookup(details, key).items():
            if val != "":
                df.at[idx, col] = str(val)
        out = lookup(outcomes, key)
        if out:
            df.at[idx, "minutes_status"] = out["minutes_status"]
            for col in ("city", "county"):
                if out[col] and out[col] != row[col]:
                    df.at[idx, col] = out[col]
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


if __name__ == "__main__":
    main()
