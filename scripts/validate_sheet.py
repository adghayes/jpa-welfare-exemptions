"""Validate internal consistency of the collaborator's sheet (imported CSVs).

Reads input/grants.csv, input/sheet_parcels.csv, input/sheet_tax_rates.csv
(produced by scripts/import_sheet_export.py) and cross-checks:

  A. Grants arithmetic: restricted ratio, revenue loss = exemption x rate,
     coalescing of confirmed/best-estimate columns, welfare-flag consistency.
  B. Grants <-> Parcels: coverage, roll-value sums, unit counts, tax rates.
  C. Parcels <-> Tax Rates: per-parcel rate matches the rate table.
  D. Duplicates: AINs shared across projects, duplicate resolutions.

Output: console summary + output/sheet_validation/findings.csv
(columns: check, project_id, property, detail). Each finding is a candidate
manual edit to the Google Sheet — the sheet is the master, nothing here
modifies data.

Usage:
    python scripts/validate_sheet.py
"""

import csv
from pathlib import Path

import pandas as pd

GRANTS = Path("input/grants.csv")
PARCELS = Path("input/sheet_parcels.csv")
RATES = Path("input/sheet_tax_rates.csv")
OUT_DIR = Path("output/sheet_validation")

MONEY_TOL = 1.0        # dollars
MONEY_REL_TOL = 0.005  # 0.5%
RATE_TOL = 5e-7
RATIO_TOL = 1e-4

findings: list[dict] = []


def add(check: str, project_id: str, prop: str, detail: str) -> None:
    findings.append(
        {"check": check, "project_id": project_id, "property": prop, "detail": detail}
    )


