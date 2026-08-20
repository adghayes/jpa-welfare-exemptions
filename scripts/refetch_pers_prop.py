#!/usr/bin/env python3
"""
Refetch personal property values from LA County API.

This script reads AIns from cmfa_parcels_final.csv and fetches
Roll_PersPropValue and Roll_FixtureValue from the LA County
Parcel API, then merges them into the CSV.

Usage:
    python scripts/refetch_pers_prop.py
"""

import csv
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path


# LA County Parcel API endpoint
API_URL = "https://cache.gis.lacounty.gov/cache/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"

# Fields to fetch
FIELDS = ["AIN", "Roll_PersPropValue", "Roll_FixtureValue"]

# Input/output paths
INPUT_CSV = Path(__file__).parent.parent / "output/find_parcels/cmfa_parcels_final.csv"
OUTPUT_CSV = INPUT_CSV  # Overwrite in place


def fetch_pers_prop_values(ains: list[str], batch_size: int = 50) -> dict[str, dict]:
    """
    Fetch personal property and fixture values for a list of AIns.

    Args:
        ains: List of AIN strings (10-digit)
        batch_size: Number of AIns to query at once

    Returns:
        Dict mapping AIN -> {pers_prop_value, fixture_value}
    """
    results = {}

    # Process in batches
    for i in range(0, len(ains), batch_size):
        batch = ains[i:i + batch_size]

        # Build WHERE clause for batch
        ain_list = ",".join(f"'{ain}'" for ain in batch)
        where_clause = f"AIN IN ({ain_list})"

        params = {
            "where": where_clause,
            "outFields": ",".join(FIELDS),
            "returnGeometry": "false",
            "f": "json"
        }

        url = f"{API_URL}?{urllib.parse.urlencode(params)}"

        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.loads(response.read().decode())

            if "features" in data:
                for feature in data["features"]:
                    attrs = feature.get("attributes", {})
                    ain = attrs.get("AIN", "")
                    if ain:
                        results[ain] = {
                            "pers_prop_value": attrs.get("Roll_PersPropValue"),
                            "fixture_value": attrs.get("Roll_FixtureValue")
                        }

            print(f"  Fetched batch {i//batch_size + 1}: {len(batch)} AIns, {len(data.get('features', []))} results")

        except Exception as e:
            print(f"  Error fetching batch {i//batch_size + 1}: {e}")

        # Rate limiting
        time.sleep(0.2)

    return results


def main():
    print(f"Reading AIns from {INPUT_CSV}")

    # Read existing CSV and extract unique AIns
    rows = []
    ains = set()

    with open(INPUT_CSV, 'r', newline='') as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        for row in reader:
            rows.append(row)
            if row.get("ain"):
                ains.add(row["ain"])

    print(f"Found {len(ains)} unique AIns across {len(rows)} rows")

    # Fetch personal property values
    print("\nFetching personal property values from LA County API...")
    ain_list = sorted(list(ains))
    values = fetch_pers_prop_values(ain_list)
    print(f"\nReceived data for {len(values)} AIns")

    # Add new columns if not present
    new_columns = ["roll_pers_prop_value", "roll_fixture_value"]
    for col in new_columns:
        if col not in fieldnames:
            # Insert after roll_imp_value
            try:
                idx = fieldnames.index("roll_imp_value") + 1
            except ValueError:
                idx = len(fieldnames)
            fieldnames.insert(idx, col)

    # Merge data into rows
    updated_count = 0
    for row in rows:
        ain = row.get("ain", "")
        if ain in values:
            val = values[ain]
            row["roll_pers_prop_value"] = val.get("pers_prop_value") or ""
            row["roll_fixture_value"] = val.get("fixture_value") or ""
            updated_count += 1
        else:
            # Ensure columns exist even if no data
            row.setdefault("roll_pers_prop_value", "")
            row.setdefault("roll_fixture_value", "")

    print(f"Updated {updated_count} rows with personal property data")

    # Write updated CSV
    print(f"\nWriting updated CSV to {OUTPUT_CSV}")
    with open(OUTPUT_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("Done!")

    # Summary of non-zero values
    non_zero_pp = sum(1 for r in rows if r.get("roll_pers_prop_value") and float(r.get("roll_pers_prop_value", 0) or 0) > 0)
    non_zero_fx = sum(1 for r in rows if r.get("roll_fixture_value") and float(r.get("roll_fixture_value", 0) or 0) > 0)
    print(f"\nSummary:")
    print(f"  Rows with non-zero personal property value: {non_zero_pp}")
    print(f"  Rows with non-zero fixture value: {non_zero_fx}")


if __name__ == "__main__":
    main()
