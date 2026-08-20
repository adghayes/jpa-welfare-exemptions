#!/usr/bin/env python3
"""
LA County Parcel Lookup Pipeline

Resolves addresses to parcel identifiers (AIN/APN) and fetches tax/exemption data.

Usage:
    python scripts/lookup_parcels.py --input input/addresses.csv --output output/parcel_lookup/
    python scripts/lookup_parcels.py --address "103 S Edgemont St, Los Angeles, CA 90004"
"""

import argparse
import csv
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

import re

from src.parcel_lookup import (
    normalize_address,
    geocode_address,
    resolve_parcels,
    estimate_tax_from_parcel,
    ParcelCache,
)


def extract_street_number(address: str) -> str:
    """Extract the street number from an address string."""
    if not address:
        return ""
    # Match leading digits (with optional letter suffix like "123A")
    match = re.match(r'^(\d+[A-Za-z]?)', address.strip())
    return match.group(1) if match else ""


def filter_parcels_by_address(parcels: list, input_address: str) -> list:
    """
    Filter parcels to only those matching the input street number.

    This prevents false matches from the spatial buffer capturing
    neighboring parcels (e.g., 520 vs 530 on the same street).

    Returns empty list if no matches found - this indicates the geocoded
    coordinates may not align with the correct parcel.
    """
    # Extract street number from input
    parsed = normalize_address(input_address)
    input_number = parsed.street_number

    if not input_number:
        return parcels  # Can't filter without street number

    filtered = []
    for parcel in parcels:
        # Extract street number from situs address
        situs_number = extract_street_number(parcel.situs_address)
        if situs_number == input_number:
            filtered.append(parcel)

    # Return only matching parcels - do NOT fall back to unfiltered
    # An empty result means geocode coordinates didn't align with parcel data
    return filtered


def lookup_single_address(
    address: str,
    cache: ParcelCache,
    use_cache: bool = True,
    rate_limit: float = 0.5
) -> dict:
    """
    Look up a single address and return parcel/tax info.

    Args:
        address: Full address string
        cache: ParcelCache instance
        use_cache: Whether to use cached results
        rate_limit: Seconds between API calls

    Returns:
        Dict with lookup results
    """
    result = {
        "input_address": address,
        "normalized_address": "",
        "geocode_status": "",
        "geocode_score": None,
        "geocode_address": "",
        "lat": None,
        "lon": None,
        "parcel_count": 0,
        "parcels": [],
        "primary_ain": "",
        "primary_apn": "",
        "error": "",
    }

    # Normalize address
    parsed = normalize_address(address)
    result["normalized_address"] = parsed.normalized

    # Check geocode cache
    cached_geo = cache.get_geocode(parsed.normalized) if use_cache else None

    if cached_geo:
        geo_result = cached_geo
        result["geocode_status"] = "cached"
    else:
        # Geocode
        geo_result = geocode_address(address, rate_limit=rate_limit)
        if geo_result and not geo_result.error:
            cache.set_geocode(parsed.normalized, geo_result)
        result["geocode_status"] = "fetched"

    if geo_result.error:
        result["error"] = geo_result.error
        return result

    if not geo_result.best_match:
        result["error"] = "No geocode match"
        return result

    bm = geo_result.best_match
    result["geocode_score"] = bm.score
    result["geocode_address"] = bm.address
    result["lat"] = bm.y
    result["lon"] = bm.x

    # Check parcel cache
    cached_parcels = cache.get_parcels(bm.y, bm.x) if use_cache else None

    if cached_parcels:
        parcel_result = cached_parcels
    else:
        # Resolve parcels
        parcel_result = resolve_parcels(bm.y, bm.x, rate_limit=rate_limit)
        if parcel_result and not parcel_result.error:
            cache.set_parcels(bm.y, bm.x, parcel_result)

    if parcel_result.error:
        result["error"] = parcel_result.error
        return result

    # Filter parcels to only those matching the input address
    # This prevents false matches from the spatial buffer
    filtered_parcels = filter_parcels_by_address(parcel_result.parcels, address)

    # If no matches found with 10m buffer, try larger buffer (25m)
    # This handles cases where geocode coordinates are slightly offset
    if not filtered_parcels and parcel_result.parcels:
        wider_result = resolve_parcels(bm.y, bm.x, buffer_meters=25, rate_limit=rate_limit)
        if wider_result and not wider_result.error:
            filtered_parcels = filter_parcels_by_address(wider_result.parcels, address)

    result["parcel_count"] = len(filtered_parcels)

    # Process each parcel
    for parcel in filtered_parcels:
        tax_info = estimate_tax_from_parcel(parcel)

        parcel_data = {
            "ain": parcel.ain,
            "apn": parcel.apn,
            "situs_address": parcel.situs_address,
            "use_description": parcel.use_description,
            "roll_year": parcel.roll_year,
            "assessed_total": (parcel.roll_land_value or 0) + (parcel.roll_imp_value or 0),
            "roll_land_value": parcel.roll_land_value,
            "roll_imp_value": parcel.roll_imp_value,
            "homeowners_exemp": parcel.homeowners_exemp,
            "real_estate_exemp": parcel.real_estate_exemp,
            "total_exemptions": tax_info.total_exemptions,
            "net_taxable_value": tax_info.net_taxable_value,
            "estimated_annual_tax": tax_info.estimated_annual_tax,
            "has_welfare_exemption": tax_info.has_welfare_exemption,
            "tax_rate_area": parcel.tax_rate_area,
        }
        result["parcels"].append(parcel_data)

    # Set primary parcel (first one)
    if result["parcels"]:
        result["primary_ain"] = result["parcels"][0]["ain"]
        result["primary_apn"] = result["parcels"][0]["apn"]

    return result


