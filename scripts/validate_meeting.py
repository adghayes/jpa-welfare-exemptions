#!/usr/bin/env python3
"""
CMFA Meeting Validation Tool

Validates grant data for a single meeting using the three-document flow:
1. Agenda - Primary source for grant items with initial metadata
2. Minutes - Confirm grants weren't struck from agenda
3. Staff Report - Extract additional details (units, nonprofit, address, etc.)

Outputs:
- extracted_grants_YYYY-MM-DD.csv - Raw extracted data from PDFs
- validation_report_YYYY-MM-DD.csv - Comparison to manual spreadsheet

Usage:
    python scripts/validate_meeting.py 2025-11-21
    python scripts/validate_meeting.py 2025-11-21 [--csv some_grants.csv]
"""

import argparse
import csv
import re
import sys
from dataclasses import dataclass, asdict, fields
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cmfa_scraping.agenda_parser import parse_agenda_pdf, AgendaGrant
from src.cmfa_scraping.staff_report_parser import parse_staff_report_pdf, StaffReportGrant


MEETINGS_DIR = Path("data/cmfa_scraping/meetings")


@dataclass
class ExtractedGrant:
    """Merged grant data from all document sources."""
    # From agenda/minutes
    property_name: str
    entity: str  # LP name (Applicant / Entity in CSV)
    city: str
    county: str
    resolution: str
    meeting_date: str
    item_type: str  # "authorize" or "preliminary"
    minutes_confirmed: bool = False
    minutes_outcome: str = ""  # "approved" | "pulled" | "continued" | "" (minutes unavailable)

    # From staff report (enrichment)
    investor_1: str = ""  # Staff report "Applicant:" field
    investor_2: str = ""  # second party when the applicant reads "A / B"
    nonprofit_partner: str = ""
    total_units: Optional[int] = None
    restricted_units: Optional[int] = None
    rent_restricted_pct: str = ""
    term_years: Optional[int] = None
    city_cut: Optional[float] = None
    grant_description: str = ""
    address: str = ""


def normalize_property_name(s: str) -> str:
    """Normalize property name for matching."""
    if pd.isna(s) or not s:
        return ""
    s = str(s).lower().strip()
    # Remove newlines
    s = s.replace('\n', ' ').replace('\r', ' ')
    # Remove periods
    s = s.replace('.', '')
    # Normalize abbreviations (plurals first, then singulars)
    s = s.replace('streets', 'st').replace('street', 'st')
    s = s.replace('boulevards', 'blvd').replace('boulevard', 'blvd')
    s = s.replace('avenues', 'ave').replace('avenue', 'ave')
    s = s.replace('drive', 'dr')
    s = s.replace('gardens', 'garden')  # Normalize plural to singular
    # Remove portfolio prefixes (staff reports sometimes have these)
    s = re.sub(r'^[a-z\s]+portfolio:\s*', '', s)
    # Remove common suffixes
    for suffix in ['apartments', 'apartment', 'homes', 'village', 'connections', 'transit', 'the ']:
        s = s.replace(suffix, '')
    s = ' '.join(s.split())
    # Remove trailing street abbreviation after ordinal numbers (e.g., "685 w 4th st" -> "685 w 4th")
    s = re.sub(r'(\d+(?:st|nd|rd|th))\s+(st|ave|blvd)$', r'\1', s)
    return s


