"""One-time bootstrap of manual/ override files from the collaborator sheet.

The pipeline generates everything it can from primary documents and county
APIs; facts only a human collected live in manual/ CSVs, credited to their
source. This script seeds those files from the collaborator's sheet export
(input/grants.csv + input/sheet_parcels.csv, via import_sheet_export.py).

After running once, manual/ files are the source of truth — future manual
facts are edited there directly with source=manual-repo-edit.

Outputs:
    manual/grant_id_map.csv        project_id <-> generated grant crosswalk
    manual/manual_grants.csv       fully-manual grants (predate meeting archives)
    manual/grant_overrides.csv     long-format per-field manual values
    manual/parcel_assignments.csv  project_id -> parcel identity (AIN/APN)
    manual/parcel_values_manual.csv  roll values for non-LA parcels

Usage:
    python scripts/bootstrap_manual_from_sheet.py
"""

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.build_basic_list import norm_name  # noqa: E402

from fuzzywuzzy import fuzz

SHEET_SOURCE = "collaborator-sheet"  # export of 2026-08-21

BASIC_LIST = Path("output/pipeline/basic_list.csv")
SHEET_GRANTS = Path("input/grants.csv")
SHEET_PARCELS = Path("input/sheet_parcels.csv")
OUT = Path("manual")

# grants that predate the scraped meeting archives -> fully manual rows
PRE_ARCHIVE_IDS = {"192", "193", "223", "238"}

# sheet column -> dataset field, for fields a human researched and the
# documents either don't contain or got wrong (always emitted as overrides)
MANUAL_ONLY_FIELDS = {
    "Investor 2": "investor_2",
    "New build?": "new_build",
    "Built": "built",
    "Acquisition Price (M)": "acquisition_price_m",
    "Acquisition Date": "acquisition_date",
    "Link": "link",
    "Leasing Link": "leasing_link",
    "Closing Fee": "closing_fee",
    "CMFA Annual Fee": "cmfa_annual_fee",
    "SCC FILED?": "scc_filed",
}

# sheet column -> dataset field, for fields the pipeline CAN generate;
# an override is emitted only when the sheet meaningfully disagrees with or
# fills a blank in the generated value
GENERATABLE_FIELDS = {
    "Property Name": "property_name",
    "Applicant / Entity": "entity",
    "City": "city",
    "County": "county",
    "Resolution": "resolution",
    "Investor 1": "investor_1",
    "Nonprofit Partner": "nonprofit_partner",
    "Total Unit Count": "total_units",
    "Restricted Unit Count": "restricted_units",
    "Address": "address",
    "City's Cut": "city_cut",
    "Regulatory Term": "term_years",
}


LEGAL_NOISE = re.compile(
    r",?\s*(a (california|delaware) limited (partnership|liability company)"
    r"|a limited partnership|or an affiliate thereof|inc|llc|l\.?l\.?c"
    r"|lp|l\.?p|lllp)\.?\s*$", re.IGNORECASE)

ORG_FIELDS = {"entity", "investor_1", "investor_2", "nonprofit_partner"}


def norm_value(v: str, field: str = "") -> str:
    """Normalize for comparison: trim, collapse space, drop float '.0',
    currency symbols, case; for organization fields also strip legal
    suffixes (LP / L.P. / 'a California limited partnership' / ...)."""
    s = re.sub(r"\s+", " ", str(v).strip())
    s = s.replace("$", "").replace(",", "")
    if re.fullmatch(r"-?\d+\.0+", s):
        s = s.split(".")[0]
    try:
        f = float(s)
        return f"{f:g}"
    except ValueError:
        pass
    s = s.lower().rstrip(".")
    if field in ORG_FIELDS:
        prev = None
        while prev != s:
            prev = s
            s = LEGAL_NOISE.sub("", s).strip().rstrip(",.")
        s = s.replace(".", "")
    if field == "property_name":
        s = norm_name(s)
    return s


def match_grants(sheet: pd.DataFrame, gen: pd.DataFrame) -> pd.DataFrame:
    """Crosswalk sheet project rows to generated grants."""
    gen = gen.reset_index(drop=True)
    gen["_norm"] = gen["property_name"].map(norm_name)
    by_res = {}
    for i, r in gen.iterrows():
        res = str(r["resolution"]).strip()
        if res:
            by_res.setdefault(res, i)

    rows = []
    used = set()
    for _, s in sheet.iterrows():
        pid = s["Project ID"]
        if pid in PRE_ARCHIVE_IDS:
            continue
        res = str(s["Resolution"]).strip()
        gi, method = None, ""
        if res and res in by_res:
            gi, method = by_res[res], "resolution"
        else:
            # fuzzy name match; among equal-name candidates (re-authorized
            # properties appear once per authorization) prefer the generated
            # row whose meeting date is nearest the sheet's Date
            key = norm_name(s["Property Name"])
            s_date = str(s.get("Date", ""))
            candidates = []
            for i, r in gen.iterrows():
                if r["agency"] != s["Agency"]:
                    continue
                sc = max(fuzz.ratio(key, r["_norm"]), fuzz.token_sort_ratio(key, r["_norm"]))
                if sc >= 90:
                    candidates.append((sc, i))
            if candidates:
                top = max(sc for sc, _ in candidates)
                tied = [i for sc, i in candidates if sc == top]
                gi = min(tied, key=lambda i: abs(
                    (pd.to_datetime(gen.at[i, "meeting_date"], errors="coerce")
                     - pd.to_datetime(s_date, errors="coerce")).days
                    if s_date and pd.notna(pd.to_datetime(s_date, errors="coerce"))
                    else 0))
                method = f"fuzzy-name({top})"
        if gi is None:
            rows.append({"project_id": pid, "matched": "NO",
                         "generated_property": "", "generated_meeting_date": "",
                         "match_method": "", "sheet_property": s["Property Name"]})
        else:
            used.add(gi)
            rows.append({"project_id": pid, "matched": "YES",
                         "generated_property": gen.at[gi, "property_name"],
                         "generated_meeting_date": gen.at[gi, "meeting_date"],
                         "match_method": method, "sheet_property": s["Property Name"]})
    return pd.DataFrame(rows)


