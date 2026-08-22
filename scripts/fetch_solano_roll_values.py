"""Fetch current assessment-roll values for Solano County parcel assignments.

Solano's assessor runs a PublicAccessNow portal (ca-solano.publicaccessnow.com).
Two JSON endpoints do the work, both public and unauthenticated:

  1. QuickSearch (ModuleId 470, TabId 38):
     /DesktopModules/QuickSearch/API/Module/GetData?keywords=<APN>&page=1
     -> resolves a 10-digit parcel id to its "Altkey" record key
  2. Value History DataDisplay (ModuleId 473, TabId 39):
     /API/DataDisplay/DataSources/GetData?p=<parcelid>&a=<altkey>
     -> per-tax-year rows: Assessment Value by attribute (Land / Improvement /
        Personal / Trade Fixtures) plus Net Taxable Value

For each assigned Solano parcel we keep the LATEST tax year's values:
roll_land_value / roll_imp_value from the assessment rows, and
real_estate_exemp derived as (total assessed - net taxable).

Output: output/pipeline/solano_roll_values.csv (same schema as the LA fetch).

Usage:
    python scripts/fetch_solano_roll_values.py
"""

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

BASE = "https://ca-solano.publicaccessnow.com"
SEARCH_URL = BASE + "/DesktopModules/QuickSearch/API/Module/GetData"
VALUES_URL = BASE + "/API/DataDisplay/DataSources/GetData"
SEARCH_CTX = {"ModuleId": "470", "TabId": "38"}
VALUES_CTX = {"ModuleId": "473", "TabId": "39"}
RATE_LIMIT = 0.4

ASSIGNMENTS = Path("manual/parcel_assignments.csv")
OUT = Path("output/pipeline/solano_roll_values.csv")


def get_json(url: str, ctx: dict) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **ctx})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def solano_parcel_id(ain: str) -> str:
    return ain.strip().replace("-", "").zfill(10)


def find_altkey(parcel_id: str) -> str | None:
    data = get_json(f"{SEARCH_URL}?keywords={parcel_id}&page=1", SEARCH_CTX)
    for item in data.get("items", []):
        f = item["fields"]
        if f.get("ParcelID") == parcel_id:
            return str(f["Altkey"]), f.get("Situs", ""), f.get("Situscity", "")
    return None


def latest_year_values(parcel_id: str, altkey: str) -> dict | None:
    data = get_json(f"{VALUES_URL}?p={parcel_id}&a={altkey}", VALUES_CTX)
    by_year: dict[str, dict] = {}
    for g in data.get("groups", []):
        year = {c["name"]: c["value"] for c in g["groupedColumns"]}.get("TaxYear", "")
        rec = by_year.setdefault(year, {"assessed": {}, "net_taxable": None})
        for sub in g.get("groups", []):
            for row in sub.get("rows", []):
                vals = {v["column"]: v["value"] for v in row["values"]}
                vtype = vals.get("ValueTypeDescr", "")
                amount = float(vals.get("ValueAmount") or 0)
                attr = vals.get("Attribute1Formatted", "")
                if vtype == "Assessment Value":
                    rec["assessed"][attr] = rec["assessed"].get(attr, 0) + amount
                elif vtype == "Net Taxable Value":
                    rec["net_taxable"] = amount
    if not by_year:
        return None
    year = max(y for y in by_year if y)
    rec = by_year[year]
    land = rec["assessed"].get("Land", 0)
    imp = rec["assessed"].get("Improvement", 0)
    total_assessed = sum(rec["assessed"].values())
    net = rec["net_taxable"]
    exemp = max(0.0, total_assessed - net) if net is not None else 0.0
    return {
        "roll_year": year,
        "roll_land_value": land,
        "roll_imp_value": imp,
        "roll_total_value": land + imp,
        "real_estate_exemp": exemp,
    }


def main() -> None:
    a = pd.read_csv(ASSIGNMENTS, dtype=str).fillna("")
    sol = a[(a["county"] == "Solano") & (a["ain"].str.strip() != "")]
    parcel_ids = sorted({solano_parcel_id(x) for x in sol["ain"]})
    print(f"{len(parcel_ids)} unique Solano parcel ids assigned")

    rows, missing = [], []
    today = date.today().isoformat()
    for i, pid in enumerate(parcel_ids, 1):
        try:
            hit = find_altkey(pid)
            time.sleep(RATE_LIMIT)
            if not hit:
                missing.append((pid, "not found in portal search"))
                continue
            altkey, situs, city = hit
            vals = latest_year_values(pid, altkey)
            time.sleep(RATE_LIMIT)
            if not vals:
                missing.append((pid, "no value history"))
                continue
        except Exception as exc:
            missing.append((pid, f"error: {exc}"))
            continue
        rows.append({
            "ain": pid.lstrip("0") or pid,  # match the assignment key style
            "parcel_id": pid,
            "situs_address": f"{situs} {city}".strip(),
            "use_description": "",
            "year_built": "",
            **vals,
            "value_source": "solano-county-portal",
            "fetch_date": today,
        })
        if i % 10 == 0:
            print(f"  {i}/{len(parcel_ids)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {OUT}: {len(rows)} parcels")
    for pid, why in missing:
        print(f"  MISSING {pid}: {why}")


if __name__ == "__main__":
    main()
