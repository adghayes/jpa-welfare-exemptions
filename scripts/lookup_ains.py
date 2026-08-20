#!/usr/bin/env python3
"""
Lookup parcel information by AIN from the LA County GIS layer.

Usage:
    python scripts/lookup_ains.py input/parcel_examples.csv
"""

import csv
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parcel_lookup.parcel_resolver import resolve_parcel_by_ain
from src.parcel_lookup.cache import ParcelCache


def main():
    if len(sys.argv) < 2:
        print("Usage: python lookup_ains.py <input_csv>")
        print("  Input CSV should have one AIN per line (with or without dashes)")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    if not input_file.exists():
        print(f"Error: File not found: {input_file}")
        sys.exit(1)

    # Initialize cache
    cache = ParcelCache()
    print(f"Cache: {cache.db_path}")
    print(f"Cache stats: {cache.stats()}")

    # Read AIns from file
    ains = []
    with open(input_file) as f:
        for line in f:
            ain = line.strip()
            if ain and not ain.startswith("#"):
                ains.append(ain)

    print(f"Loaded {len(ains)} AIns from {input_file}")

    # Output file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = Path("output/parcel_lookup") / f"ain_lookup_{timestamp}.csv"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Process each AIN
    results = []
    found = 0
    with_exemption = 0
    cache_hits = 0

    for i, ain in enumerate(ains, 1):
        print(f"[{i}/{len(ains)}] {ain}...", end=" ")

        # Check cache first
        if cache.is_ain_cached(ain):
            parcel = cache.get_parcel_by_ain(ain)
            cache_hits += 1
            from_cache = True
        else:
            parcel = resolve_parcel_by_ain(ain, rate_limit=0.3)
            cache.set_parcel_by_ain(ain, parcel)  # Cache result (even if None)
            from_cache = False

        if parcel:
            found += 1

            # Calculate totals
            land = parcel.roll_land_value or 0
            imp = parcel.roll_imp_value or 0
            assessed = land + imp

            ho_ex = parcel.homeowners_exemp or 0
            re_ex = parcel.real_estate_exemp or 0
            pp_ex = parcel.pers_prop_exemp or 0
            fix_ex = parcel.fixture_exemp or 0
            total_exemp = ho_ex + re_ex + pp_ex + fix_ex

            net_taxable = max(0, assessed - total_exemp)
            has_welfare = re_ex > 0

            if has_welfare:
                with_exemption += 1
                cache_tag = " (cached)" if from_cache else ""
                print(f"✓ [EXEMPTION: ${re_ex:,.0f}]{cache_tag}")
            else:
                cache_tag = " (cached)" if from_cache else ""
                print(f"✓{cache_tag}")

            results.append({
                "input_ain": ain,
                "ain": parcel.ain,
                "apn": parcel.apn,
                "situs_address": parcel.situs_address,
                "situs_city": parcel.situs_city,
                "situs_zip": parcel.situs_zip,
                "use_description": parcel.use_description,
                "use_type": parcel.use_type,
                "roll_year": parcel.roll_year,
                "roll_land_value": land,
                "roll_imp_value": imp,
                "assessed_total": assessed,
                "homeowners_exemp": ho_ex if ho_ex else "",
                "real_estate_exemp": re_ex if re_ex else "",
                "pers_prop_exemp": pp_ex if pp_ex else "",
                "fixture_exemp": fix_ex if fix_ex else "",
                "total_exemptions": total_exemp if total_exemp else "",
                "net_taxable_value": net_taxable,
                "has_welfare_exemption": has_welfare,
                "tax_rate_area": parcel.tax_rate_area,
                "tax_rate_city": parcel.tax_rate_city,
            })
        else:
            cache_tag = " (cached)" if from_cache else ""
            print(f"✗ NOT FOUND{cache_tag}")
            results.append({
                "input_ain": ain,
                "ain": "",
                "apn": "",
                "situs_address": "NOT FOUND",
                "situs_city": "",
                "situs_zip": "",
                "use_description": "",
                "use_type": "",
                "roll_year": "",
                "roll_land_value": "",
                "roll_imp_value": "",
                "assessed_total": "",
                "homeowners_exemp": "",
                "real_estate_exemp": "",
                "pers_prop_exemp": "",
                "fixture_exemp": "",
                "total_exemptions": "",
                "net_taxable_value": "",
                "has_welfare_exemption": "",
                "tax_rate_area": "",
                "tax_rate_city": "",
            })

    # Write results
    if results:
        fieldnames = list(results[0].keys())
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

        print(f"\nWrote {len(results)} results to {output_file}")

    # Summary
    print(f"\nSummary:")
    print(f"  Total AIns processed: {len(ains)}")
    print(f"  Found: {found}")
    print(f"  Not found: {len(ains) - found}")
    print(f"  With welfare exemption: {with_exemption}")
    print(f"  Cache hits: {cache_hits}")
    print(f"  API calls: {len(ains) - cache_hits}")
    print(f"\nCache stats after run: {cache.stats()}")


if __name__ == "__main__":
    main()
