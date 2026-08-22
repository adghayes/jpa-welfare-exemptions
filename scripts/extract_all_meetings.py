#!/usr/bin/env python3
"""
CMFA Full Dataset Extraction

Extracts grant data from all meetings with deduplication.

Deduplication rules:
1. Prefer authorize over preliminary - When same property appears as both, keep only authorize
2. Allow preliminary from latest meeting - These haven't had a chance to be authorized yet
3. Match by normalized property_name + entity

Output:
- output/cmfa_scraping/all_grants_extracted.csv - Full deduplicated dataset
- output/cmfa_scraping/all_grants_validation.csv - Comparison to manual CSV

Usage:
    python scripts/extract_all_meetings.py
    python scripts/extract_all_meetings.py --csv input/grants.csv
"""

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.validate_meeting import (
    ExtractedGrant,
    MEETINGS_DIR,
    load_meeting_documents,
    parse_all_sources,
    normalize_property_name,
    normalize_entity,
    names_match,
)


OUTPUT_DIR = Path("output/cmfa_scraping")
DEFAULT_CSV = Path("input/grants.csv")


def get_all_meeting_dates(start_date: str = "2023-07-01") -> list[str]:
    """Get all meeting dates from the meetings directory, sorted chronologically.

    Args:
        start_date: Skip meetings before this date (YYYY-MM-DD). Default is 2023-07-01
                    to include early meetings like 2023-07-14.
    """
    meetings = []
    for path in MEETINGS_DIR.iterdir():
        if path.is_dir() and path.name[0].isdigit():
            try:
                datetime.strptime(path.name, "%Y-%m-%d")
                if path.name >= start_date:
                    meetings.append(path.name)
            except ValueError:
                continue
    return sorted(meetings)


def deduplicate_grants(all_grants: list[ExtractedGrant]) -> list[ExtractedGrant]:
    """
    Deduplicate grants, preferring authorize over preliminary.

    Rules:
    1. Group by normalized property_name (with typo corrections)
    2. If group has authorize → keep authorize only (latest one)
    3. If group has only preliminary → keep it (mark as preliminary_only)
    """
    if not all_grants:
        return []

    # Hardcoded typo corrections (same as names_match)
    TYPO_ALIASES = {
        'kinglsey': 'kingsley',
    }

    # Canonical name aliases (same as names_match)
    CANONICAL_ALIASES = {
        '2330 3rd': '2330 e 3rd',
        'bella vista': 'bella vista at hilltop',
    }

    def get_canonical_key(name: str) -> str:
        """Get canonical key for grouping, applying typo and alias corrections."""
        norm = normalize_property_name(name)
        for typo, correct in TYPO_ALIASES.items():
            norm = norm.replace(typo, correct)
        if norm in CANONICAL_ALIASES:
            norm = CANONICAL_ALIASES[norm]
        return norm

    # Group by canonical normalized property name
    groups = defaultdict(list)
    for grant in all_grants:
        key = get_canonical_key(grant.property_name)
        groups[key].append(grant)

    result = []
    stats = {"authorize_kept": 0, "preliminary_only": 0}

    for key, grants in groups.items():
        authorize = [g for g in grants if g.item_type == "authorize"]
        preliminary = [g for g in grants if g.item_type == "preliminary"]

        if authorize:
            # Keep latest authorize
            best = max(authorize, key=lambda g: g.meeting_date)
            result.append(best)
            stats["authorize_kept"] += 1
        elif preliminary:
            # Keep latest preliminary - mark as preliminary_only
            latest_prelim = max(preliminary, key=lambda g: g.meeting_date)
            # Mark as preliminary_only (never authorized)
            latest_prelim.item_type = "preliminary_only"
            result.append(latest_prelim)
            stats["preliminary_only"] += 1

    print(f"  Deduplication: {stats['authorize_kept']} authorize, "
          f"{stats['preliminary_only']} preliminary_only (never authorized)")

    return result


def extract_all_meetings(verbose: bool = True) -> list[ExtractedGrant]:
    """Extract grants from all meetings."""
    meeting_dates = get_all_meeting_dates()
    print(f"Found {len(meeting_dates)} meetings")

    all_grants = []
    for meeting_date in meeting_dates:
        docs = load_meeting_documents(meeting_date)
        if not docs:
            continue

        if verbose:
            print(f"  {meeting_date}: ", end="")

        try:
            grants = parse_all_sources(docs, meeting_date)
            all_grants.extend(grants)
            if verbose:
                auth = len([g for g in grants if g.item_type == "authorize"])
                prelim = len([g for g in grants if g.item_type == "preliminary"])
                print(f"{auth} authorize, {prelim} preliminary")
        except Exception as e:
            if verbose:
                print(f"ERROR: {e}")

    print(f"\nTotal raw grants: {len(all_grants)}")
    return all_grants


