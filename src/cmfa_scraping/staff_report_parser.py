"""
Staff Report Parser

Extracts detailed grant information from CMFA Staff Report PDFs.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pdfplumber


@dataclass
class StaffReportGrant:
    """A welfare tax exemption grant extracted from a staff report."""
    property_name: str
    applicant: str  # This is the investor/development company (maps to CSV "Investor 1")
    nonprofit_partner: str
    city: str
    county: str
    meeting_date: datetime
    address: str = ""
    total_units: int | None = None
    restricted_units: int | None = None
    rent_restricted_pct: str = ""  # e.g., "100% at 80% AMI" or "40% at 60% AMI"
    restricted_pct: int | None = None  # overall percent of units restricted
    unit_mix: str = ""
    term_years: int | None = None
    city_share: float | None = None
    estimated_closing: str = ""
    action_type: str = ""  # Resolution, Final Resolution, Inducement Resolution
    grant_description: str = ""  # Purpose field from header
    source_file: str = ""
    source_page: int = 0


def parse_staff_report_pdf(pdf_path: Path) -> list[StaffReportGrant]:
    """
    Parse a Staff Report PDF to extract welfare tax exemption grant details.

    Args:
        pdf_path: Path to the staff report PDF

    Returns:
        List of StaffReportGrant objects
    """
    grants = []

    # Extract meeting date - try parent folder name first (meetings/YYYY-MM-DD/staff_report.pdf)
    # then filename (YYYY-MM-DD-staff_report.pdf)
    meeting_date = extract_date_from_filename(pdf_path.parent.name)
    if meeting_date is None:
        meeting_date = extract_date_from_filename(pdf_path.name)
    if meeting_date is None:
        print(f"Warning: Could not extract date from {pdf_path}")
        return grants

    with pdfplumber.open(pdf_path) as pdf:
        # Process each page
        i = 0
        while i < len(pdf.pages):
            page_text = pdf.pages[i].extract_text() or ""

            # Check if this page starts a Charitable Affordable Housing section
            if is_grant_start_page(page_text):
                # Collect text from this grant section (may span multiple pages)
                grant_text = page_text
                start_page = i + 1  # 1-indexed

                # Look ahead to collect continuation pages
                j = i + 1
                while j < len(pdf.pages):
                    next_text = pdf.pages[j].extract_text() or ""
                    # Check if we've hit a new section
                    if is_new_section_start(next_text):
                        break
                    grant_text += "\n" + next_text
                    j += 1

                # Parse the grant
                grant = parse_grant_section(grant_text, meeting_date, pdf_path.name, start_page)
                if grant:
                    grants.append(grant)

                i = j  # Skip to end of this grant section
            else:
                i += 1

    return grants


def is_grant_start_page(text: str) -> bool:
    """Check if this page starts a Charitable Affordable Housing grant section."""
    return (
        "SUMMARY AND RECOMMENDATIONS" in text and
        "Charitable Affordable Housing" in text
    )


def is_new_section_start(text: str) -> bool:
    """Check if this page starts a new section (not continuation)."""
    # New sections start with a title followed by SUMMARY AND RECOMMENDATIONS
    lines = text.strip().split('\n')
    if len(lines) >= 2:
        # First line should be a title (often all caps)
        # Second line should be "SUMMARY AND RECOMMENDATIONS"
        if "SUMMARY AND RECOMMENDATIONS" in lines[1]:
            return True
    return False


def parse_grant_section(text: str, meeting_date: datetime, source_file: str,
                        source_page: int) -> StaffReportGrant | None:
    """Parse a single grant section to extract all fields."""

    lines = text.split('\n')

    # Extract property name (first line, often ALL CAPS)
    property_name = lines[0].strip() if lines else "Unknown"

    # Extract header fields
    applicant = extract_field(text, r'Applicant:\s*(.+?)(?:\n|$)')
    # Match both "Nonprofit:" and "Nonprofit Partner:" variations
    nonprofit = extract_field(text, r'Nonprofit(?:\s+Partner)?:\s*(.+?)(?:\n|$)')
    action = extract_field(text, r'Action:\s*(.+?)(?:\n|$)')

    # Extract grant description (Purpose field)
    # Purpose wraps across lines; capture until the next "Label:" line
    m = re.search(r'Purpose:\s*(.+?)(?=\n[A-Z][A-Za-z ]{2,20}:|\n\n|$)', text, re.DOTALL)
    grant_description = re.sub(r'\s+', ' ', m.group(1)).strip() if m else ""

    # Extract city and county from Purpose line
    purpose_match = re.search(
        r'City\s+of\s+([^,]+),\s*(?:([^,]+)\s+)?County',
        text, re.IGNORECASE
    )
    if purpose_match:
        city = purpose_match.group(1).strip()
        county = purpose_match.group(2).strip() if purpose_match.group(2) else ""
    else:
        city = extract_city_from_text(text)
        county = extract_county_from_text(text)

    # Extract address from The Project section
    address = extract_address(text)

    # Extract unit counts
    total_units = extract_total_units(text)
    restricted_units = extract_restricted_units(text)

    # Extract rent restricted percentage (AMI levels) + clean overall percent.
    # Deliberately NOT derived from each other: restricted_units reports what
    # the AMI tier lines say, restricted_pct what the header says — build-time
    # QA compares them, and a derivation here would mask real inconsistencies
    # (and the restricted>total multi-building signature).
    rent_restricted_pct = extract_rent_restricted_pct(text)
    restricted_pct = extract_restricted_pct(text)

    # Extract unit mix
    unit_mix = extract_unit_mix(text)

    # Extract term of restriction
    term_years = extract_term_years(text)

    # Extract city's expected share
    city_share = extract_city_share(text)

    # Extract estimated closing
    estimated_closing = extract_field(text, r'Estimated\s+Closing:\s*(.+?)(?:\n|$)')

    return StaffReportGrant(
        property_name=property_name,
        applicant=applicant,
        nonprofit_partner=nonprofit,
        city=city,
        county=county,
        meeting_date=meeting_date,
        address=address,
        total_units=total_units,
        restricted_units=restricted_units,
        rent_restricted_pct=rent_restricted_pct,
        restricted_pct=restricted_pct,
        unit_mix=unit_mix,
        term_years=term_years,
        city_share=city_share,
        estimated_closing=estimated_closing,
        action_type=action,
        grant_description=grant_description,
        source_file=source_file,
        source_page=source_page
    )


def extract_field(text: str, pattern: str) -> str:
    """Extract a field using regex pattern."""
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def extract_city_from_text(text: str) -> str:
    """Extract city from various patterns in the text."""
    patterns = [
        r'City\s+of\s+([^,\n]+)',
        r'located\s+in\s+(?:the\s+)?(?:City\s+of\s+)?([^,\n]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def extract_county_from_text(text: str) -> str:
    """Extract county from text."""
    match = re.search(r'(\w+)\s+County,?\s+California', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def extract_address(text: str) -> str:
    """Extract property address from The Project section."""
    # First, try to find "The Project:" section
    project_section = ""
    project_match = re.search(r'The\s+Project:\s*(.*?)(?=The\s+City\s+of|Terms\s+of\s+Transaction|Public\s+Benefit|Finance\s+Team)',
                              text, re.IGNORECASE | re.DOTALL)
    if project_match:
        project_section = project_match.group(1)
    else:
        project_section = text

    # Pattern for addresses - look for street number followed by street name
    patterns = [
        # "located at 740 S Western Avenue in Santa Maria, CA"
        r'located\s+at\s+(\d+[^,\n]+(?:,\s*[A-Za-z\s]+)?(?:,\s*CA)?(?:\s+\d{5})?)',
        # "property located at 740 S Western Avenue"
        r'property[^.]*?(\d+\s+[A-Za-z0-9\s.]+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Way|Lane|Ln|Place|Pl)[^,\n]*)',
        # Just an address with street type
        r'(\d+\s+[A-Za-z0-9\s.]+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Way|Lane|Ln|Place|Pl|Expy|Expressway|Highway|Hwy)[^,\n]*,\s*[A-Za-z\s]+(?:,\s*CA)?)',
    ]

    for pattern in patterns:
        match = re.search(pattern, project_section, re.IGNORECASE)
        if match:
            address = match.group(1).strip()
            # Clean up - remove extra whitespace and trailing text
            address = re.sub(r'\s+', ' ', address)
            address = re.sub(r'\.\s*$', '', address)  # Remove trailing period
            # Limit length to avoid capturing too much
            if len(address) < 150:
                return address
    return ""


def extract_total_units(text: str) -> int | None:
    """Extract total unit count from The Project section.

    Strategy:
    1. Search "The Project" section first - has definitive statements like
       "110-unit" or "consisting of 42 units"
    2. Public Benefit section shows RESTRICTED units, not total (avoid AMI breakdowns)
    3. Only use Public Benefit if it says "100%" (total = restricted)
    """
    # Extract "The Project" section
    # Note: Use "The City of X:" (with colon) to match section header, not inline text like "in the City of X"
    project_match = re.search(
        r'The\s+Project:\s*(.*?)(?=\nThe\s+City\s+of\s+\w+:|Terms\s+of\s+Transaction|Public\s+Benefit|Finance\s+Team)',
        text, re.IGNORECASE | re.DOTALL
    )
    project_text = project_match.group(1) if project_match else ""

    # Extract Public Benefit section
    benefit_match = re.search(
        r'Public\s+Benefit:?\s*(.*?)(?=Finance\s+Team|Recommendation|$)',
        text, re.IGNORECASE | re.DOTALL
    )
    benefit_text = benefit_match.group(1) if benefit_match else ""

    # PRIORITY 1: Look in "The Project" section for total unit count
    # These patterns are definitive totals, not restricted counts.
    # Numbers may carry thousands separators ("1,008-unit"); "units per acre"
    # is a density, never a count.
    project_patterns = [
        # "110-unit" or "42-unit"
        r'([\d,]+)[- ]unit\s+(?:affordable\s+)?(?:multi-?family|apartment|housing|rental)',
        # "consisting of 42 units"
        r'consisting\s+of\s+([\d,]+)\s+units',
        # "development of a 110-unit"
        r'(?:development|construction)\s+of\s+(?:a\s+)?([\d,]+)[- ]unit',
        # "356 rentable units"
        r'([\d,]+)\s+rentable\s+units',
        # Simple "X-unit" pattern
        r'([\d,]+)[- ]unit',
        # "X units" in project section (not "X units per acre")
        r'([\d,]+)\s+units(?!\s+per\b)',
    ]

    for pattern in project_patterns:
        match = re.search(pattern, project_text, re.IGNORECASE)
        if match:
            num = int(match.group(1).replace(",", ""))
            if 5 <= num <= 2000:
                return num

    # PRIORITY 2: Check Public Benefit for "100% (X Units)" - means all units restricted
    # Only use this if it says 100%, otherwise it's a partial AMI breakdown
    pct_100_match = re.search(r'100%\s*\((\d+)\s+[Uu]nits?\)', benefit_text)
    if pct_100_match:
        num = int(pct_100_match.group(1))
        if 5 <= num <= 2000:
            return num

    # PRIORITY 3: Full text fallback with conservative patterns
    fallback_patterns = [
        r'(\d+)[- ]unit\s+(?:affordable\s+)?(?:multi-?family|apartment|housing|rental)',
        r'consisting\s+of\s+(\d+)\s+units',
    ]

    for pattern in fallback_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            num = int(match.group(1))
            if 5 <= num <= 2000:
                return num

    return None


def extract_restricted_units(text: str) -> int | None:
    """Extract restricted unit count from Public Benefit section."""
    # AMI tier lines list restricted counts per tier — SUM them ("20% (24
    # Units) restricted to 60% ... and 80% (98 Units) restricted to 80% ...").
    benefit = re.search(
        r'(?:Public\s+Benefit|Percent\s+of\s+Restricted).*?(?=Finance\s+Team|Recommendation|$)',
        text, re.IGNORECASE | re.DOTALL)
    scope = benefit.group(0) if benefit else text
    tiers = re.findall(r'\d+\s*%\s*\(([\d,]+)\s+units?\)\s+restricted\s+to',
                       scope, re.IGNORECASE)
    if tiers:
        return sum(int(t.replace(",", "")) for t in tiers)

    # Fallback patterns in Public Benefit section
    # "40% (184 units) restricted to 80% or less" — counts may carry
    # thousands separators ("1,008 Units")
    patterns = [
        r'(\d+)\s*%?\s*\(([\d,]+)\s+units?\)\s+restricted',
        r'([\d,]+)\s+units?\s+restricted',
        r'Percent[^:]*:\s*(\d+)%\s*\n.*?([\d,]+)\s+units?',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            # Return the unit count (second group if available, else first)
            groups = match.groups()
            for g in reversed(groups):
                if g and g.replace(",", "").isdigit():
                    return int(g.replace(",", ""))
    return None


def extract_unit_mix(text: str) -> str:
    """Extract unit mix description."""
    match = re.search(r'Unit\s+[Mm]ix:\s*([^\n]+)', text)
    if match:
        return match.group(1).strip()
    return ""


def extract_term_years(text: str) -> int | None:
    """Extract term of restriction in years."""
    match = re.search(r'Term\s+of\s+Restriction:\s*(\d+)\s+years?', text, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # Also try "next XX years"
    match = re.search(r'next\s+(\d+)\s+years', text, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return None


def extract_restricted_pct(text: str) -> int | None:
    """Extract the OVERALL percent of units restricted.

    Staff reports state it canonically as
    "Percent of Restricted Rental Units in the Project: 40%" — distinct from
    the AMI tier lines ("100% (45 Units) restricted to 80% ..."), whose
    percentages are sometimes of the restricted units, not of the total.
    """
    m = re.search(
        r'Percent\s+of\s+Restricted\s+Rental\s+Units\s+in\s+the\s+Project:\s*([\d.]+)\s*%',
        text, re.IGNORECASE)
    if m:
        pct = round(float(m.group(1)))
        if 0 < pct <= 100:
            return pct
    return None


def extract_rent_restricted_pct(text: str) -> str:
    """
    Extract rent restriction percentage and AMI levels.

    Looks for patterns like:
    - "100% (278 Units) restricted to 80% or less of area median income"
    - "40% (80 units) restricted to 80% or less of area median income"
    - Multiple lines with different AMI levels

    Returns a summary string like "100% at 80% AMI" or "40% at 60% AMI, 30% at 80% AMI"
    """
    # Find the Public Benefit or Percent of Restricted section
    benefit_match = re.search(
        r'(?:Public\s+Benefit|Percent\s+of\s+Restricted).*?(?=Finance\s+Team|Recommendation|$)',
        text, re.IGNORECASE | re.DOTALL
    )
    search_text = benefit_match.group(0) if benefit_match else text

    # Pattern: "XX% (YY units) restricted to ZZ% or less of area median income"
    # (unit counts may carry thousands separators: "100% (1,008 Units)")
    ami_patterns = re.findall(
        r'(\d+)%?\s*\([\d,]+\s+[Uu]nits?\)\s+restricted\s+to\s+(\d+)%\s+or\s+less',
        search_text, re.IGNORECASE
    )

    if ami_patterns:
        # Combine into readable format
        parts = [f"{pct}% at {ami}% AMI" for pct, ami in ami_patterns]
        return "; ".join(parts)

    # Simpler pattern: "X% restricted to Y% AMI"
    simple_match = re.search(r'(\d+)%\s+restricted.*?(\d+)%\s+(?:or\s+less\s+of\s+)?(?:area\s+)?median', search_text, re.IGNORECASE)
    if simple_match:
        return f"{simple_match.group(1)}% at {simple_match.group(2)}% AMI"

    # Just look for AMI level
    ami_match = re.search(r'(\d+)%\s+(?:or\s+less\s+of\s+)?(?:area\s+)?median\s+income', search_text, re.IGNORECASE)
    if ami_match:
        return f"at {ami_match.group(1)}% AMI"

    return ""


def extract_city_share(text: str) -> float | None:
    """Extract city's expected share amount."""
    match = re.search(r'City\s+is\s+expected\s+to\s+receive\s+approximately\s+\$?([\d,]+)',
                      text, re.IGNORECASE)
    if match:
        amount_str = match.group(1).replace(',', '')
        return float(amount_str)
    return None


def extract_date_from_filename(filename: str) -> datetime | None:
    """Extract date from filename like 2023-12-08-staff_report.pdf"""
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return datetime(year, month, day)
        except ValueError:
            return None
    return None