def main() -> None:
    gen = pd.read_csv(BASIC_LIST, dtype=str).fillna("")
    sheet = pd.read_csv(SHEET_GRANTS, dtype=str).fillna("")
    OUT.mkdir(exist_ok=True)

    # 1. crosswalk
    id_map = match_grants(sheet, gen)
    id_map.to_csv(OUT / "grant_id_map.csv", index=False)
    n_yes = (id_map["matched"] == "YES").sum()
    print(f"grant_id_map: {n_yes}/{len(id_map)} sheet rows matched to generated grants")
    for _, r in id_map[id_map["matched"] == "NO"].iterrows():
        print(f"  UNMATCHED [{r['project_id']}] {r['sheet_property']}")
    dupes = (id_map[id_map["matched"] == "YES"]
             .groupby(["generated_property", "generated_meeting_date"])["project_id"]
             .agg(list))
    for (prop, date), pids in dupes.items():
        if len(pids) > 1:
            print(f"  MANY-TO-ONE {pids} -> {prop} ({date})")

    # 2. fully-manual grants
    manual = sheet[sheet["Project ID"].isin(PRE_ARCHIVE_IDS)].copy()
    manual["source"] = SHEET_SOURCE
    manual.to_csv(OUT / "manual_grants.csv", index=False)
    print(f"manual_grants: {len(manual)} rows (predate meeting archives)")

    # 3. per-field overrides
    gen["_norm"] = gen["property_name"].map(norm_name)
    gen_by_key = {(r["_norm"], r["meeting_date"]): r for _, r in gen.iterrows()}
    matched = id_map.set_index("project_id")

    overrides = []
    for _, s in sheet.iterrows():
        pid = s["Project ID"]
        if pid in PRE_ARCHIVE_IDS:
            continue
        m = matched.loc[pid]
        gen_row = (gen_by_key.get((norm_name(m["generated_property"]),
                                   m["generated_meeting_date"]))
                   if m["matched"] == "YES" else None)

        for col, field in MANUAL_ONLY_FIELDS.items():
            v = str(s.get(col, "")).strip()
            if v not in ("", "False", "FALSE"):
                overrides.append({"project_id": pid, "field": field, "value": v,
                                  "source": SHEET_SOURCE, "note": ""})
        # status flags
        if str(s.get("DEAD?", "")).strip().lower() == "true":
            overrides.append({"project_id": pid, "field": "status", "value": "dead",
                              "source": SHEET_SOURCE, "note": ""})
        elif str(s.get("Stale", "")).strip().lower() == "true":
            overrides.append({"project_id": pid, "field": "status", "value": "stale",
                              "source": SHEET_SOURCE, "note": ""})

        for col, field in GENERATABLE_FIELDS.items():
            sv = str(s.get(col, "")).strip()
            if sv in ("", "Proposed", "CSCDA"):
                continue
            gv = str(gen_row[field]).strip() if gen_row is not None and field in gen_row else ""
            if norm_value(sv, field) != norm_value(gv, field):
                note = "fills blank" if gv == "" else f"differs from generated: {gv[:60]!r}"
                overrides.append({"project_id": pid, "field": field, "value": sv,
                                  "source": SHEET_SOURCE, "note": note})

    pd.DataFrame(overrides).to_csv(OUT / "grant_overrides.csv", index=False)
    by_field = pd.DataFrame(overrides)["field"].value_counts()
    print(f"grant_overrides: {len(overrides)} overrides")
    print(by_field.to_string())

    # 4. parcel assignments (identity) + non-LA values
    p = pd.read_csv(SHEET_PARCELS, dtype=str).fillna("")
    p = p[p["project_id"].str.strip() != ""]
    assignments = pd.DataFrame({
        "project_id": p["project_id"],
        "county": p["county"],
        "property_name": p["property"],
        "ain": p["ain"].str.replace("-", "").str.strip(),
        "apn": p["apn"],
        "situs_address": p["situs_address"],
        "legacy_redundant": p["legacy_redundant"],
        "assignment_source": SHEET_SOURCE,
        "method": p["method"],
        "notes": p["notes"],
    })
    assignments.to_csv(OUT / "parcel_assignments.csv", index=False)
    print(f"parcel_assignments: {len(assignments)} rows "
          f"({(assignments['county'] == 'Los Angeles').sum()} LA)")

    non_la = p[p["county"] != "Los Angeles"]
    values = pd.DataFrame({
        "project_id": non_la["project_id"],
        "ain": non_la["ain"].str.replace("-", "").str.strip(),
        "county": non_la["county"],
        "roll_year": non_la["roll_year"],
        "roll_total_value": non_la["roll_total_value"],
        "real_estate_exemp": non_la["real_estate_exemp"],
        "value_source": SHEET_SOURCE,
    })
    values = values[values["roll_total_value"].str.strip().isin(["", "-", "0"]) == False]  # noqa: E712
    values.to_csv(OUT / "parcel_values_manual.csv", index=False)
    print(f"parcel_values_manual: {len(values)} non-LA parcels with roll values")


if __name__ == "__main__":
    main()
