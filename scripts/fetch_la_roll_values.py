"""Fetch current assessment-roll values for all LA County parcel assignments.

Reads manual/parcel_assignments.csv (county == Los Angeles, AIN present),
batch-queries the public LA County parcel MapServer, and writes
output/pipeline/la_roll_values.csv — one row per AIN with roll year, land and
improvement values, the real-estate (welfare) exemption, situs, and use
description. value_source is always la-county-api; a fetch_date column
records when.

Usage:
    python scripts/fetch_la_roll_values.py
"""

import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

API_URL = "https://cache.gis.lacounty.gov/cache/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"
FIELDS = [
    "AIN", "APN", "SitusFullAddress", "SitusCity", "SitusZIP", "UseDescription",
    "YearBuilt1", "Roll_Year", "Roll_LandValue", "Roll_ImpValue",
    "Roll_RealEstateExemp", "Roll_HomeOwnersExemp", "TaxRateArea",
]
ASSIGNMENTS = Path("manual/parcel_assignments.csv")
OUT = Path("output/pipeline/la_roll_values.csv")


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
        with urllib.request.urlopen(f"{API_URL}?{urllib.parse.urlencode(params)}",
                                    timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if "error" in data:
            raise RuntimeError(data["error"])
        for feat in data.get("features", []):
            attrs = feat["attributes"]
            results[str(attrs.get("AIN", ""))] = attrs
        print(f"  batch {i//50 + 1}: {len(batch)} AINs -> {len(data.get('features', []))} parcels")
        time.sleep(0.3)
    return results


def main() -> None:
    a = pd.read_csv(ASSIGNMENTS, dtype=str).fillna("")
    la = a[(a["county"] == "Los Angeles") & (a["ain"].str.strip() != "")]
    ains = sorted(set(la["ain"]))
    print(f"{len(ains)} unique LA County AINs assigned")

    fetched = fetch_by_ains(ains)
    today = date.today().isoformat()

    rows = []
    missing = []
    for ain in ains:
        attrs = fetched.get(ain)
        if not attrs or attrs.get("Roll_Year") is None:
            missing.append(ain)
            continue
        land = attrs.get("Roll_LandValue") or 0
        imp = attrs.get("Roll_ImpValue") or 0
        rows.append({
            "ain": ain,
            "apn": attrs.get("APN", ""),
            "situs_address": attrs.get("SitusFullAddress", "") or "",
            "use_description": attrs.get("UseDescription", "") or "",
            "year_built": attrs.get("YearBuilt1", "") or "",
            "roll_year": attrs.get("Roll_Year", ""),
            "roll_land_value": land,
            "roll_imp_value": imp,
            "roll_total_value": land + imp,
            "real_estate_exemp": attrs.get("Roll_RealEstateExemp", 0) or 0,
            "homeowners_exemp": attrs.get("Roll_HomeOwnersExemp", 0) or 0,
            "value_source": "la-county-api",
            "fetch_date": today,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {OUT}: {len(rows)} parcels")
    if missing:
        print(f"{len(missing)} AINs returned no roll data (renumbered/in transition):")
        for m in missing:
            props = la.loc[la['ain'] == m, 'property_name'].unique()
            print(f"  {m}  ({'; '.join(props)})")


if __name__ == "__main__":
    main()