def names_match(name1: str, name2: str) -> bool:
    """Check if two property names match.

    Uses exact normalized matching with hardcoded exceptions for known typos.
    """
    norm1 = normalize_property_name(name1)
    norm2 = normalize_property_name(name2)

    # Hardcoded typo corrections (add more as discovered via LLM check)
    TYPO_ALIASES = {
        'kinglsey': 'kingsley',  # Typo in 2024-10-10 agenda
    }

    # Canonical name aliases (exact match only, for same property with different names)
    CANONICAL_ALIASES = {
        '2330 3rd': '2330 e 3rd',  # Missing "E." (East) in some sources
        'bella vista': 'bella vista at hilltop',  # Shortened name in some sources
        '569th w 6th': '569 w 6th',  # Typo in 2025-03-14 agenda ("569th" instead of "569")
        'hawaiian garden senior': 'hawaiian terrace senior',  # Agenda used city name instead of property name
    }

    # Apply typo corrections (substring replace)
    fixed1 = norm1
    fixed2 = norm2
    for typo, correct in TYPO_ALIASES.items():
        fixed1 = fixed1.replace(typo, correct)
        fixed2 = fixed2.replace(typo, correct)

    # Apply canonical aliases (exact match only)
    if fixed1 in CANONICAL_ALIASES:
        fixed1 = CANONICAL_ALIASES[fixed1]
    if fixed2 in CANONICAL_ALIASES:
        fixed2 = CANONICAL_ALIASES[fixed2]

    # Exact match only
    return fixed1 == fixed2


def normalize_entity(s: str) -> str:
    """Normalize entity name for comparison by removing common suffixes."""
    if not s:
        return ""
    s = str(s).lower().strip()
    # Remove trailing comma
    s = s.rstrip(',').strip()
    # Remove common suffixes that don't affect identity
    suffixes_to_remove = [
        ' or an affiliate thereof',
        ', a california limited partnership',
        ', a delaware limited partnership',
        ', a california limited liability company',
        ', a delaware limited liability company',
        ', a limited partnership',
        ', a washington limited partnership',
        ', a colorado nonprofit corporation',
    ]
    for suffix in suffixes_to_remove:
        if s.endswith(suffix):
            s = s[:-len(suffix)].strip()
    return s


def load_meeting_documents(meeting_date: str) -> dict:
    """Load all documents for a meeting date."""
    meeting_dir = MEETINGS_DIR / meeting_date

    if not meeting_dir.exists():
        print(f"Error: Meeting directory not found: {meeting_dir}")
        return {}

    docs = {}
    for doc_type in ['agenda', 'staff_report', 'minutes']:
        pdf_path = meeting_dir / f"{doc_type}.pdf"
        docx_path = meeting_dir / f"{doc_type}.docx"

        if pdf_path.exists():
            docs[doc_type] = pdf_path
        elif docx_path.exists():
            docs[doc_type] = docx_path
        else:
            docs[doc_type] = None

    return docs


def load_csv_entries(csv_path: Path, meeting_date: str) -> pd.DataFrame:
    """Load CSV entries for a specific meeting date (CMFA only)."""
    df = pd.read_csv(csv_path)
    # Sheet dates mix 2- and 4-digit years (07/14/23 vs 9/19/2025)
    df['Date'] = pd.to_datetime(df['Date'], format='mixed', errors='coerce')
    target_date = datetime.strptime(meeting_date, "%Y-%m-%d")
    df_meeting = df[df['Date'].dt.date == target_date.date()]
    df_cmfa = df_meeting[df_meeting['Agency'] == 'CMFA']
    return df_cmfa


def parse_minutes_outcomes(minutes_path: Path) -> dict[str, str]:
    """Map each resolution number in the minutes to its stated outcome.

    Minutes list items as "... (Resolution 25-346) <disposition text> ..."
    where the disposition, up to the next item, reads e.g. "Motion by X.
    Seconded by Y. Motion carries unanimously" or "This item was pulled
    from the Agenda."
    """
    from src.cmfa_scraping.agenda_parser import extract_text_from_pdf

    text = extract_text_from_pdf(minutes_path)
    outcomes: dict[str, str] = {}
    # Minutes dialects: 2023-2025 record a motion after EACH item; some
    # sections use ONE motion per numbered section (block vote); 2026 adopts
    # whole sections on a consent calendar with a single motion naming them:
    # "Consent Items 4, 5, 6, 7, and 8 were approved together. Motion ...
    # carries". Per-item text always wins over section-level approval.
    consent_sections: set[str] = set()
    cm = re.search(r"Consent Items?\s+([0-9,\s&and]+?)\s+were approved together(.{0,250})",
                   text, re.DOTALL | re.IGNORECASE)
    if cm and "carries" in cm.group(2).lower():
        consent_sections = set(re.findall(r"\d+", cm.group(1)))

    sections = re.split(r"\n\s*(?=\d+\.\s)", text)
    for section in sections:
        matches = list(re.finditer(r"\(Resolution\s*(\d+-\d+)\)", section))
        if not matches:
            continue
        section_low = section.lower()
        section_no_m = re.match(r"\s*(\d+)\.", section)
        section_no = section_no_m.group(1) if section_no_m else ""
        section_approved = "carries" in section_low or section_no in consent_sections
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
            seg = section[m.end():end].lower()
            if "pulled" in seg:
                outcome = "pulled"
            elif "continued" in seg:
                outcome = "continued"
            elif "carries" in seg:
                outcome = "approved"
            elif section_approved:
                outcome = "approved"   # block vote covering the section
            else:
                outcome = ""
            res = m.group(1)
            if res not in outcomes or outcomes[res] == "":
                outcomes[res] = outcome
    return outcomes