def process_csv(
    input_path: Path,
    output_dir: Path,
    address_column: str = "cand_address",
    county_column: Optional[str] = "cand_county",
    county_filter: Optional[str] = "los-angeles",
    use_cache: bool = True,
    rate_limit: float = 0.5,
    limit: Optional[int] = None
):
    """
    Process a CSV file of addresses.

    Args:
        input_path: Path to input CSV
        output_dir: Directory for output files
        address_column: Column containing addresses
        county_column: Column for county filtering (optional)
        county_filter: County value to filter on (optional)
        use_cache: Whether to use cached results
        rate_limit: Seconds between API calls
        limit: Max rows to process (for testing)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read input
    df = pd.read_csv(input_path)
    print(f"Loaded {len(df)} rows from {input_path.name}")

    # Filter by county if specified
    if county_column and county_filter and county_column in df.columns:
        original_count = len(df)
        df = df[df[county_column].str.lower().str.replace(" ", "-") == county_filter.lower()]
        print(f"Filtered to {len(df)} rows where {county_column} = {county_filter}")

    # Limit rows if specified
    if limit:
        df = df.head(limit)
        print(f"Limited to {len(df)} rows for testing")

    # Initialize cache
    cache = ParcelCache()
    print(f"Cache: {cache.db_path}")
    print(f"Cache stats: {cache.stats()}")

    # Process each address
    results = []
    parcel_details = []
    errors = []

    total = len(df)
    for idx, row in df.iterrows():
        address = row.get(address_column, "")
        if not address or pd.isna(address):
            continue

        row_id = idx + 1
        print(f"[{row_id}/{total}] {address[:60]}...", end=" ", flush=True)

        try:
            result = lookup_single_address(
                address,
                cache=cache,
                use_cache=use_cache,
                rate_limit=rate_limit
            )
            result["row_id"] = row_id

            # Add original row data
            for col in df.columns:
                if col not in result:
                    result[f"orig_{col}"] = row[col]

            results.append(result)

            # Expand parcels to separate rows
            for parcel in result.get("parcels", []):
                parcel_row = {
                    "row_id": row_id,
                    "input_address": result["input_address"],
                    **parcel
                }
                parcel_details.append(parcel_row)

            # Track errors
            if result.get("error"):
                errors.append({
                    "row_id": row_id,
                    "address": address,
                    "error": result["error"]
                })
                print(f"ERROR: {result['error']}")
            else:
                parcels = result.get("parcel_count", 0)
                exemp = any(p.get("has_welfare_exemption") for p in result.get("parcels", []))
                status = f"{parcels} parcel(s)"
                if exemp:
                    status += " [EXEMPTION]"
                print(status)

        except Exception as e:
            print(f"EXCEPTION: {e}")
            errors.append({
                "row_id": row_id,
                "address": address,
                "error": str(e)
            })

    # Write outputs
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Main results (one row per input address)
    results_df = pd.DataFrame(results)
    # Remove nested parcels column for CSV
    if "parcels" in results_df.columns:
        results_df = results_df.drop(columns=["parcels"])
    results_path = output_dir / f"parcel_results_{timestamp}.csv"
    results_df.to_csv(results_path, index=False)
    print(f"\nWrote {len(results_df)} results to {results_path}")

    # Parcel details (one row per parcel)
    if parcel_details:
        parcels_df = pd.DataFrame(parcel_details)
        parcels_path = output_dir / f"parcel_details_{timestamp}.csv"
        parcels_df.to_csv(parcels_path, index=False)
        print(f"Wrote {len(parcels_df)} parcel details to {parcels_path}")

    # Errors
    if errors:
        errors_df = pd.DataFrame(errors)
        errors_path = output_dir / f"parcel_errors_{timestamp}.csv"
        errors_df.to_csv(errors_path, index=False)
        print(f"Wrote {len(errors_df)} errors to {errors_path}")

    # Summary stats
    print(f"\nSummary:")
    print(f"  Total addresses processed: {len(results)}")
    print(f"  Total parcels found: {len(parcel_details)}")
    print(f"  Addresses with errors: {len(errors)}")

    exemption_parcels = [p for p in parcel_details if p.get("has_welfare_exemption")]
    print(f"  Parcels with welfare exemption: {len(exemption_parcels)}")

    print(f"\nCache stats after run: {cache.stats()}")


def main():
    parser = argparse.ArgumentParser(
        description="Look up LA County parcel IDs and tax info for addresses"
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        help="Input CSV file with addresses"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("output/parcel_lookup"),
        help="Output directory (default: output/parcel_lookup/)"
    )
    parser.add_argument(
        "--address", "-a",
        type=str,
        help="Single address to look up"
    )
    parser.add_argument(
        "--address-column",
        type=str,
        default="cand_address",
        help="CSV column containing addresses"
    )
    parser.add_argument(
        "--county-column",
        type=str,
        default="cand_county",
        help="CSV column for county filtering"
    )
    parser.add_argument(
        "--county-filter",
        type=str,
        default="los-angeles",
        help="County value to filter (default: los-angeles)"
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache, fetch fresh data"
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=0.5,
        help="Seconds between API requests (default: 0.5)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Max rows to process (for testing)"
    )

    args = parser.parse_args()

    if args.address:
        # Single address lookup
        cache = ParcelCache()
        result = lookup_single_address(
            args.address,
            cache=cache,
            use_cache=not args.no_cache,
            rate_limit=args.rate_limit
        )

        print(f"\nAddress: {result['input_address']}")
        print(f"Normalized: {result['normalized_address']}")
        print(f"Geocode: {result['geocode_address']} (score: {result['geocode_score']})")
        print(f"Coords: ({result['lat']:.6f}, {result['lon']:.6f})" if result['lat'] else "No coords")
        print(f"Parcels found: {result['parcel_count']}")

        for i, p in enumerate(result.get("parcels", []), 1):
            print(f"\n  Parcel {i}:")
            print(f"    AIN: {p['ain']} | APN: {p['apn']}")
            print(f"    Address: {p['situs_address']}")
            print(f"    Use: {p['use_description']}")
            print(f"    Assessed: ${p['assessed_total']:,.0f}")
            print(f"    Exemptions: ${p['total_exemptions']:,.0f}" if p['total_exemptions'] else "    No exemptions")
            print(f"    Est. Tax: ${p['estimated_annual_tax']:,.0f}" if p['estimated_annual_tax'] else "")
            if p['has_welfare_exemption']:
                print(f"    *** WELFARE EXEMPTION: ${p['real_estate_exemp']:,.0f} ***")

        if result.get("error"):
            print(f"\nError: {result['error']}")

    elif args.input:
        # CSV batch processing
        if not args.input.exists():
            print(f"Error: Input file not found: {args.input}")
            sys.exit(1)

        process_csv(
            input_path=args.input,
            output_dir=args.output,
            address_column=args.address_column,
            county_column=args.county_column,
            county_filter=args.county_filter,
            use_cache=not args.no_cache,
            rate_limit=args.rate_limit,
            limit=args.limit
        )

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
