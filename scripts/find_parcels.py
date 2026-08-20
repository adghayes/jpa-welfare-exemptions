#!/usr/bin/env python3
"""
Find all parcels associated with an address in LA County.

Usage:
    python scripts/find_parcels.py "5601 NORTH PARAMOUNT BOULEVARD, Long Beach, CA"
    python scripts/find_parcels.py "123 Main St, Los Angeles, CA" --output results.csv
"""

import argparse
import csv
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.find_parcels import find_parcels


def main():
    parser = argparse.ArgumentParser(
        description="Find all parcels associated with an LA County address"
    )
    parser.add_argument(
        "address",
        help="Address to search (e.g., '5601 N PARAMOUNT BLVD, Long Beach, CA')"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output CSV file path (optional)"
    )
    parser.add_argument(
        "-b", "--buffer",
        type=float,
        default=25.0,
        help="Search buffer in meters (default: 25)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Quiet mode - only output CSV, no progress info"
    )
    parser.add_argument(
        "--no-expand",
        action="store_true",
        help="Don't expand to all parcels with same AIN prefix (for single properties)"
    )

    args = parser.parse_args()

    if not args.quiet:
        print(f"Searching for parcels at: {args.address}")
        print("-" * 60)

    result = find_parcels(
        args.address,
        buffer_meters=args.buffer,
        expand_prefix=not args.no_expand
    )

    if result.error:
        print(f"ERROR: {result.error}", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print(f"Geocoded: {result.geocoded_address} (score: {result.geocode_score})")
        print(f"Coordinates: ({result.lat:.6f}, {result.lon:.6f})")
        if result.tract_number:
            print(f"Tract: {result.tract_number}")
        if result.ain_prefix:
            print(f"AIN prefix: {result.ain_prefix} (fallback)")
        print(f"Found {len(result.parcels)} parcels")
        print()

    # Output to CSV if requested
    if args.output:
        output_path = Path(args.output)
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'ain', 'apn', 'situs_address', 'situs_city', 'situs_zip',
                'use_description', 'use_type', 'roll_year',
                'roll_land_value', 'roll_imp_value',
                'homeowners_exemp', 'real_estate_exemp'
            ])
            for p in result.parcels:
                writer.writerow([
                    p.ain, p.apn, p.situs_address, p.situs_city, p.situs_zip,
                    p.use_description, p.use_type, p.roll_year,
                    p.roll_land_value, p.roll_imp_value,
                    p.homeowners_exemp, p.real_estate_exemp
                ])
        if not args.quiet:
            print(f"Wrote {len(result.parcels)} parcels to {output_path}")
    else:
        # Print to console
        for p in result.parcels:
            print(f"{p.ain} | {p.situs_address} | {p.use_description}")


if __name__ == "__main__":
    main()