def parse_all_sources(docs: dict, meeting_date: str) -> list[ExtractedGrant]:
    """
    Parse all document sources and merge into ExtractedGrant objects.

    Flow:
    1. Parse agenda for primary grant list
    2. Check minutes for confirmation
    3. Parse staff report for enrichment
    4. Merge by property name matching
    """
    extracted_grants = []

    # Step 1: Parse agenda
    print(f"\n--- Step 1: Parsing Agenda ---")
    agenda_grants = []
    if docs.get('agenda'):
        try:
            agenda_grants = parse_agenda_pdf(docs['agenda'])
            authorize = [g for g in agenda_grants if g.item_type == 'authorize']
            preliminary = [g for g in agenda_grants if g.item_type == 'preliminary']
            print(f"  Found {len(authorize)} authorization grants, {len(preliminary)} preliminary grants")
        except Exception as e:
            print(f"  Error parsing agenda: {e}")
    else:
        print("  Agenda not available")

    # Step 2: Check minutes for per-item outcomes. The minutes state each
    # item's disposition after its "(Resolution NN-NNN)" marker:
    #   "Motion by X. Seconded by Y. Motion carries..."  -> approved
    #   "This item was pulled from the agenda."          -> pulled
    #   "...continued..."                                -> continued
    # A resolution number merely APPEARING in the minutes is NOT approval.
    print(f"\n--- Step 2: Checking Minutes ---")
    minutes_outcomes: dict[str, str] = {}
    if docs.get('minutes'):
        try:
            minutes_outcomes = parse_minutes_outcomes(docs['minutes'])
            from collections import Counter
            print(f"  Found {len(minutes_outcomes)} resolution outcomes in minutes "
                  f"{dict(Counter(minutes_outcomes.values()))}")
        except Exception as e:
            print(f"  Error parsing minutes: {e}")
    else:
        print("  Minutes not available")

    # Step 3: Parse staff report
    print(f"\n--- Step 3: Parsing Staff Report ---")
    staff_grants = []
    if docs.get('staff_report'):
        try:
            staff_grants = parse_staff_report_pdf(docs['staff_report'])
            print(f"  Found {len(staff_grants)} grants in staff report")
        except Exception as e:
            print(f"  Error parsing staff report: {e}")
    else:
        print("  Staff report not available")

    # Build staff report lookup by normalized property name
    staff_lookup = {}
    for sg in staff_grants:
        norm = normalize_property_name(sg.property_name)
        staff_lookup[norm] = sg

    # Step 4: Merge sources
    print(f"\n--- Step 4: Merging Sources ---")
    for ag in agenda_grants:
        # Check minutes outcome
        outcome = minutes_outcomes.get(ag.resolution, "") if ag.resolution else ""
        confirmed = outcome == "approved"

        # Find matching staff report
        norm_name = normalize_property_name(ag.property_name)
        staff = staff_lookup.get(norm_name)

        # Try fuzzy match if exact match fails
        if not staff:
            for staff_norm, sg in staff_lookup.items():
                if names_match(ag.property_name, sg.property_name):
                    staff = sg
                    break

        # Grant description: the agenda states the amount ("grant up to
        # $10,000 in a Charitable Affordable Housing grant"), which beats the
        # staff report's Purpose header (that regex truncates at line wraps)
        if ag.grant_amount:
            description = f"Grant up to ${ag.grant_amount:,} (Charitable Affordable Housing)"
        else:
            description = staff.grant_description if staff else ""

        # Applicants sometimes read "A / B" — split into the two investors
        applicant = staff.applicant if staff else ""
        investor_1, investor_2 = applicant, ""
        if " / " in applicant:
            parts = [x.strip() for x in applicant.split(" / ")]
            if len(parts) == 2 and all(parts):
                investor_1, investor_2 = parts

        # Create merged grant
        grant = ExtractedGrant(
            property_name=ag.property_name,
            entity=ag.entity,
            city=ag.city.replace('\n', ' '),
            county=ag.county.replace('\n', ' ') if ag.county else "",
            resolution=ag.resolution,
            meeting_date=meeting_date,
            item_type=ag.item_type,
            minutes_confirmed=confirmed,
            minutes_outcome=outcome,
            investor_1=investor_1,
            investor_2=investor_2,
            nonprofit_partner=staff.nonprofit_partner if staff else "",
            total_units=staff.total_units if staff else None,
            restricted_units=staff.restricted_units if staff else None,
            rent_restricted_pct=staff.rent_restricted_pct if staff else "",
            term_years=staff.term_years if staff else None,
            city_cut=staff.city_share if staff else None,
            grant_description=description,
            address=staff.address if staff else "",
        )
        extracted_grants.append(grant)

    authorize_count = len([g for g in extracted_grants if g.item_type == 'authorize'])
    confirmed_count = len([g for g in extracted_grants if g.minutes_confirmed])
    enriched_count = len([g for g in extracted_grants if g.investor_1])
    print(f"  Merged: {len(extracted_grants)} grants ({authorize_count} authorize, {confirmed_count} confirmed, {enriched_count} enriched)")

    return extracted_grants


