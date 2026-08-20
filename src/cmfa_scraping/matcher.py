"""
CSV Matcher

Compares extracted PDF data against the manually-collected CSV.
Generates match results and identifies discrepancies.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
from fuzzywuzzy import fuzz

from .minutes_parser import MinutesGrant
# Staff report import is optional - only needed for detailed extraction
try:
    from .staff_report_parser import StaffReportGrant
except ImportError:
    StaffReportGrant = None


@dataclass
class MatchResult:
    """Result of matching a PDF grant to a CSV row."""
    csv_row_index: int | None
    csv_resolution: str
    pdf_resolution: str
    property_name_csv: str
    property_name_pdf: str
    match_confidence: float  # 0-100
    match_type: str  # "exact", "fuzzy", "no_match"
    field_differences: dict  # field_name -> (csv_value, pdf_value)


@dataclass
class CombinedGrant:
    """Combined data from Minutes and Staff Report for a single grant."""
    # From Minutes
    resolution: str
    legal_entity: str
    meeting_date: datetime

    # From Staff Report (or Minutes fallback)
    property_name: str
    applicant: str
    nonprofit_partner: str
    city: str
    county: str
    address: str
    total_units: int | None
    restricted_units: int | None
    unit_mix: str
    term_years: int | None
    city_share: float | None

    # Metadata
    source_minutes: str
    source_staff_report: str


def load_csv(csv_path: str) -> pd.DataFrame:
    """Load and clean the CSV file."""
    df = pd.read_csv(csv_path)

    # Standardize column names
    df.columns = df.columns.str.strip()

    # Parse dates
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

    return df


def combine_pdf_sources(minutes_grants: list[MinutesGrant],
                        staff_report_grants: list[StaffReportGrant]) -> list[CombinedGrant]:
    """
    Combine data from Minutes and Staff Reports.

    Joins on property_name + meeting_date (fuzzy match).
    """
    combined = []

    # Index staff reports by (normalized_property_name, date)
    staff_report_index: dict[tuple, StaffReportGrant] = {}
    for sr in staff_report_grants:
        key = (normalize_property_name(sr.property_name), sr.meeting_date.date())
        staff_report_index[key] = sr

    for mg in minutes_grants:
        # Try to find matching staff report
        key = (normalize_property_name(mg.property_name), mg.meeting_date.date())
        sr = staff_report_index.get(key)

        # If no exact match, try fuzzy match
        if sr is None:
            sr = fuzzy_match_staff_report(mg, staff_report_grants)

        combined.append(CombinedGrant(
            resolution=mg.resolution,
            legal_entity=mg.legal_entity,
            meeting_date=mg.meeting_date,
            property_name=mg.property_name,
            applicant=sr.applicant if sr else mg.legal_entity,
            nonprofit_partner=sr.nonprofit_partner if sr else "",
            city=mg.city or (sr.city if sr else ""),
            county=mg.county or (sr.county if sr else ""),
            address=sr.address if sr else "",
            total_units=sr.total_units if sr else None,
            restricted_units=sr.restricted_units if sr else None,
            unit_mix=sr.unit_mix if sr else "",
            term_years=sr.term_years if sr else None,
            city_share=sr.city_share if sr else None,
            source_minutes=mg.source_file,
            source_staff_report=sr.source_file if sr else ""
        ))

    return combined


def normalize_property_name(name: str) -> str:
    """Normalize property name for matching."""
    name = name.lower()
    # Remove common suffixes
    name = re.sub(r'\s*(apartments?|apt|housing|homes?)\s*$', '', name, flags=re.IGNORECASE)
    # Remove extra whitespace
    name = ' '.join(name.split())
    return name


def fuzzy_match_staff_report(minutes_grant: MinutesGrant,
                              staff_reports: list[StaffReportGrant],
                              threshold: int = 70) -> StaffReportGrant | None:
    """Find best matching staff report using fuzzy matching."""
    best_match = None
    best_score = 0

    mg_name = normalize_property_name(minutes_grant.property_name)
    mg_date = minutes_grant.meeting_date.date()

    for sr in staff_reports:
        # Must be same date
        if sr.meeting_date.date() != mg_date:
            continue

        sr_name = normalize_property_name(sr.property_name)
        score = fuzz.ratio(mg_name, sr_name)

        if score > best_score and score >= threshold:
            best_score = score
            best_match = sr

    return best_match


def match_to_csv(combined_grants: list[CombinedGrant],
                 csv_df: pd.DataFrame) -> tuple[list[MatchResult], list[CombinedGrant]]:
    """
    Match combined PDF grants to CSV rows.

    Returns:
        - List of MatchResult for matched grants
        - List of CombinedGrant not found in CSV (new discoveries)
    """
    results = []
    unmatched_pdf = []

    # Track which CSV rows have been matched
    matched_csv_indices = set()

    for grant in combined_grants:
        match_result = find_csv_match(grant, csv_df, matched_csv_indices)

        if match_result.csv_row_index is not None:
            matched_csv_indices.add(match_result.csv_row_index)
            results.append(match_result)
        else:
            unmatched_pdf.append(grant)
            results.append(match_result)

    return results, unmatched_pdf


def find_csv_match(grant: CombinedGrant, csv_df: pd.DataFrame,
                   exclude_indices: set) -> MatchResult:
    """Find the best matching CSV row for a grant."""

    best_match_idx = None
    best_confidence = 0
    best_match_type = "no_match"

    for idx, row in csv_df.iterrows():
        if idx in exclude_indices:
            continue

        # Try exact resolution match first
        csv_resolution = str(row.get('Resolution', '')).strip()
        if csv_resolution and csv_resolution == grant.resolution:
            confidence = 100
            match_type = "exact"
        else:
            # Fuzzy match on property name + date
            csv_prop_name = str(row.get('Property Name', '')).strip()
            csv_date = row.get('Date')

            # Check date match
            date_match = False
            if pd.notna(csv_date):
                try:
                    csv_date_obj = pd.to_datetime(csv_date).date()
                    date_match = csv_date_obj == grant.meeting_date.date()
                except Exception:
                    pass

            if not date_match:
                continue

            # Fuzzy match property name
            pdf_name = normalize_property_name(grant.property_name)
            csv_name = normalize_property_name(csv_prop_name)
            name_score = fuzz.ratio(pdf_name, csv_name)

            # Also check applicant/entity
            csv_entity = str(row.get('Applicant / Entity', '')).strip()
            entity_score = fuzz.ratio(
                grant.legal_entity.lower(),
                csv_entity.lower()
            )

            # Combined confidence
            confidence = max(name_score, (name_score + entity_score) / 2)
            match_type = "fuzzy"

        if confidence > best_confidence:
            best_confidence = confidence
            best_match_idx = idx
            best_match_type = match_type

    # Get field differences if we have a match
    field_differences = {}
    csv_resolution = ""
    csv_prop_name = ""

    if best_match_idx is not None and best_confidence >= 60:
        row = csv_df.iloc[best_match_idx]
        csv_resolution = str(row.get('Resolution', '')).strip()
        csv_prop_name = str(row.get('Property Name', '')).strip()

        # Compare fields
        field_differences = compare_fields(grant, row)
    else:
        best_match_idx = None
        best_match_type = "no_match"

    return MatchResult(
        csv_row_index=best_match_idx,
        csv_resolution=csv_resolution,
        pdf_resolution=grant.resolution,
        property_name_csv=csv_prop_name,
        property_name_pdf=grant.property_name,
        match_confidence=best_confidence,
        match_type=best_match_type,
        field_differences=field_differences
    )


def compare_fields(grant: CombinedGrant, csv_row: pd.Series) -> dict:
    """Compare grant fields to CSV row and return differences."""
    differences = {}

    # Field mappings: (grant_attr, csv_column, comparison_func)
    field_mappings = [
        ('city', 'City', compare_string),
        ('county', 'County', compare_string),
        ('nonprofit_partner', 'Nonprofit Partner', compare_string_fuzzy),
        ('total_units', 'TOTAL UNITS', compare_number),
        ('restricted_units', 'Rent Restricted', compare_number),
        ('address', 'Address', compare_string_fuzzy),
    ]

    for grant_attr, csv_col, compare_func in field_mappings:
        pdf_value = getattr(grant, grant_attr, None)
        csv_value = csv_row.get(csv_col)

        if pdf_value is None and (pd.isna(csv_value) or csv_value == ''):
            continue  # Both empty, no difference

        if not compare_func(pdf_value, csv_value):
            differences[csv_col] = (
                str(csv_value) if pd.notna(csv_value) else "",
                str(pdf_value) if pdf_value is not None else ""
            )

    return differences


def compare_string(pdf_val, csv_val) -> bool:
    """Compare strings (case-insensitive, normalized)."""
    if pdf_val is None and (pd.isna(csv_val) or csv_val == ''):
        return True
    if pdf_val is None or pd.isna(csv_val):
        return False

    return str(pdf_val).lower().strip() == str(csv_val).lower().strip()


def compare_string_fuzzy(pdf_val, csv_val, threshold: int = 80) -> bool:
    """Compare strings with fuzzy matching."""
    if pdf_val is None and (pd.isna(csv_val) or csv_val == ''):
        return True
    if pdf_val is None or pd.isna(csv_val):
        return False

    score = fuzz.ratio(str(pdf_val).lower(), str(csv_val).lower())
    return score >= threshold


def compare_number(pdf_val, csv_val) -> bool:
    """Compare numeric values."""
    if pdf_val is None and (pd.isna(csv_val) or csv_val == ''):
        return True
    if pdf_val is None or pd.isna(csv_val):
        return False

    try:
        return int(pdf_val) == int(float(csv_val))
    except (ValueError, TypeError):
        return False


def find_unmatched_csv_rows(csv_df: pd.DataFrame,
                            match_results: list[MatchResult]) -> pd.DataFrame:
    """Find CSV rows that were not matched to any PDF grant."""
    matched_indices = {r.csv_row_index for r in match_results if r.csv_row_index is not None}
    unmatched_mask = ~csv_df.index.isin(matched_indices)
    return csv_df[unmatched_mask]


def match_minutes_to_csv(minutes_grants: list[MinutesGrant],
                          csv_df: pd.DataFrame) -> tuple[list[MatchResult], list[MinutesGrant]]:
    """
    Match minutes grants directly to CSV rows (fast mode - no staff report parsing needed).

    Returns:
        - List of MatchResult for all grants
        - List of MinutesGrant not found in CSV (new discoveries)
    """
    results = []
    unmatched_pdf = []
    matched_csv_indices = set()

    for grant in minutes_grants:
        match_result = find_csv_match_from_minutes(grant, csv_df, matched_csv_indices)

        if match_result.csv_row_index is not None:
            matched_csv_indices.add(match_result.csv_row_index)
        else:
            unmatched_pdf.append(grant)

        results.append(match_result)

    return results, unmatched_pdf


def find_csv_match_from_minutes(grant: MinutesGrant, csv_df: pd.DataFrame,
                                 exclude_indices: set) -> MatchResult:
    """Find the best matching CSV row for a minutes grant."""

    best_match_idx = None
    best_confidence = 0
    best_match_type = "no_match"

    for idx, row in csv_df.iterrows():
        if idx in exclude_indices:
            continue

        # Try exact resolution match first
        csv_resolution = str(row.get('Resolution', '')).strip()
        if csv_resolution and csv_resolution == grant.resolution:
            confidence = 100
            match_type = "exact"
        else:
            # Fuzzy match on property name + date
            csv_prop_name = str(row.get('Property Name', '')).strip()
            csv_date = row.get('Date')

            # Check date match
            date_match = False
            if pd.notna(csv_date):
                try:
                    csv_date_obj = pd.to_datetime(csv_date).date()
                    date_match = csv_date_obj == grant.meeting_date.date()
                except Exception:
                    pass

            if not date_match:
                continue

            # Fuzzy match property name
            pdf_name = normalize_property_name(grant.property_name)
            csv_name = normalize_property_name(csv_prop_name)
            name_score = fuzz.ratio(pdf_name, csv_name)

            # Also check applicant/entity
            csv_entity = str(row.get('Applicant / Entity', '')).strip()
            entity_score = fuzz.ratio(
                grant.legal_entity.lower(),
                csv_entity.lower()
            )

            # Combined confidence
            confidence = max(name_score, (name_score + entity_score) / 2)
            match_type = "fuzzy"

        if confidence > best_confidence:
            best_confidence = confidence
            best_match_idx = idx
            best_match_type = match_type

    # Get field differences if we have a match
    field_differences = {}
    csv_resolution = ""
    csv_prop_name = ""

    if best_match_idx is not None and best_confidence >= 60:
        row = csv_df.iloc[best_match_idx]
        csv_resolution = str(row.get('Resolution', '')).strip()
        csv_prop_name = str(row.get('Property Name', '')).strip()

        # Compare fields (limited - we only have minutes data)
        field_differences = compare_minutes_fields(grant, row)
    else:
        best_match_idx = None
        best_match_type = "no_match"

    return MatchResult(
        csv_row_index=best_match_idx,
        csv_resolution=csv_resolution,
        pdf_resolution=grant.resolution,
        property_name_csv=csv_prop_name,
        property_name_pdf=grant.property_name,
        match_confidence=best_confidence,
        match_type=best_match_type,
        field_differences=field_differences
    )


def compare_minutes_fields(grant: MinutesGrant, csv_row: pd.Series) -> dict:
    """Compare minutes grant fields to CSV row (limited fields)."""
    differences = {}

    # Only compare fields available from minutes
    if not compare_string(grant.city, csv_row.get('City')):
        differences['City'] = (
            str(csv_row.get('City', '')) if pd.notna(csv_row.get('City')) else "",
            grant.city
        )

    if not compare_string(grant.county, csv_row.get('County')):
        differences['County'] = (
            str(csv_row.get('County', '')) if pd.notna(csv_row.get('County')) else "",
            grant.county
        )

    return differences
