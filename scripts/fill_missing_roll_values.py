"""Propose roll values for grants missing "Total Roll Value" in the sheet.

For live (non-DEAD) grants with no Total Roll Value:
  - LA County parcels that already have an AIN: fetch roll values directly
    from the LA County parcel API.
  - LA County parcels with only an address: resolve via the geocoder/tract
    finder (src.find_parcels); auto-select when exactly one apartment parcel
    matches the street number, otherwise list candidates for review.
  - Other counties: listed as manual-research items.

Nothing is written back to the sheet or its imported CSVs — output is a
proposed-edits file for manual entry into the master Google Sheet:

    output/sheet_validation/roll_value_edits.csv    (one row per parcel found)
    output/sheet_validation/roll_value_manual.csv   (needs human research)

Usage:
    python scripts/fill_missing_roll_values.py
"""

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.find_parcels.finder import find_parcels  # noqa: E402

API_URL = "https://cache.gis.lacounty.gov/cache/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"
FIELDS = [
    "AIN", "APN", "SitusFullAddress", "SitusCity", "SitusZIP", "UseDescription",
    "Roll_Year", "Roll_LandValue", "Roll_ImpValue",
    "Roll_RealEstateExemp", "Roll_HomeOwnersExemp",
    "TaxRateArea", "TaxRateCity",
]
OUT_DIR = Path("output/sheet_validation")