def export_extracted_csv(grants: list[ExtractedGrant], output_path: Path, csv_df: pd.DataFrame = None):
    """Export extracted grants to CSV with in_csv indicator."""
    if not grants:
        print(f"  No grants to export")
        return

    # Build set of CSV property names for matching
    csv_names = []
    if csv_df is not None and not csv_df.empty:
        csv_names = csv_df['Property Name'].tolist()

    columns = [
        'property_name', 'entity', 'city', 'county', 'resolution', 'meeting_date',
        'item_type', 'minutes_confirmed', 'investor_1', 'nonprofit_partner',
        'total_units', 'rent_restricted_pct', 'term_years', 'city_cut', 'grant_description',
        'in_csv'
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for grant in grants:
            row = asdict(grant)
            # Check if this grant matches any CSV entry
            in_csv = any(names_match(grant.property_name, csv_name) for csv_name in csv_names) if csv_names else False
            row['in_csv'] = 'Yes' if in_csv else 'No'
            writer.writerow(row)

    print(f"  Exported {len(grants)} grants to {output_path}")


def load_all_csv_entries(csv_path: Path) -> pd.DataFrame:
    """Load all CMFA entries from CSV."""
    df = pd.read_csv(csv_path)
    df_cmfa = df[df['Agency'] == 'CMFA']
    return df_cmfa


def export_validation_report(grants: list[ExtractedGrant], csv_df: pd.DataFrame, output_path: Path):
    """Export validation report comparing extracted data to CSV."""
    if not grants or csv_df.empty:
        print(f"  No data to compare")
        return

    # Build CSV lookup by normalized property name
    csv_lookup = {}
    for _, row in csv_df.iterrows():
        norm = normalize_property_name(row.get('Property Name', ''))
        csv_lookup[norm] = row

    discrepancies = []

    for grant in grants:
        if grant.item_type not in ('authorize', 'preliminary_only'):
            continue

        norm = normalize_property_name(grant.property_name)
        csv_row = csv_lookup.get(norm)

        # Try fuzzy match using names_match
        if csv_row is None:
            for csv_norm, row in csv_lookup.items():
                if names_match(grant.property_name, row.get('Property Name', '')):
                    csv_row = row
                    break

        if csv_row is None:
            discrepancies.append({
                'property_name': grant.property_name,
                'field': 'ENTIRE_RECORD',
                'extracted_value': 'EXISTS',
                'csv_value': '',
                'status': 'NOT_IN_CSV'
            })
            continue

        # Compare fields
        field_mappings = [
            ('city', 'City'),
            ('county', 'County'),
            ('resolution', 'Resolution'),
            ('entity', 'Applicant / Entity'),
            ('investor_1', 'Investor 1'),
            ('nonprofit_partner', 'Nonprofit Partner'),
            ('total_units', 'Total Unit Count'),
        ]

        for extracted_field, csv_field in field_mappings:
            extracted_val = getattr(grant, extracted_field, '')
            csv_val = csv_row.get(csv_field, '')

            if extracted_val is None:
                extracted_val = ''
            if pd.isna(csv_val):
                csv_val = ''

            extracted_str = str(extracted_val).strip().lower().replace('\n', ' ')
            csv_str = str(csv_val).strip().lower()

            # For entity field, normalize before comparison
            if extracted_field == 'entity':
                extracted_str = normalize_entity(extracted_str)
                csv_str = normalize_entity(csv_str)

            if not extracted_str and not csv_str:
                continue

            if extracted_str and not csv_str:
                discrepancies.append({
                    'property_name': grant.property_name,
                    'field': extracted_field,
                    'extracted_value': str(extracted_val).strip(),
                    'csv_value': '',
                    'status': 'MISSING_IN_CSV'
                })
            elif extracted_str != csv_str:
                discrepancies.append({
                    'property_name': grant.property_name,
                    'field': extracted_field,
                    'extracted_value': str(extracted_val).strip(),
                    'csv_value': str(csv_val).strip(),
                    'status': 'MISMATCH'
                })

    # Check for CSV entries not in extracted data
    extracted_names = [g.property_name for g in grants if g.item_type in ('authorize', 'preliminary_only')]
    for norm, row in csv_lookup.items():
        csv_name = row.get('Property Name', '')
        # Check if this CSV entry matches any extracted grant using fuzzy matching
        found = any(names_match(csv_name, ext_name) for ext_name in extracted_names)
        if not found:
                discrepancies.append({
                    'property_name': row.get('Property Name', ''),
                    'field': 'ENTIRE_RECORD',
                    'extracted_value': '',
                    'csv_value': 'EXISTS',
                    'status': 'NOT_IN_EXTRACTED'
                })

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['property_name', 'field', 'extracted_value', 'csv_value', 'status'])
        writer.writeheader()
        writer.writerows(discrepancies)

    print(f"  Exported {len(discrepancies)} discrepancies to {output_path}")

    # Export separate files for NOT_IN_CSV and NOT_IN_EXTRACTED
    not_in_csv = [d for d in discrepancies if d['status'] == 'NOT_IN_CSV']
    not_in_extracted = [d for d in discrepancies if d['status'] == 'NOT_IN_EXTRACTED']

    if not_in_csv:
        not_in_csv_path = output_path.parent / "extracted_not_in_csv.csv"
        # Build lookup from property name to grant
        grant_lookup = {g.property_name: g for g in grants}
        columns = [
            'property_name', 'entity', 'city', 'county', 'resolution', 'meeting_date',
            'item_type', 'minutes_confirmed', 'investor_1', 'nonprofit_partner',
            'total_units', 'rent_restricted_pct', 'term_years', 'city_cut', 'grant_description'
        ]
        with open(not_in_csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for d in not_in_csv:
                grant = grant_lookup.get(d['property_name'])
                if grant:
                    row = asdict(grant)
                    writer.writerow({k: row.get(k, '') for k in columns})
        print(f"  Exported {len(not_in_csv)} properties extracted but not in CSV to {not_in_csv_path}")

    if not_in_extracted:
        not_in_extracted_path = output_path.parent / "csv_not_extracted.csv"
        with open(not_in_extracted_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['property_name'])
            writer.writeheader()
            for d in not_in_extracted:
                writer.writerow({'property_name': d['property_name']})
        print(f"  Exported {len(not_in_extracted)} CSV properties not extracted to {not_in_extracted_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract and validate CMFA grant data from all meetings"
    )
    parser.add_argument(
        '--csv',
        type=Path,
        default=DEFAULT_CSV,
        help=f"Path to grants CSV for validation (default: {DEFAULT_CSV})"
    )
    parser.add_argument(
        '--no-validation',
        action='store_true',
        help="Skip validation against CSV"
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help="Reduce output verbosity"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("CMFA Full Dataset Extraction")
    print("=" * 60)

    # Extract from all meetings
    print("\n--- Extracting from all meetings ---")
    all_grants = extract_all_meetings(verbose=not args.quiet)

    # Deduplicate
    print("\n--- Deduplicating ---")
    deduped_grants = deduplicate_grants(all_grants)
    print(f"  Final count: {len(deduped_grants)} grants")

    # Sort by meeting date
    deduped_grants.sort(key=lambda g: g.meeting_date)

    # Load CSV for in_csv field and validation
    df_csv = None
    if args.csv.exists():
        df_csv = load_all_csv_entries(args.csv)

    # Export
    print("\n--- Exporting ---")
    extracted_path = OUTPUT_DIR / "all_grants_extracted.csv"
    export_extracted_csv(deduped_grants, extracted_path, df_csv)

    # Validation
    if not args.no_validation and df_csv is not None:
        print("\n--- Validating against CSV ---")
        print(f"  CSV has {len(df_csv)} CMFA entries")

        validation_path = OUTPUT_DIR / "all_grants_validation.csv"
        export_validation_report(deduped_grants, df_csv, validation_path)

    # Summary
    auth_count = len([g for g in deduped_grants if g.item_type == 'authorize'])
    prelim_count = len([g for g in deduped_grants if g.item_type == 'preliminary'])

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total grants: {len(deduped_grants)}")
    print(f"  Authorized:   {auth_count}")
    print(f"  Preliminary:  {prelim_count} (from latest meeting only)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
