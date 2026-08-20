"""
CMFA Agenda Parser

Extracts charitable grant items from CMFA meeting agendas.
The agenda serves as the primary source for what grants are being considered.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pdfplumber


@dataclass
class AgendaGrant:
    """A charitable grant item from the agenda."""
    entity: str  # e.g., "Post Renaissance Apartments, LP"
    property_name: str  # e.g., "Renaissance Apartment Homes"
    city: str
    county: str
    resolution: str  # e.g., "25-551"
    grant_amount: Optional[int] = None  # Usually $10,000
    item_type: str = "authorize"  # "authorize" or "preliminary"


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract all text from a PDF file."""
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            text_parts.append(text)
    return "\n".join(text_parts)


def parse_grant_line(line: str, item_type: str = "authorize") -> Optional[AgendaGrant]:
    """
    Parse a single grant line item from the agenda.

    Examples:
    - "Post Renaissance Apartments, LP, (Renaissance Apartment Homes), City of Fresno, County of Fresno;
       grant up to $10,000 in a Charitable Affordable Housing grant. (Resolution 25-551)"
    - "18337 Kittridge, LP, (Kittridge Affordable Housing Apartments), City of Los Angeles,
       County of Los Angeles; grant up to $10,000 in a Charitable Affordable Housing grant. (Resolution 25-552)"
    """
    # Pattern for entity, property name, city, county, resolution
    # Entity is everything before the first parenthesized property name
    # Property name is in parentheses
    # City follows "City of"
    # County follows "County of"
    # Resolution is in parentheses at the end

    # Extract property name in parentheses (first occurrence that looks like a property)
    property_match = re.search(r'\(([^)]+(?:Apartments?|Homes?|Village|Housing)[^)]*)\)', line, re.IGNORECASE)
    if not property_match:
        # Try a more general pattern for property name
        property_match = re.search(r'\(([A-Z][^)]+)\)', line)

    if not property_match:
        return None

    property_name = property_match.group(1).strip()

    # Extract entity (everything before the property name parentheses)
    entity_end = property_match.start()
    entity_text = line[:entity_end].strip()
    # Remove trailing comma, "or an affiliate thereof"
    entity = re.sub(r',?\s*or an affiliate thereof\s*,?\s*$', '', entity_text, flags=re.IGNORECASE)
    entity = entity.rstrip(', ')

    # Extract city
    city_match = re.search(r'City of ([^,]+)', line)
    city = city_match.group(1).strip() if city_match else ""

    # Extract county
    county_match = re.search(r'County of ([^;,]+)', line)
    county = county_match.group(1).strip() if county_match else ""

    # Extract resolution
    resolution_match = re.search(r'\(Resolution\s*(\d+-\d+)\)', line)
    resolution = resolution_match.group(1) if resolution_match else ""

    # Extract grant amount
    amount_match = re.search(r'\$([0-9,]+)', line)
    grant_amount = None
    if amount_match:
        grant_amount = int(amount_match.group(1).replace(',', ''))

    if not entity or not property_name:
        return None

    return AgendaGrant(
        entity=entity,
        property_name=property_name,
        city=city,
        county=county,
        resolution=resolution,
        grant_amount=grant_amount,
        item_type=item_type
    )


def parse_agenda_grants(text: str) -> list[AgendaGrant]:
    """
    Parse all charitable grant items from agenda text.

    Looks for sections like:
    - "Authorize the giving of a charitable grant" (final authorization)
    - "Acceptance of applications...preliminary approvals" (preliminary)
    """
    grants = []

    # Split into lines, preserving context
    lines = text.split('\n')

    # Track current section type
    current_section = None
    current_item_text = ""
    current_item_section = None  # Track which section the current item belongs to

    def process_pending_item():
        """Process any pending item text."""
        nonlocal current_item_text, current_item_section
        if current_item_text and current_item_section:
            grant = parse_grant_line(current_item_text, current_item_section)
            if grant:
                grants.append(grant)
        current_item_text = ""
        current_item_section = None

    for i, line in enumerate(lines):
        line_stripped = line.strip()

        # Detect section headers
        if 'authorize the giving of' in line_stripped.lower() and 'charitable' in line_stripped.lower():
            process_pending_item()
            current_section = "authorize"
            continue
        elif 'acceptance of application' in line_stripped.lower() and 'preliminary' in line_stripped.lower():
            process_pending_item()
            current_section = "preliminary"
            continue
        elif re.match(r'^\d+\.', line_stripped) and current_section:
            # New numbered item - process pending item before potentially resetting
            process_pending_item()
            # Reset section if this isn't a grant-related section
            if not ('charitable' in line_stripped.lower() or 'grant' in line_stripped.lower()):
                current_section = None
            continue

        # If we're in a grant section, look for grant items
        if current_section:
            # Grant items start with letter like "a.", "b.", etc.
            if re.match(r'^[a-z]\.\s', line_stripped):
                # Process previous item if exists
                process_pending_item()

                # Start new item with current section
                current_item_text = line_stripped[2:].strip()  # Remove "a. " prefix
                current_item_section = current_section
            elif current_item_text and line_stripped:
                # Continuation of current item
                current_item_text += " " + line_stripped

    # Don't forget last item
    process_pending_item()

    return grants


def parse_agenda_pdf(pdf_path: Path) -> list[AgendaGrant]:
    """
    Parse a CMFA agenda PDF and extract all charitable grant items.

    Args:
        pdf_path: Path to the agenda PDF

    Returns:
        List of AgendaGrant objects
    """
    text = extract_text_from_pdf(pdf_path)
    return parse_agenda_grants(text)


if __name__ == "__main__":
    # Test with a sample agenda
    import sys

    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1])
    else:
        pdf_path = Path("data/cmfa_scraping/meetings/2025-11-21/agenda.pdf")

    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    grants = parse_agenda_pdf(pdf_path)

    print(f"Found {len(grants)} charitable grant items:\n")
    for i, grant in enumerate(grants, 1):
        print(f"{i}. {grant.property_name}")
        print(f"   Entity: {grant.entity}")
        print(f"   City: {grant.city}, County: {grant.county}")
        print(f"   Resolution: {grant.resolution}")
        print(f"   Amount: ${grant.grant_amount:,}" if grant.grant_amount else "   Amount: N/A")
        print(f"   Type: {grant.item_type}")
        print()
