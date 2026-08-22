"""Fetch current assessed values for San Diego County parcel assignments.

Source: SANDAG's hosted Parcels feature layer (geo.sandag.org), maintained
from the County Assessor/Recorder (ARCC) parcel base and updated continuously
(asr_land / asr_impr / asr_total). The layer carries NO exemption field, so
real_estate_exemp is left blank here — the merge falls back to manual values
for that field. roll_year is stamped 2026 (current roll; layer last edited
Aug 2026) — see METHODS.md.

Output: output/pipeline/san_diego_roll_values.csv

Usage:
    python scripts/fetch_san_diego_roll_values.py
"""

import csv
import json
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

URL = "https://geo.sandag.org/server/rest/services/Hosted/Parcels/FeatureServer/0/query"
ASSIGNMENTS = Path("manual/parcel_assignments.csv")
OUT = Path("output/pipeline/san_diego_roll_values.csv")


def main() -> None:
    a = pd.read_csv(ASSIGNMENTS, dtype=str).fillna("")
    sd = a[(a["county"] == "San Diego") & (a["ain"].str.strip() != "")]
    ains = sorted(set(sd["ain"]))
    print(f"{len(ains)} San Diego APNs assigned")

    params = {
        "where": "apn IN (" + ",".join(f"'{x}'" for x in ains) + ")",
        "outFields": "apn,situs_address,situs_street,situs_suffix,situs_juris,"
                     "asr_land,asr_impr,asr_total,asr_landuse,year_effective,unitqty",
        "returnGeometry": "false",
        "f": "json",
    }
    req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    if "error" in data:
        raise RuntimeError(data["error"])

    today = date.today().isoformat()
    rows = []
    found = set()
    for feat in data.get("features", []):
        at = feat["attributes"]
        apn = str(at["apn"])
        found.add(apn)
        situs = " ".join(str(at.get(k) or "").strip() for k in
                         ("situs_address", "situs_street", "situs_suffix", "situs_juris")).strip()
        rows.append({
            "ain": apn,
            "parcel_id": apn,
            "situs_address": situs,
            "situs_street": str(at.get("situs_street") or ""),
            "situs_juris": str(at.get("situs_juris") or ""),
            "use_description": str(at.get("asr_landuse") or ""),
            "year_built": str(at.get("year_effective") or ""),
            "roll_year": "2026",
            "roll_land_value": at.get("asr_land") or 0,
            "roll_imp_value": at.get("asr_impr") or 0,
            "roll_total_value": at.get("asr_total") or 0,
            "real_estate_exemp": "",  # not published by this source
            "units": at.get("unitqty") or 0,
            "value_source": "sandag-parcels",
            "fetch_date": today,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT}: {len(rows)} parcels")
    for missing in sorted(set(ains) - found):
        print(f"  MISSING {missing}")


if __name__ == "__main__":
    main()
