#!/usr/bin/env python3
"""
CMFA Full Dataset Extraction

Extracts grant data from all meetings.

Row grain: one row per authorization event — a property re-granted under a
new resolution at a later meeting gets a row per authorization. Properties
that only ever received preliminary approval get a single preliminary_only
row (their latest appearance).

Output:
- output/pipeline/all_grants_extracted.csv

Usage:
    python scripts/extract_all_meetings.py [--quiet]
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.validate_meeting import (
    ExtractedGrant,
    MEETINGS_DIR,
    load_meeting_documents,
    parse_all_sources,
    normalize_property_name,
)


OUTPUT_DIR = Path("output/pipeline")

# Parsed-meeting cache, keyed by meeting date. PDF parsing dominates the
# pipeline's runtime; a meeting is re-parsed only when one of its documents'
# mtimes changes, and the whole cache self-invalidates when any parser
# source file changes (same scheme as the CSCDA cache in build_basic_list.py).
CACHE_PATH = OUTPUT_DIR / "cmfa_parse_cache.json"
PARSER_SOURCES = [
    Path(__file__).parent / "validate_meeting.py",
    Path(__file__).parent.parent / "src/cmfa_scraping/agenda_parser.py",
    Path(__file__).parent.parent / "src/cmfa_scraping/staff_report_parser.py",
]


def parser_fingerprint(sources: list[Path]) -> str:
    import hashlib
    h = hashlib.sha256()
    for p in sources:
        h.update(p.read_bytes())
    return h.hexdigest()


def _doc_mtimes(docs: dict) -> dict:
    return {k: (p.stat().st_mtime if p else None) for k, p in docs.items()}


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
            # Keep EVERY authorization: a property can be re-granted under a
            # new resolution months later (e.g. Alexandria II, Res 25-487 then
            # 26-005), and each authorization is its own dataset row. Distinct
            # authorizations are distinguished by meeting_date.
            seen_dates = set()
            for g in sorted(authorize, key=lambda g: g.meeting_date):
                if g.meeting_date in seen_dates:
                    continue
                seen_dates.add(g.meeting_date)
                result.append(g)
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
    """Extract grants from all meetings (cached per meeting by doc mtimes)."""
    meeting_dates = get_all_meeting_dates()
    print(f"Found {len(meeting_dates)} meetings")

    fingerprint = parser_fingerprint(PARSER_SOURCES)
    cache = {}
    if CACHE_PATH.exists():
        stored = json.loads(CACHE_PATH.read_text())
        if stored.get("parser_hash") == fingerprint:
            cache = stored.get("meetings", {})
        elif verbose:
            print("  parser code changed - re-parsing all meetings")

    all_grants, dirty, hits = [], False, 0
    for meeting_date in meeting_dates:
        docs = load_meeting_documents(meeting_date)
        if not docs:
            continue

        entry = cache.get(meeting_date)
        mtimes = _doc_mtimes(docs)
        if entry and entry["mtimes"] == mtimes:
            all_grants.extend(ExtractedGrant(**g) for g in entry["grants"])
            hits += 1
            continue

        if verbose:
            print(f"  {meeting_date}: ", end="")

        try:
            grants = parse_all_sources(docs, meeting_date)
            all_grants.extend(grants)
            cache[meeting_date] = {"mtimes": mtimes,
                                   "grants": [asdict(g) for g in grants]}
            dirty = True
            if verbose:
                auth = len([g for g in grants if g.item_type == "authorize"])
                prelim = len([g for g in grants if g.item_type == "preliminary"])
                print(f"{auth} authorize, {prelim} preliminary")
        except Exception as e:
            # always visible, even with --quiet: a silently dropped meeting
            # is a silently wrong dataset
            print(f"  {meeting_date}: ERROR {e}", file=sys.stderr)

    if dirty:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(
            {"parser_hash": fingerprint, "meetings": cache}))
    if hits:
        print(f"  ({hits}/{len(meeting_dates)} meetings from parse cache)")

    print(f"\nTotal raw grants: {len(all_grants)}")
    return all_grants


def export_extracted_csv(grants: list[ExtractedGrant], output_path: Path):
    """Export extracted grants to CSV."""
    if not grants:
        print(f"  No grants to export")
        return

    columns = [
        'property_name', 'entity', 'city', 'county', 'resolution', 'meeting_date',
        'item_type', 'minutes_confirmed', 'minutes_outcome', 'investor_1', 'investor_2', 'nonprofit_partner',
        'total_units', 'restricted_units', 'rent_restricted_pct', 'restricted_pct', 'term_years', 'city_cut', 'grant_description', 'address',
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for grant in grants:
            row = asdict(grant)
            writer.writerow({k: row.get(k, '') for k in columns})

    print(f"  Exported {len(grants)} grants to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract CMFA grant data from all meetings"
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

    # Export
    print("\n--- Exporting ---")
    extracted_path = OUTPUT_DIR / "all_grants_extracted.csv"
    export_extracted_csv(deduped_grants, extracted_path)

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