def money(v):
    s = str(v).replace("$", "").replace(",", "").strip()
    if s in ("", "-", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_by_ains(ains: list[str]) -> dict[str, dict]:
    results = {}
    for i in range(0, len(ains), 50):
        batch = ains[i:i + 50]
        params = {
            "where": f"AIN IN ({','.join(repr(a) for a in batch)})",
            "outFields": ",".join(FIELDS),
            "returnGeometry": "false",
            "f": "json",
        }
        with urllib.request.urlopen(f"{API_URL}?{urllib.parse.urlencode(params)}", timeout=30) as resp:
            data = json.loads(resp.read().decode())
        for feat in data.get("features", []):
            attrs = feat["attributes"]
            results[str(attrs.get("AIN", ""))] = attrs
        time.sleep(0.3)
    return results


def street_number(address: str) -> str:
    return address.strip().split(" ")[0] if address.strip() else ""


def main() -> None:
    g = pd.read_csv("input/grants.csv", dtype=str).fillna("")
    p = pd.read_csv("input/sheet_parcels.csv", dtype=str).fillna("")
    p = p[p["project_id"].str.strip() != ""]
    rates = pd.read_csv("input/sheet_tax_rates.csv", dtype=str).fillna("")
    rate_by_key = {
        (k.zfill(5) if k.isdigit() else k.lower()): v
        for k, v in zip(rates["tax_rate_area"].str.strip(), rates["total_rate"])
    }

    live = g[g["DEAD?"].str.lower() != "true"]
    targets = live[live["Total Roll Value"].map(money).isna()
                   | (live["Total Roll Value"].map(money) == 0)]
    print(f"{len(targets)} live grants missing Total Roll Value")

    by_project = dict(tuple(p.groupby("project_id")))
    edits, manual = [], []

    for _, grant in targets.iterrows():
        pid, prop, county = grant["Project ID"], grant["Property Name"], grant["County"]
        parcels = by_project.get(pid)

        if county != "Los Angeles":
            manual.append({
                "project_id": pid, "property": prop, "county": county,
                "address": grant["Address"],
                "reason": "non-LA county: no automated parcel/roll source",
                "known_ains": "; ".join(a for a in (parcels["ain"] if parcels is not None else []) if a.strip()),
            })
            continue

        # sheet stores some AINs dashed (5540-019-012); the API wants 10 digits
        known_ains = [a.strip().replace("-", "")
                      for a in (parcels["ain"] if parcels is not None else []) if a.strip()]

        if known_ains:
            fetched = fetch_by_ains(known_ains)
            for ain in known_ains:
                attrs = fetched.get(ain)
                if not attrs:
                    manual.append({
                        "project_id": pid, "property": prop, "county": county,
                        "address": grant["Address"],
                        "reason": f"AIN {ain} not found in LA parcel DB",
                        "known_ains": ain,
                    })
                    continue
                edits.append(make_edit(pid, prop, attrs, rate_by_key, source="sheet-ain"))
            continue

        # address-only: resolve via geocoder + tract finder
        address = (parcels["input_address"].iloc[0].strip() if parcels is not None
                   and parcels["input_address"].iloc[0].strip() else grant["Address"])
        if not address.strip():
            manual.append({
                "project_id": pid, "property": prop, "county": county, "address": "",
                "reason": "no AIN and no address", "known_ains": "",
            })
            continue

        try:
            result = find_parcels(address)
        except Exception as exc:  # network/geocode failure
            manual.append({
                "project_id": pid, "property": prop, "county": county, "address": address,
                "reason": f"finder error: {exc}", "known_ains": "",
            })
            continue

        want_no = street_number(address)
        apartments = [
            pc for pc in result.parcels
            if "apartment" in pc.use_description.lower()
            and str(pc.situs_house_no) == want_no
        ]
        if len(apartments) == 1:
            pc = apartments[0]
            attrs = {
                "AIN": pc.ain, "APN": pc.apn, "SitusFullAddress": pc.situs_address,
                "SitusCity": pc.situs_city, "SitusZIP": pc.situs_zip,
                "UseDescription": pc.use_description, "Roll_Year": pc.roll_year,
                "Roll_LandValue": pc.roll_land_value, "Roll_ImpValue": pc.roll_imp_value,
                "Roll_RealEstateExemp": pc.real_estate_exemp,
                "TaxRateArea": pc.tax_rate_area, "TaxRateCity": pc.tax_rate_city,
            }
            edits.append(make_edit(pid, prop, attrs, rate_by_key, source="finder-auto"))
        else:
            cands = "; ".join(
                f"{pc.ain} {pc.situs_address} ({pc.use_description})"
                for pc in result.parcels[:8]
            )
            manual.append({
                "project_id": pid, "property": prop, "county": county, "address": address,
                "reason": f"{len(apartments)} apartment matches of {len(result.parcels)} "
                          f"tract parcels; candidates: {cands}",
                "known_ains": "",
            })
        time.sleep(0.4)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    edits_path = OUT_DIR / "roll_value_edits.csv"
    manual_path = OUT_DIR / "roll_value_manual.csv"
    if edits:
        with open(edits_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(edits[0].keys()))
            w.writeheader()
            w.writerows(edits)
    if manual:
        with open(manual_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(manual[0].keys()))
            w.writeheader()
            w.writerows(manual)
    print(f"\n{len(edits)} proposed parcel edits -> {edits_path}")
    print(f"{len(manual)} items needing manual research -> {manual_path}")


def make_edit(pid: str, prop: str, attrs: dict, rate_by_key: dict, source: str) -> dict:
    land = attrs.get("Roll_LandValue") or 0
    imp = attrs.get("Roll_ImpValue") or 0
    tra = str(attrs.get("TaxRateArea", "") or "")
    rate = rate_by_key.get(tra.zfill(5), "")
    return {
        "project_id": pid,
        "property": prop,
        "source": source,
        "ain": attrs.get("AIN", ""),
        "apn": attrs.get("APN", ""),
        "situs_address": attrs.get("SitusFullAddress", ""),
        "situs_city": attrs.get("SitusCity", ""),
        "use_description": attrs.get("UseDescription", ""),
        "roll_year": attrs.get("Roll_Year", ""),
        "roll_land_value": land,
        "roll_imp_value": imp,
        "roll_total_value": (land or 0) + (imp or 0),
        "real_estate_exemp": attrs.get("Roll_RealEstateExemp", ""),
        "tax_rate_area": tra,
        "tax_rate_city": attrs.get("TaxRateCity", ""),
        "total_tax_rate": rate,
        "rate_missing_from_sheet": "" if rate else "TRA not in Tax Rates tab",
    }


if __name__ == "__main__":
    main()
