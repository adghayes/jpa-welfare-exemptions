"""Fetch the BOE's Supplemental Clearance Certificate list.

A limited partnership must hold a Supplemental Clearance Certificate (SCC)
from the State Board of Equalization before a county assessor can grant the
welfare exemption on its low-income housing property. The BOE publishes the
full list in its open data portal as an OData endpoint:

    https://www.boe.ca.gov/dataportal/api/odata/Supplemental_Clearance_Certs

Fields: ManagingGeneralPartner, MGPOCCNumber, LimitedPartnership, SCCNumber,
County, IssueDate, FiscalYearFirstQualified.

Output: output/pipeline/scc_certificates.csv (the full list; the merge
matches grants' entities against LimitedPartnership).

Usage:
    python scripts/fetch_scc_certificates.py
"""

import csv
import json
import urllib.request
from datetime import date
from pathlib import Path

BASE = "https://www.boe.ca.gov/dataportal/api/odata/Supplemental_Clearance_Certs"
OUT = Path("output/pipeline/scc_certificates.csv")
PAGE = 5000


def main() -> None:
    rows = []
    skip = 0
    while True:
        url = f"{BASE}?%24format=json&%24top={PAGE}&%24skip={skip}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        batch = data.get("value", [])
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        skip += PAGE

    today = date.today().isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "limited_partnership", "scc_number", "county",
            "managing_general_partner", "mgp_occ_number",
            "issue_date", "fiscal_year_first_qualified", "fetch_date"])
        w.writeheader()
        for r in rows:
            w.writerow({
                "limited_partnership": r.get("LimitedPartnership", ""),
                "scc_number": r.get("SCCNumber", ""),
                "county": r.get("County", ""),
                "managing_general_partner": r.get("ManagingGeneralPartner", ""),
                "mgp_occ_number": r.get("MGPOCCNumber", ""),
                "issue_date": str(r.get("IssueDate", ""))[:10],
                "fiscal_year_first_qualified": r.get("FiscalYearFirstQualified", ""),
                "fetch_date": today,
            })
    print(f"wrote {OUT}: {len(rows)} certificates")


if __name__ == "__main__":
    main()
