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
    "resolution", "meeting_date",
    # status block: how far the item got (documents), what the minutes say,
    # the property-level rollup, and the collaborator's judgment call
    "item_type", "minutes_status", "authorization_status", "superseded_by",
    "manual_status",
    # BOE welfare-exemption certificate — evaluated together with status
    "scc_filed", "scc_number", "scc_issue_date",
    # review links: the meeting's documents (CSCDA: packet fills
    # staff_report_url; its minutes live in the NEXT meeting's packet)
    "agenda_url", "staff_report_url", "minutes_url",
    "investor_1", "investor_2", "nonprofit_partner",
    "total_units", "restricted_units", "rent_restricted_pct", "term_years",
    "city_cut", "grant_description", "address", "estimated_closing",
    "new_build", "built", "acquisition_price_m", "acquisition_date",
    "link", "leasing_link",
    "row_source", "field_overrides",
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

    # pipeline-assigned ids must be STABLE across refreshes (manual/ files
    # reference them), so they are recorded in an append-only ledger keyed by
    # (normalized property | meeting date) and never recomputed or reused
    ledger_path = Path("manual/generated_id_ledger.csv")
    if ledger_path.exists():
        ledger_df = pd.read_csv(ledger_path, dtype=str).fillna("")
        ledger = dict(zip(ledger_df["key"], ledger_df["project_id"]))
    else:
        ledger = {}
    next_id = max([NEW_ID_START - 1] + [int(v) for v in ledger.values()]) + 1

    unmatched_gen.sort(key=lambda r: (r["meeting_date"], r["property_name"]))
    ledger_dirty = False
    for base in unmatched_gen:
        key = norm_name(base["property_name"]) + "|" + base["meeting_date"]
        pid = ledger.get(key)
        if pid is None:
            pid = str(next_id)
            next_id += 1
            ledger[key] = pid
            ledger_dirty = True
        rows.append({**base, "project_id": pid, "row_source": "generated"})
        if base["item_type"] == "authorize":
            qa("new-grant", pid,
               f"{base['property_name']} ({base['agency']}, {base['meeting_date']}) "
               "not in collaborator sheet")
    if ledger_dirty:
        pd.DataFrame(sorted(ledger.items(), key=lambda kv: int(kv[1])),
                     columns=["key", "project_id"]).to_csv(ledger_path, index=False)

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
        if str(s.get("DEAD?", "")).strip().lower() == "true":
            row["manual_status"] = "dead"
        elif str(s.get("Stale", "")).strip().lower() == "true":
            row["manual_status"] = "stale"
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

    # --- property grouping: operative vs superseded authorizations ---------
    # CMFA/CSCDA re-run the full approval when a deal slips or is pulled and
    # re-agendized, and a property that changes sponsors gets a fresh grant.
    # For counting, ONE authorization per property is operative: the latest
    # one the minutes actually approved. Earlier ones are 'superseded';
    # items the minutes record as pulled are 'pulled' and never operative.
    grants["_prop"] = grants["property_name"].map(norm_name) + "|" + grants["county"]
    grants["authorization_status"] = ""
    grants["superseded_by"] = ""
    operative_of_prop: dict[str, str] = {}
    for prop, grp in grants.groupby("_prop"):
        prelim = grp[grp["item_type"] == "preliminary_only"]
        grants.loc[prelim.index, "authorization_status"] = "preliminary"
        auth = grp[grp["item_type"] == "authorize"]
        if auth.empty:
            continue
        pulled = auth[auth["minutes_status"] == "pulled"]
        live_auth = auth[auth["minutes_status"] != "pulled"]
        grants.loc[pulled.index, "authorization_status"] = "pulled"
        if live_auth.empty:
            continue  # only pulled attempts: never authorized
        approved = live_auth[live_auth["minutes_status"] == "approved"]
        pool = approved if not approved.empty else live_auth
        operative_idx = pool["meeting_date"].idxmax()
        operative_pid = grants.at[operative_idx, "project_id"]
        operative_of_prop[prop] = operative_pid
        grants.at[operative_idx, "authorization_status"] = "operative"
        others = live_auth.index.difference([operative_idx])
        grants.loc[others, "authorization_status"] = "superseded"
        grants.loc[others, "superseded_by"] = operative_pid
    pid_to_operative = {r["project_id"]: operative_of_prop.get(r["_prop"], "")
                        for _, r in grants.iterrows()}
    n_status = grants["authorization_status"].value_counts().to_dict()
    grants = grants.drop(columns="_prop")

    # --- BOE Supplemental Clearance Certificates ---------------------------
    # An LP needs an SCC before the assessor can grant the welfare exemption.
    # Match each grant's entity against the BOE list (exact after stripping
    # legal suffixes; county must agree when the same name matches several
    # certificates). Near-misses become QA findings for human review.
    import re as _re
    from fuzzywuzzy import fuzz as _fuzz

    _LEGAL = _re.compile(
        r",?\s*((a|an)? ?(california|delaware|washington)? ?limited (partnership|liability company)"
        r"|or an affiliate( thereof)?|inc|llc|l\.?l\.?c|lp|l\.?p|lllp)\.?\s*$",
        _re.IGNORECASE)

    def norm_org(s: str) -> str:
        s = str(s).lower().replace(".", "").replace(",", "")
        s = _re.sub(r"[–—−]", "-", s)  # BOE uses hyphens; agendas mix in en-dashes
        s = _re.sub(r"\s+", " ", s).strip()
        prev = None
        while prev != s:
            prev = s
            s = _LEGAL.sub("", s).strip().rstrip(",.")
        return s

    scc = pd.read_csv("output/pipeline/scc_certificates.csv", dtype=str).fillna("")
    scc_by_name: dict[str, list[dict]] = {}
    for _, cert in scc.iterrows():
        scc_by_name.setdefault(norm_org(cert["limited_partnership"]), []).append(cert.to_dict())

    # scc columns already exist (reindex) and may hold manual override values
    # for grants the BOE list doesn't match — never wipe them
    scc_names = list(scc_by_name)
    for idx, r in grants.iterrows():
        key = norm_org(r["entity"])
        manual_filed = str(r.get("scc_filed", "")).strip()
        if not key or (manual_filed and str(r.get("scc_number", "")).strip()):
            continue
        certs = scc_by_name.get(key, [])
        if manual_filed:
            # human says filed, but the matcher found no certificate. BOE is
            # authoritative for SCCs: corroborate exactly or flag for review.
            if certs:
                county_match = [c for c in certs if c["county"].title() == r["county"].title()]
                cert = (county_match or certs)[0]
                grants.at[idx, "scc_number"] = cert["scc_number"]
                grants.at[idx, "scc_issue_date"] = cert["issue_date"]
            else:
                best, score = None, 0
                for name in scc_names:
                    s = _fuzz.token_sort_ratio(key, name)
                    if s > score:
                        best, score = name, s
                cert = scc_by_name[best][0] if best and score >= 70 else None
                qa("scc-manual-unmatched", r["project_id"],
                   f"manual scc_filed={manual_filed} but no BOE certificate matches "
                   f"entity {r['entity'][:40]!r}"
                   + (f"; closest: {cert['limited_partnership'][:40]!r} "
                      f"(#{cert['scc_number']}, {cert['county'].title()}, score {score})"
                      if cert else ""))
            continue
        if certs:
            county_match = [c for c in certs if c["county"].title() == r["county"].title()]
            cert = (county_match or certs)[0]
            grants.at[idx, "scc_filed"] = "True"
            grants.at[idx, "scc_number"] = cert["scc_number"]
            grants.at[idx, "scc_issue_date"] = cert["issue_date"]
            if not county_match and r["county"]:
                qa("scc-county-mismatch", r["project_id"],
                   f"SCC {cert['scc_number']} matches entity but is filed in "
                   f"{cert['county'].title()}, grant county {r['county']}")
        elif r["item_type"] == "authorize":
            best, score = None, 0
            for name in scc_names:
                s = _fuzz.token_sort_ratio(key, name)
                if s > score:
                    best, score = name, s
            if 90 <= score < 100:
                cert = scc_by_name[best][0]
                qa("scc-possible-match", r["project_id"],
                   f"entity {r['entity'][:40]!r} ~ SCC LP {cert['limited_partnership'][:40]!r} "
                   f"(#{cert['scc_number']}, {cert['county'].title()}, score {score})")

    # link each grant to its meeting's documents (review aid)
    doc_urls = pd.read_csv("output/pipeline/doc_urls.csv", dtype=str).fillna("")
    url_by_key = {(r["agency"], r["meeting_date"]): r for _, r in doc_urls.iterrows()}
    for col in ("agenda_url", "staff_report_url", "minutes_url"):
        grants[col] = [
            url_by_key.get((r["agency"], r["meeting_date"]), {}).get(col, "")
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
                        "solano-county-portal": "Solano",
                        "sandag-parcels": "San Diego"}
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
            # some sources publish no exemption field (e.g. SANDAG);
            # keep the manual exemption for that parcel, field-level
            if str(vals.get("real_estate_exemp", "")).strip() == "" and key in manual_by_key:
                mv = manual_by_key[key]
                if str(mv.get("real_estate_exemp", "")).strip() not in ("", "0", "0.0"):
                    vals["real_estate_exemp"] = mv["real_estate_exemp"]
                    provenance.append({
                        "table": "parcels", "project_id": a["project_id"],
                        "field": f"real_estate_exemp:{a['ain']}",
                        "value": mv["real_estate_exemp"],
                        "source": mv.get("value_source", ""),
                        "note": "value source publishes no exemption field"})
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
            # preliminary-only properties have no grant yet, so missing
            # values are expected; dead rows STAY flagged — the dead flag is
            # manual data we don't want to trust silently
            auth = grants.loc[grants["project_id"] == a["project_id"], "authorization_status"]
            if (a["legacy_redundant"].strip().lower() != "true"
                    and not (len(auth) and auth.iloc[0] == "preliminary")):
                qa("parcel-no-values", a["project_id"],
                   f"AIN {a['ain'] or '(none)'} ({a['county']}): no roll values from any source")
        # parcel identity is always manual (collaborator assignment)
        provenance.append({"table": "parcels", "project_id": a["project_id"],
                           "field": f"assignment:{a['ain']}", "value": a["ain"],
                           "source": a["assignment_source"], "note": a["method"]})
        parcel_rows.append({**row, **vals})
    parcels = pd.DataFrame(parcel_rows)
    # parcels belong to the property: expose the operative project id so
    # sheet formulas sum each property exactly once
    parcels["operative_project_id"] = parcels["project_id"].map(
        lambda p: pid_to_operative.get(p) or p)

    # per-AIN situs-validation verdict (from check_parcel_assignments.py)
    verdicts_path = Path("output/pipeline/assignment_verdicts.csv")
    parcels["assignment_check"] = ""
    if verdicts_path.exists():
        vd = pd.read_csv(verdicts_path, dtype=str).fillna("")
        vmap = {(r["project_id"], r["ain"]): r["assignment_check"] for _, r in vd.iterrows()}
        parcels["assignment_check"] = [
            vmap.get((r["project_id"], r["ain"]), "")
            for _, r in parcels.iterrows()]

    # --- QA ---------------------------------------------------------------
    # parcels are property-level: an operative grant is covered if ANY row
    # of its property group has parcels
    live = grants[(grants["manual_status"] != "dead")
                  & (grants["authorization_status"] == "operative")]
    covered = set(parcels["project_id"]) | set(parcels["operative_project_id"])
    for _, g in live.iterrows():
        if g["project_id"] not in covered:
            qa("missing-parcels", g["project_id"],
               f"{g['property_name']} ({g['county']}): operative authorization, "
               "not dead, no parcels anywhere in its property group")

    # an AIN shared within one property group is supersession working as
    # intended (re-authorizations inherit the property's parcels); only an
    # AIN spanning DIFFERENT operative properties is an error
    live_p = parcels[(parcels["legacy_redundant"].str.lower() != "true")
                     & (parcels["ain"].str.strip() != "")]
    for ain, ops in live_p.groupby("ain")["operative_project_id"].agg(set).items():
        if len(ops) > 1:
            qa("shared-ain", "/".join(sorted(ops)),
               f"AIN {ain} assigned to multiple distinct properties")

    # --- assignment checks (situs validation, missing siblings, unit counts) --
    checks_path = Path("output/pipeline/assignment_checks.csv")
    if checks_path.exists():
        for _, c in pd.read_csv(checks_path, dtype=str).fillna("").iterrows():
            qa(c["check"], c["project_id"], c["detail"])

    # --- write ---------------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    grants.to_csv(OUT_DIR / "grants.csv", index=False)
    parcels.to_csv(OUT_DIR / "parcels.csv", index=False)
    pd.DataFrame(provenance).to_csv(OUT_DIR / "provenance.csv", index=False)
    qa_df = pd.DataFrame(findings, columns=["check", "project_id", "detail"])
    qa_df.to_csv(OUT_DIR / "qa_findings.csv", index=False)

    print(f"grants:  {len(grants)} rows "
          f"({(grants['row_source'] == 'generated').sum()} generated, "
          f"{(grants['row_source'] != 'generated').sum()} manual)")
    print(f"authorization_status: {n_status}")
    print(f"parcels: {len(parcels)} rows; value sources: "
          f"{parcels['value_source'].value_counts(dropna=False).to_dict()}")
    print(f"provenance: {len(provenance)} entries")
    print(f"qa_findings: {len(findings)}")
    if len(qa_df):
        print(qa_df["check"].value_counts().to_string())


if __name__ == "__main__":
    main()