def money(v) -> float | None:
    s = str(v).replace("$", "").replace(",", "").strip()
    if s in ("", "-", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def close_money(a: float, b: float) -> bool:
    return abs(a - b) <= max(MONEY_TOL, MONEY_REL_TOL * max(abs(a), abs(b)))


def rate_key(s: str) -> str:
    s = str(s).strip()
    return s.zfill(5) if s.isdigit() else s.lower()


def main() -> None:
    g = pd.read_csv(GRANTS, dtype=str).fillna("")
    p = pd.read_csv(PARCELS, dtype=str).fillna("")
    p = p[p["project_id"].str.strip() != ""]
    r = pd.read_csv(RATES, dtype=str).fillna("")

    rates = {rate_key(row["tax_rate_area"]): float(row["total_rate"]) for _, row in r.iterrows()}

    is_dead = g["DEAD?"].str.lower() == "true"

    # --- A. grants arithmetic ---
    for _, row in g.iterrows():
        pid, prop = row["Project ID"], row["Property Name"]
        total_u, restr_u = money(row["Total Unit Count"]), money(row["Restricted Unit Count"])
        ratio = money(row["Restricted Ratio"])
        if total_u and restr_u is not None and ratio is not None:
            if abs(ratio - restr_u / total_u) > RATIO_TOL:
                add("A1-restricted-ratio", pid, prop,
                    f"Restricted Ratio {ratio:.6f} != {restr_u:.0f}/{total_u:.0f} = {restr_u/total_u:.6f}")

        rate = money(row["Tax Rate"])
        # Sheet convention: "Best Estimate Annual Revenue Loss" is derived from
        # "Confirmed or Best Estimate Exemption" (actual roll exemption when
        # confirmed, modeled estimate otherwise), not from "Best Estimate Exemption".
        for exemp_col, loss_col, tag in (
            ("Confirmed Exemption", "Confirmed Annual Revenue Loss", "A2-confirmed-loss"),
            ("Confirmed or Best Estimate Exemption", "Best Estimate Annual Revenue Loss", "A3-best-estimate-loss"),
        ):
            exemp, loss = money(row[exemp_col]), money(row[loss_col])
            if rate is not None and exemp is not None and loss is not None:
                if not close_money(loss, exemp * rate):
                    add(tag, pid, prop,
                        f"{loss_col} {loss:,.2f} != {exemp_col} {exemp:,.2f} x rate {rate} = {exemp*rate:,.2f}")

        conf = money(row["Confirmed Exemption"])
        best = money(row["Best Estimate Exemption"])
        either = money(row["Confirmed or Best Estimate Exemption"])
        expected = conf if (conf is not None and conf > 0) else best
        if either is not None and expected is not None and not close_money(either, expected):
            add("A4-coalesce-exemption", pid, prop,
                f"Confirmed or Best Estimate {either:,.2f} != expected {expected:,.2f} "
                f"(confirmed {conf}, best {best})")

        on_roll = row["Welfare Exemption On 2025 Roll"].strip().lower()
        if on_roll == "true" and (conf is None or conf <= 0):
            add("A5-welfare-flag", pid, prop,
                "Welfare Exemption On 2025 Roll is TRUE but Confirmed Exemption is empty/zero")
        if on_roll == "false" and conf is not None and conf > 0:
            add("A5-welfare-flag", pid, prop,
                f"Welfare Exemption On 2025 Roll is FALSE but Confirmed Exemption is {conf:,.0f}")

    # --- B. grants <-> parcels ---
    by_project = dict(tuple(p.groupby("project_id")))
    grant_ids = set(g["Project ID"])
    for pid in by_project:
        if pid not in grant_ids:
            add("B0-orphan-parcel", pid, by_project[pid]["property"].iloc[0],
                "Parcel rows reference a project_id missing from the grants tab")

    for _, row in g.iterrows():
        pid, prop = row["Project ID"], row["Property Name"]
        rows = by_project.get(pid)
        identified = row["Parcel Identified"].strip().lower() == "true"
        if identified and rows is None:
            add("B1-missing-parcels", pid, prop,
                "Parcel Identified is TRUE but no rows in Parcels tab")
            continue
        if rows is None:
            if not is_dead[g["Project ID"] == pid].iloc[0]:
                add("B1-missing-parcels", pid, prop,
                    "No parcel rows (and not marked DEAD)")
            continue

        live = rows[rows["legacy_redundant"].str.lower() != "true"]

        roll_vals = [money(v) for v in live["roll_total_value"]]
        if all(v is not None for v in roll_vals) and roll_vals:
            total = sum(roll_vals)
            sheet_total = money(row["Total Roll Value"])
            if sheet_total is not None and not close_money(total, sheet_total):
                add("B2-roll-value-sum", pid, prop,
                    f"Grants Total Roll Value {sheet_total:,.0f} != sum of parcel "
                    f"roll_total_value {total:,.0f} ({len(live)} parcels)")

        p_units = {money(v) for v in live["CMFA Total Units"] if money(v) is not None}
        g_units = money(row["Total Unit Count"])
        if p_units and g_units is not None and p_units != {g_units}:
            add("B3-unit-mismatch", pid, prop,
                f"Parcels 'CMFA Total Units' {sorted(p_units)} != grants Total Unit Count {g_units:.0f}")

        p_rates = {money(v) for v in live["Total Tax Rate"] if money(v) is not None}
        g_rate = money(row["Tax Rate"])
        if g_rate is not None and p_rates and all(abs(pr - g_rate) > RATE_TOL for pr in p_rates):
            add("B4-rate-mismatch", pid, prop,
                f"Grants Tax Rate {g_rate} matches none of parcel rates {sorted(p_rates)}")

    # --- C. parcels <-> tax-rate table ---
    for _, row in p.iterrows():
        area = row["tax_rate_area"].strip()
        stated = money(row["Total Tax Rate"])
        if not area or stated is None:
            continue
        table = rates.get(rate_key(area))
        if table is None:
            add("C1-unknown-rate-area", row["project_id"], row["property"],
                f"tax_rate_area {area!r} not found in Tax Rates tab")
        elif abs(table - stated) > RATE_TOL:
            add("C2-rate-table-mismatch", row["project_id"], row["property"],
                f"Parcel Total Tax Rate {stated} != Tax Rates tab {table} for {area!r}")

    # --- D. duplicates ---
    live_p = p[(p["legacy_redundant"].str.lower() != "true") & (p["ain"].str.strip() != "")]
    ain_projects = live_p.groupby("ain")["project_id"].agg(set)
    for ain, pids in ain_projects.items():
        if len(pids) > 1:
            add("D1-shared-ain", "/".join(sorted(pids)), "",
                f"AIN {ain} appears under multiple projects")

    real_res = g[~g["Resolution"].str.strip().isin(["", "Proposed", "CSCDA"])]
    res_counts = real_res.groupby("Resolution")["Project ID"].agg(list)
    for res, pids in res_counts.items():
        if len(pids) > 1:
            add("D2-duplicate-resolution", "/".join(pids), "",
                f"Resolution {res!r} appears on multiple projects")

    # --- report ---
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "findings.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["check", "project_id", "property", "detail"])
        w.writeheader()
        w.writerows(findings)

    print(f"{len(findings)} findings -> {out}\n")
    df = pd.DataFrame(findings)
    if not df.empty:
        print(df["check"].value_counts().to_string())


if __name__ == "__main__":
    main()