def export_extracted_csv(grants: list[ExtractedGrant], output_path: Path):
    """Export extracted grants to CSV."""
    if not grants:
        print(f"  No grants to export")
        return

    # Define column order
    columns = [
        'property_name', 'entity', 'city', 'county', 'resolution', 'meeting_date',
        'item_type', 'minutes_confirmed', 'investor_1', 'investor_2', 'nonprofit_partner',
        'total_units', 'restricted_units', 'rent_restricted_pct', 'term_years', 'city_cut', 'grant_description', 'address'
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for grant in grants:
            row = asdict(grant)
            writer.writerow(row)

    print(f"  Exported {len(grants)} grants to {output_path}")


def export_validation_report(grants: list[ExtractedGrant], csv_df: pd.DataFrame, output_path: Path):
    """Export validation report comparing extracted data to CSV."""
    if grants is None or csv_df.empty:
        print(f"  No data to compare")
        return

    # Build CSV lookup
    csv_lookup = {}
    for _, row in csv_df.iterrows():
        norm = normalize_property_name(row.get('Property Name', ''))
        csv_lookup[norm] = row

    discrepancies = []

    for grant in grants:
        if grant.item_type != 'authorize':
            continue

        norm = normalize_property_name(grant.property_name)
        csv_row = csv_lookup.get(norm)

        # Try partial match
        if csv_row is None:
            for csv_norm, row in csv_lookup.items():
                if norm in csv_norm or csv_norm in norm:
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

            # Handle None/NaN
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

            # Skip if both empty
            if not extracted_str and not csv_str:
                continue

            # Check for missing in CSV
            if extracted_str and not csv_str:
                discrepancies.append({
                    'property_name': grant.property_name,
                    'field': extracted_field,
                    'extracted_value': str(extracted_val).strip(),
                    'csv_value': '',
                    'status': 'MISSING_IN_CSV'
                })
            # Check for mismatch
            elif extracted_str != csv_str:
                discrepancies.append({
                    'property_name': grant.property_name,
                    'field': extracted_field,
                    'extracted_value': str(extracted_val).strip(),
                    'csv_value': str(csv_val).strip(),
                    'status': 'MISMATCH'
                })

    # Check for CSV entries not in extracted data
    extracted_norms = {normalize_property_name(g.property_name) for g in grants if g.item_type == 'authorize'}
    for norm, row in csv_lookup.items():
        if norm not in extracted_norms:
            # Check partial match
            found = False
            for en in extracted_norms:
                if norm in en or en in norm:
                    found = True
                    break
            if not found:
                discrepancies.append({
                    'property_name': row.get('Property Name', ''),
                    'field': 'ENTIRE_RECORD',
                    'extracted_value': '',
                    'csv_value': 'EXISTS',
                    'status': 'NOT_IN_EXTRACTED'
                })

    # Write report
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['property_name', 'field', 'extracted_value', 'csv_value', 'status'])
        writer.writeheader()
        writer.writerows(discrepancies)

    print(f"  Exported {len(discrepancies)} discrepancies to {output_path}")


