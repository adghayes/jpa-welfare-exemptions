"""Build the provenance-stamped dataset: grants + parcels + provenance + QA.

Merges:
  output/pipeline/basic_list.csv        generated grants (documents)
  manual/grant_id_map.csv               collaborator project_id crosswalk
  manual/manual_grants.csv              fully-manual grants (pre-archive)
  manual/grant_overrides.csv            per-field manual values
  manual/parcel_assignments.csv         project_id -> AIN (manual identity)
  output/pipeline/la_roll_values.csv    LA roll values (county API)
  manual/parcel_values_manual.csv       non-LA roll values (collaborator)

Outputs (output/dataset/):
  grants.csv      one row per authorization; grant-level facts only.
                  No roll values, no sums, no derived metrics — those are
                  computed at the sheet level by formulas.
  parcels.csv     one row per parcel, keyed by project_id; roll values with
                  per-row value_source. The sheet links to this by project_id.
  provenance.csv  long-format record of every manually-sourced value
  qa_findings.csv merge-time consistency findings

Generated grants not in the collaborator's sheet get new numeric project_ids
starting at NEW_ID_START (301), assigned deterministically by meeting date
then name, so sheet formulas keyed on project_id can't collide.

Usage:
    python scripts/build_dataset.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.build_basic_list import norm_name  # noqa: E402

OUT_DIR = Path("output/dataset")
NEW_ID_START = 301

GRANT_COLUMNS = [
    "project_id", "agency", "property_name", "entity", "city", "county",
    "resolution", "meeting_date", "item_type", "minutes_status",
    "investor_1", "investor_2", "nonprofit_partner",
    "total_units", "restricted_units", "rent_restricted_pct", "term_years",
    "city_cut", "grant_description", "address", "estimated_closing",
    "status", "new_build", "built", "acquisition_price_m", "acquisition_date",
    "link", "leasing_link", "scc_filed",
    "source_document_url", "row_source", "field_overrides",
]

# collaborator-sheet column -> dataset field, for fully-manual rows
SHEET_TO_DATASET = {
    "Property Name": "property_name", "Applicant / Entity": "entity",
    "City": "city", "County": "county", "Agency": "agency",
    "Resolution": "resolution", "Date": "meeting_date",
    "Investor 1": "investor_1", "Investor 2": "investor_2",
    "Nonprofit Partner": "nonprofit_partner",
    "Total Unit Count": "total_units", "Restricted Unit Count": "restricted_units",
    "Address": "address", "Grant Description": "grant_description",
    "New build?": "new_build", "Built": "built",
    "Acquisition Price (M)": "acquisition_price_m",
    "Acquisition Date": "acquisition_date",
    "Link": "link", "Leasing Link": "leasing_link",
    "SCC FILED?": "scc_filed", "City's Cut": "city_cut",
    "Regulatory Term": "term_years",
}

findings: list[dict] = []
provenance: list[dict] = []


def qa(check: str, project_id: str, detail: str) -> None:
    findings.append({"check": check, "project_id": project_id, "detail": detail})


def main() -> None:
    gen = pd.read_csv("output/pipeline/basic_list.csv", dtype=str).fillna("")
    id_map = pd.read_csv("manual/grant_id_map.csv", dtype=str).fillna("")
    manual_grants = pd.read_csv("manual/manual_grants.csv", dtype=str).fillna("")
    overrides = pd.read_csv("manual/grant_overrides.csv", dtype=str).fillna("")

    # --- assign project ids -------------------------------------------------
    gen = gen.reset_index(drop=True)
    gen["_key"] = gen["property_name"].map(norm_name) + "|" + gen["meeting_date"]

    key_to_pids: dict[str, list[str]] = {}
    for _, m in id_map[id_map["matched"] == "YES"].iterrows():
        key = norm_name(m["generated_property"]) + "|" + m["generated_meeting_date"]
        key_to_pids.setdefault(key, []).append(m["project_id"])

    for key, pids in key_to_pids.items():
        if len(pids) > 1:
            qa("duplicate-sheet-rows", "/".join(pids),
               f"multiple sheet projects map to one authorization ({key.split('|')[0]})")

    rows: list[dict] = []
    unmatched_gen = []
    for _, g in gen.iterrows():
        pids = key_to_pids.get(g["_key"])
        base = {c: g.get(c, "") for c in GRANT_COLUMNS if c in g.index}
        if pids:
            for pid in pids:
                rows.append({**base, "project_id": pid, "row_source": "generated"})
        else:
            unmatched_gen.append(base)

    unmatched_gen.sort(key=lambda r: (r["meeting_date"], r["property_name"]))
    next_id = NEW_ID_START
    for base in unmatched_gen:
        rows.append({**base, "project_id": str(next_id), "row_source": "generated"})
        if base["item_type"] == "authorize":
            qa("new-grant", str(next_id),
               f"{base['property_name']} ({base['agency']}, {base['meeting_date']}) "
               "not in collaborator sheet")
        next_id += 1

    grants = pd.DataFrame(rows).reindex(columns=GRANT_COLUMNS).fillna("")

    # --- apply manual overrides --------------------------------------------
    grants = grants.set_index("project_id", drop=False)
    field_overrides: dict[str, list[str]] = {}
    for _, o in overrides.iterrows():
        pid, field, value = o["project_id"], o["field"], o["value"]
        if pid not in grants.index:
            qa("override-orphan", pid, f"override for unknown project ({field})")
            continue
        if field not in GRANT_COLUMNS:
            qa("override-unknown-field", pid, field)
            continue
        current = str(grants.at[pid, field])
        if current and "differs" in o["note"]:
            qa("override-conflict", pid,
               f"{field}: generated {current!r} -> manual {value!r}")
        grants.at[pid, field] = value
        field_overrides.setdefault(pid, []).append(f"{field}:{o['source']}")
        provenance.append({"table": "grants", "project_id": pid, "field": field,
                           "value": value, "source": o["source"], "note": o["note"]})

    grants["field_overrides"] = grants["project_id"].map(
        lambda p: "; ".join(field_overrides.get(p, [])))

    # --- fully-manual rows ---------------------------------------------------
    manual_rows = []
    for _, s in manual_grants.iterrows():
        row = {c: "" for c in GRANT_COLUMNS}
        for sheet_col, field in SHEET_TO_DATASET.items():
            row[field] = str(s.get(sheet_col, "")).strip()
        row["project_id"] = s["Project ID"]
        row["item_type"] = "authorize"
        row["row_source"] = s.get("source", "collaborator-sheet")
        row["field_overrides"] = "*:" + row["row_source"]
        manual_rows.append(row)
        provenance.append({"table": "grants", "project_id": row["project_id"],
                           "field": "*", "value": "", "source": row["row_source"],
                           "note": "entire row manual (predates meeting archives)"})
    grants = pd.concat([grants.reset_index(drop=True), pd.DataFrame(manual_rows)],
                       ignore_index=True)
    grants["_pid_num"] = pd.to_numeric(grants["project_id"], errors="coerce")
    grants = grants.sort_values(["_pid_num"]).drop(columns="_pid_num")

    # link each grant to its most descriptive source document (review aid)
    doc_urls = pd.read_csv("output/pipeline/doc_urls.csv", dtype=str).fillna("")
    url_by_key = {(r["agency"], r["meeting_date"]): r["url"] for _, r in doc_urls.iterrows()}
    grants["source_document_url"] = [
        url_by_key.get((r["agency"], r["meeting_date"]), "")
        for _, r in grants.iterrows()
    ]

    # --- parcels --------------------------------------------------------------
    assignments = pd.read_csv("manual/parcel_assignments.csv", dtype=str).fillna("")
    manual_values = pd.read_csv("manual/parcel_values_manual.csv", dtype=str).fillna("")

    # every county with an automated fetcher writes output/pipeline/<x>_roll_values.csv
    api_by_ain: dict[str, dict] = {}
    automated_counties = set()
    for path in sorted(Path("output/pipeline").glob("*_roll_values.csv")):
        vals = pd.read_csv(path, dtype=str).fillna("")
        for _, r in vals.iterrows():
            api_by_ain[r["ain"]] = r.to_dict()
    county_of_source = {"la-county-api": "Los Angeles",
                        "solano-county-portal": "Solano"}
    automated_counties = {county_of_source.get(v.get("value_source", ""), "")
                          for v in api_by_ain.values()} - {""}

    manual_by_key = {(r["project_id"], r["ain"]): r for _, r in manual_values.iterrows()}

    VALUE_COLS = ["roll_year", "roll_land_value", "roll_imp_value",
                  "roll_total_value", "real_estate_exemp", "year_built",
                  "use_description", "value_source", "fetch_date"]
    parcel_rows = []
    for _, a in assignments.iterrows():
        row = a.to_dict()
        key = (a["project_id"], a["ain"])
        vals = {c: "" for c in VALUE_COLS}
        if a["ain"] in api_by_ain:
            v = api_by_ain[a["ain"]]
            for c in VALUE_COLS:
                vals[c] = v.get(c, "")
        elif key in manual_by_key:
            v = manual_by_key[key]
            for c in ["roll_year", "roll_total_value", "real_estate_exemp", "value_source"]:
                vals[c] = v.get(c, "")
            automated = a["county"] in automated_counties
            note = ("AIN returned no data from county source"
                    if automated else "no automated source for county")
            if automated:
                qa("fetch-fallback", a["project_id"],
                   f"AIN {a['ain']} ({a['county']}) has no current roll data; using manual value")
            provenance.append({"table": "parcels", "project_id": a["project_id"],
                               "field": f"values:{a['ain']}", "value": v.get("roll_total_value", ""),
                               "source": v.get("value_source", ""), "note": note})
        else:
            qa("parcel-no-values", a["project_id"],
               f"AIN {a['ain'] or '(none)'} ({a['county']}): no roll values from any source")
        # parcel identity is always manual (collaborator assignment)
        provenance.append({"table": "parcels", "project_id": a["project_id"],
                           "field": f"assignment:{a['ain']}", "value": a["ain"],
                           "source": a["assignment_source"], "note": a["method"]})
        parcel_rows.append({**row, **vals})
    parcels = pd.DataFrame(parcel_rows)

    # --- QA ---------------------------------------------------------------
    live = grants[(grants["status"] != "dead") & (grants["item_type"] == "authorize")]
    has_parcels = set(parcels["project_id"])
    for _, g in live.iterrows():
        if g["project_id"] in has_parcels:
            continue
        try:
            is_sheet_project = int(g["project_id"]) < NEW_ID_START
        except ValueError:
            is_sheet_project = True
        if is_sheet_project:
            qa("missing-parcels", g["project_id"],
               f"{g['property_name']}: authorized, not dead, no parcel rows")

    live_p = parcels[(parcels["legacy_redundant"].str.lower() != "true")
                     & (parcels["ain"].str.strip() != "")]
    for ain, pids in live_p.groupby("ain")["project_id"].agg(set).items():
        if len(pids) > 1:
            qa("shared-ain", "/".join(sorted(pids)), f"AIN {ain} assigned to multiple projects")

    # --- write ---------------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    grants.to_csv(OUT_DIR / "grants.csv", index=False)
    parcels.to_csv(OUT_DIR / "parcels.csv", index=False)
    pd.DataFrame(provenance).to_csv(OUT_DIR / "provenance.csv", index=False)
    qa_df = pd.DataFrame(findings)
    qa_df.to_csv(OUT_DIR / "qa_findings.csv", index=False)

    print(f"grants:  {len(grants)} rows "
          f"({(grants['row_source'] == 'generated').sum()} generated, "
          f"{(grants['row_source'] != 'generated').sum()} manual)")
    print(f"parcels: {len(parcels)} rows; value sources: "
          f"{parcels['value_source'].value_counts(dropna=False).to_dict()}")
    print(f"provenance: {len(provenance)} entries")
    print(f"qa_findings: {len(findings)}")
    if len(qa_df):
        print(qa_df["check"].value_counts().to_string())


if __name__ == "__main__":
    main()
