#!/usr/bin/env python3
"""
Interactive script to find parcels for CMFA grant properties.

Processes LA County properties one at a time, allowing user review and decisions.
Saves progress for resume capability.
"""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.find_parcels import find_parcels

# Paths
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
    """Load progress from JSON file."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {
        'processed': [],  # List of processed property names
        'skipped': [],    # List of skipped property names
        'results': [],    # List of result dicts
        'last_index': 0
    }


def save_progress(progress: dict):
    """Save progress to JSON file."""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)


def save_results_csv(results: list[dict]):
    """Save results to CSV file."""
    if not results:
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = OUTPUT_DIR / f"cmfa_parcels_{timestamp}.csv"

    fieldnames = ['ain', 'apn', 'situs_address', 'input_address', 'property_name',
                  'tract_number', 'method', 'use_description']

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nResults saved to: {output_file}")
    return output_file


def display_result(result, property_name: str, address: str, index: int, total: int):
    """Display find_parcels result for user review."""
    print("\n" + "=" * 70)
    print(f"=== Property {index + 1}/{total} ===")
    print(f"Name: {property_name}")
    print(f"Address: {address}")
    print("-" * 70)

    if result.error:
        print(f"ERROR: {result.error}")
        return

    print(f"Geocoded: {result.geocoded_address} (score: {result.geocode_score})")
    print(f"Coordinates: ({result.lat:.6f}, {result.lon:.6f})")

    if result.tract_number:
        print(f"Method: Tract {result.tract_number}")
    elif result.ain_prefix:
        print(f"Method: AIN Prefix {result.ain_prefix} (fallback - may over-include!)")

    print(f"Found: {len(result.parcels)} parcels")

    if result.parcels:
        print("\nPreview (first 5):")
        for p in result.parcels[:5]:
            print(f"  {p.ain} | {p.situs_address} | {p.use_description}")
        if len(result.parcels) > 5:
            print(f"  ... and {len(result.parcels) - 5} more")


def get_user_choice() -> str:
    """Get user's choice for current property."""
    print("\n" + "-" * 70)
    print("[A]ccept parcels, [S]kip, [V]iew all, [R]etry (no expand), [Q]uit")

    while True:
        choice = input("Choice: ").strip().upper()
        if choice in ['A', 'S', 'V', 'R', 'Q']:
            return choice
        print("Invalid choice. Enter A, S, V, R, or Q.")


def process_property(grant: dict, index: int, total: int, progress: dict) -> tuple[str, list[dict]]:
    """
    Process a single property. Returns (action, results).

    action: 'accept', 'skip', 'quit'
    results: list of parcel dicts if accepted, else empty
    """
    property_name = grant.get('Property Name', 'Unknown')
    address = grant.get('Address', '')

    if not address:
        print(f"\nSkipping {property_name} - no address")
        return 'skip', []

    # Check if already processed
    if property_name in progress['processed'] or property_name in progress['skipped']:
        print(f"\nSkipping {property_name} - already processed")
        return 'skip', []

    # Find parcels
    result = find_parcels(address)
    display_result(result, property_name, address, index, total)

    if result.error:
        choice = input("\n[S]kip or [Q]uit? ").strip().upper()
        if choice == 'Q':
            return 'quit', []
        return 'skip', []

    while True:
        choice = get_user_choice()

        if choice == 'A':
            # Accept - convert parcels to result dicts
            results = []
            method = f"tract:{result.tract_number}" if result.tract_number else f"ain_prefix:{result.ain_prefix}"
            for p in result.parcels:
                results.append({
                    'ain': p.ain,
                    'apn': p.apn,
                    'situs_address': p.situs_address,
                    'input_address': address,
                    'property_name': property_name,
                    'tract_number': result.tract_number or '',
                    'method': method,
                    'use_description': p.use_description
                })
            return 'accept', results

        elif choice == 'S':
            return 'skip', []

        elif choice == 'V':
            # View all parcels
            print("\nAll parcels:")
            for p in result.parcels:
                print(f"  {p.ain} | {p.situs_address} | {p.use_description}")
            continue

        elif choice == 'R':
            # Retry without expansion (just parcels at location)
            result = find_parcels(address, expand_prefix=False)
            display_result(result, property_name, address, index, total)
            print("(Showing only parcels at geocoded location, no tract expansion)")
            continue

        elif choice == 'Q':
            return 'quit', []


def main():
    print("=" * 70)
    print("CMFA Parcel Finder - Interactive Mode")
    print("=" * 70)

    # Load grants
    grants = load_grants("Los Angeles")
    print(f"\nLoaded {len(grants)} LA County properties from CMFA grants file")

    # Load progress
    progress = load_progress()
    processed_count = len(progress['processed'])
    skipped_count = len(progress['skipped'])

    if processed_count > 0 or skipped_count > 0:
        print(f"Resuming: {processed_count} accepted, {skipped_count} skipped")
        resume = input("Continue from where you left off? [Y/n]: ").strip().lower()
        if resume == 'n':
            progress = {'processed': [], 'skipped': [], 'results': [], 'last_index': 0}

    total = len(grants)

    try:
        for i, grant in enumerate(grants):
            property_name = grant.get('Property Name', 'Unknown')

            # Skip already processed
            if property_name in progress['processed'] or property_name in progress['skipped']:
                continue

            action, results = process_property(grant, i, total, progress)

            if action == 'accept':
                progress['processed'].append(property_name)
                progress['results'].extend(results)
                print(f"\n>>> Accepted {len(results)} parcels for {property_name}")
            elif action == 'skip':
                progress['skipped'].append(property_name)
                print(f"\n>>> Skipped {property_name}")
            elif action == 'quit':
                print("\nQuitting...")
                break

            # Save progress after each decision
            progress['last_index'] = i
            save_progress(progress)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")

    # Summary
    print("\n" + "=" * 70)
    print("Session Summary")
    print("=" * 70)
    print(f"Properties accepted: {len(progress['processed'])}")
    print(f"Properties skipped: {len(progress['skipped'])}")
    print(f"Total parcels found: {len(progress['results'])}")

    if progress['results']:
        save_csv = input("\nSave results to CSV? [Y/n]: ").strip().lower()
        if save_csv != 'n':
            save_results_csv(progress['results'])


if __name__ == "__main__":
    main()