def display_summary(grants: list[ExtractedGrant], csv_df: pd.DataFrame):
    """Display summary of extraction and validation."""
    authorize = [g for g in grants if g.item_type == 'authorize']
    preliminary = [g for g in grants if g.item_type == 'preliminary']
    confirmed = [g for g in grants if g.minutes_confirmed]
    enriched = [g for g in grants if g.investor_1]

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  Authorization grants:  {len(authorize)}")
    print(f"  Preliminary grants:    {len(preliminary)}")
    print(f"  Minutes confirmed:     {len(confirmed)}/{len(authorize)}")
    print(f"  Staff report enriched: {len(enriched)}/{len(grants)}")
    print(f"  CSV entries (CMFA):    {len(csv_df)}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract and validate CMFA grant data for a single meeting"
    )
    parser.add_argument(
        'meeting_date',
        help="Meeting date in YYYY-MM-DD format (e.g., 2025-11-21)"
    )
    parser.add_argument(
        '--csv',
        type=Path,
        default=None,
        help="Optional grants CSV to validate against (skipped if omitted)"
    )
    parser.add_argument(
        '--no-validation',
        action='store_true',
        help="Skip validation against CSV (only export extracted data)"
    )

    args = parser.parse_args()

    # Validate date format
    try:
        datetime.strptime(args.meeting_date, "%Y-%m-%d")
    except ValueError:
        print(f"Error: Invalid date format. Use YYYY-MM-DD (e.g., 2025-11-21)")
        return 1

    # Load documents
    docs = load_meeting_documents(args.meeting_date)
    if not docs:
        return 1

    # Display document summary
    print(f"\n{'='*60}")
    print(f"Meeting: {args.meeting_date}")
    print(f"{'='*60}")
    for doc_type, path in docs.items():
        if path:
            size_kb = path.stat().st_size / 1024
            print(f"  {doc_type}: {path.name} ({size_kb:.1f} KB)")
        else:
            print(f"  {doc_type}: NOT FOUND")

    # Parse all sources and merge
    extracted_grants = parse_all_sources(docs, args.meeting_date)

    # Load CSV entries for validation
    df_csv = pd.DataFrame()
    if args.csv is not None and args.csv.exists() and not args.no_validation:
        df_csv = load_csv_entries(args.csv, args.meeting_date)

    # Output directory (meeting folder)
    output_dir = MEETINGS_DIR / args.meeting_date

    # Export extracted grants CSV
    print(f"\n--- Exporting Results ---")
    extracted_path = output_dir / f"extracted_grants_{args.meeting_date}.csv"
    export_extracted_csv(extracted_grants, extracted_path)

    # Export validation report if CSV available
    if not df_csv.empty:
        validation_path = output_dir / f"validation_report_{args.meeting_date}.csv"
        export_validation_report(extracted_grants, df_csv, validation_path)

    # Display summary
    display_summary(extracted_grants, df_csv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
