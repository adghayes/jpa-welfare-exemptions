#!/usr/bin/env python3
"""
Process CMFA properties to find parcels - with auto-accept for clear matches.

Auto-accepts when:
- Filtering to "Five or more apartments" yields exactly 1 parcel
- That parcel's address matches the input street number

Otherwise shows for manual review.
"""

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.find_parcels import find_parcels

INPUT_FILE = Path(__file__).parent.parent / "input" / "CMFA-grants-01-01-2026-midnight.csv"
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "find_parcels"
PROGRESS_FILE = OUTPUT_DIR / "progress.json"


def load_grants(county_filter: str = "Los Angeles") -> list[dict]:
    """Load CMFA grants, filtered by county."""
    grants = []
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('County', '').strip() == county_filter:
                grants.append(row)
    return grants


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {'processed': [], 'skipped': [], 'results': [], 'last_index': 0}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)


def extract_street_number(address: str) -> str:
    """Extract street number from address."""
    match = re.match(r'^(\d+)', address.strip())
    return match.group(1) if match else ''


def find_matching_parcels(result, input_address: str) -> list:
    """
    Filter parcels to "Five or more apartments" and match street number.
    Returns list of matching parcels.
    """
    if result.error or not result.parcels:
        return []

    # Filter to apartments
    apartments = [p for p in result.parcels if 'Five or more' in p.use_description]

    # Extract input street number
    input_num = extract_street_number(input_address)
    if not input_num:
        return apartments  # Can't filter by number, return all apartments

    # Filter to matching street number
    matching = [p for p in apartments if p.situs_address.startswith(input_num + ' ')]

    return matching if matching else apartments


def process_property(grant: dict, index: int, total: int):
    """
    Process a single property. Returns (action, results, message).
    action: 'auto', 'review', 'skip', 'error'
    """
    property_name = grant.get('Property Name', 'Unknown')
    address = grant.get('Address', '')

    if not address:
        return 'skip', [], f"No address for {property_name}"

    result = find_parcels(address)

    if result.error:
        return 'error', [], f"Error: {result.error}"

    # Find matching parcels
    matching = find_matching_parcels(result, address)
    input_num = extract_street_number(address)

    # Check for auto-accept: exactly 1 matching parcel with same street number
    if len(matching) == 1:
        p = matching[0]
        parcel_num = extract_street_number(p.situs_address)
        if parcel_num == input_num:
            return 'auto', matching, f"Auto-match: {p.ain} at {p.situs_address}"

    # Need manual review
    return 'review', matching, f"Found {len(matching)} apartments (of {len(result.parcels)} total)"


def main():
    grants = load_grants("Los Angeles")
    progress = load_progress()

    print(f"Processing {len(grants)} LA County properties")
    print(f"Already processed: {len(progress['processed'])}, skipped: {len(progress['skipped'])}")
    print("=" * 70)

    auto_count = 0
    review_count = 0

    for i, grant in enumerate(grants):
        property_name = grant.get('Property Name', 'Unknown')
        address = grant.get('Address', '')

        # Skip already processed
        if property_name in progress['processed'] or property_name in progress['skipped']:
            continue

        action, parcels, message = process_property(grant, i, len(grants))

        print(f"\n[{i+1}/{len(grants)}] {property_name}")
        print(f"  Address: {address}")
        print(f"  {message}")

        if action == 'auto':
            # Auto-accept
            p = parcels[0]
            progress['processed'].append(property_name)
            progress['results'].append({
                'ain': p.ain,
                'apn': p.apn,
                'situs_address': p.situs_address,
                'input_address': address,
                'property_name': property_name,
                'tract_number': '',
                'method': 'auto_match',
                'use_description': p.use_description
            })
            print(f"  -> AUTO-ACCEPTED: {p.ain}")
            auto_count += 1
            save_progress(progress)

        elif action == 'review':
            # Need manual review - stop here
            print(f"  -> NEEDS REVIEW")
            print(f"\n  Matching parcels:")
            for p in parcels:
                print(f"    {p.ain} | {p.situs_address} | {p.use_description}")

            review_count += 1

            # Ask what to do
            print(f"\n  Options: [A]ccept all, [1-{len(parcels)}] Accept specific, [S]kip, [Q]uit")
            choice = input("  Choice: ").strip().upper()

            if choice == 'Q':
                break
            elif choice == 'S':
                progress['skipped'].append(property_name)
                save_progress(progress)
            elif choice == 'A':
                for p in parcels:
                    progress['results'].append({
                        'ain': p.ain,
                        'apn': p.apn,
                        'situs_address': p.situs_address,
                        'input_address': address,
                        'property_name': property_name,
                        'tract_number': '',
                        'method': 'manual_accept_all',
                        'use_description': p.use_description
                    })
                progress['processed'].append(property_name)
                save_progress(progress)
                print(f"  -> Accepted {len(parcels)} parcels")
            elif choice.isdigit() and 1 <= int(choice) <= len(parcels):
                p = parcels[int(choice) - 1]
                progress['results'].append({
                    'ain': p.ain,
                    'apn': p.apn,
                    'situs_address': p.situs_address,
                    'input_address': address,
                    'property_name': property_name,
                    'tract_number': '',
                    'method': 'manual_select',
                    'use_description': p.use_description
                })
                progress['processed'].append(property_name)
                save_progress(progress)
                print(f"  -> Accepted: {p.ain}")

        elif action == 'skip' or action == 'error':
            progress['skipped'].append(property_name)
            save_progress(progress)
            print(f"  -> SKIPPED")

    print("\n" + "=" * 70)
    print(f"Session complete: {auto_count} auto-accepted, {review_count} reviewed")
    print(f"Total processed: {len(progress['processed'])}, Total parcels: {len(progress['results'])}")


if __name__ == "__main__":
    main()
