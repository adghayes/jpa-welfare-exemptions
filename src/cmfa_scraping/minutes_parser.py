"""
Meeting Minutes Parser

Extracts welfare tax exemption grant resolutions from CMFA Meeting Minutes (PDF and DOCX).
Format:
  a. [Legal Entity], ([Property Name]), City of [City], County of [County];
     grant up to $10,000 in charitable affordable housing grant. (Resolution XX-XXX)
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pdfplumber

# Optional DOCX support
try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


@dataclass
class MinutesGrant:
    """A welfare tax exemption grant extracted from meeting minutes."""
    resolution: str
    legal_entity: str
    property_name: str
    city: str
    county: str
    meeting_date: datetime
    source_file: str


def extract_text_from_docx(docx_path: Path) -> str:
    """Extract text from a DOCX file."""
    if not DOCX_AVAILABLE:
        raise ImportError("python-docx is required for DOCX parsing. Install with: pip install python-docx")

    doc = DocxDocument(docx_path)
    paragraphs = []
    for para in doc.paragraphs:
        paragraphs.append(para.text)
    return "\n".join(paragraphs)


def parse_minutes_file(file_path: Path) -> list[MinutesGrant]:
    """
    Parse a Meeting Minutes file (PDF or DOCX) to extract welfare tax exemption grant resolutions.

    Args:
        file_path: Path to the minutes file (PDF or DOCX)

    Returns:
        List of MinutesGrant objects
    """
    suffix = file_path.suffix.lower()
    if suffix == '.pdf':
        return parse_minutes_pdf(file_path)
    elif suffix in ('.docx', '.doc'):
        return parse_minutes_docx(file_path)
    else:
        print(f"Warning: Unsupported file format {suffix} for {file_path.name}")
        return []


def parse_minutes_docx(docx_path: Path) -> list[MinutesGrant]:
    """
    Parse a Meeting Minutes DOCX to extract welfare tax exemption grant resolutions.

    Args:
        docx_path: Path to the minutes DOCX

    Returns:
        List of MinutesGrant objects
    """
    grants = []

    # Extract meeting date from filename (YYYY-MM-DD-minutes.docx)
    meeting_date = extract_date_from_filename(docx_path.name)
    if meeting_date is None:
        print(f"Warning: Could not extract date from {docx_path.name}")
        return grants

    full_text = extract_text_from_docx(docx_path)

    return _parse_minutes_text(full_text, meeting_date, docx_path.name)


def parse_minutes_pdf(pdf_path: Path) -> list[MinutesGrant]:
    """
    Parse a Meeting Minutes PDF to extract welfare tax exemption grant resolutions.

    Args:
        pdf_path: Path to the minutes PDF

    Returns:
        List of MinutesGrant objects
    """
    grants = []

    # Extract meeting date from filename (YYYY-MM-DD-minutes.pdf)
    meeting_date = extract_date_from_filename(pdf_path.name)
    if meeting_date is None:
        print(f"Warning: Could not extract date from {pdf_path.name}")
        return grants

    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    return _parse_minutes_text(full_text, meeting_date, pdf_path.name)


def _parse_minutes_text(full_text: str, meeting_date: datetime, source_file: str) -> list[MinutesGrant]:
    """
    Parse minutes text to extract welfare tax exemption grant resolutions.

    Args:
        full_text: The full text content of the minutes
        meeting_date: The meeting date
        source_file: Source filename for tracking

    Returns:
        List of MinutesGrant objects
    """
    grants = []

    # Find the charitable affordable housing section
    # Pattern: "Authorize the giving of a charitable grant pursuant to the CMFA Charitable Affordable Housing Program"
    # Or variations like "charitable affordable housing grant"

    # Split into lines and look for grant items
    # Pattern for each grant:
    # [letter]. [Legal Entity], ([Property Name]), City of [City], County of [County];
    #          grant up to $10,000 in charitable affordable housing grant. (Resolution XX-XXX)

    # Normalize text: join wrapped lines into continuous text
    # This handles PDF line wrapping that splits grant entries across lines
    # Must do this BEFORE the filter check, since "grant\nup to" won't match "grant up to"
    full_text = ' '.join(full_text.split())

    # First check if this document has ANY charitable grants
    # Key phrase: "charitable" combined with "grant" near each other
    if 'charitable' not in full_text.lower() or 'grant up to' not in full_text.lower():
        return grants  # No charitable grants in this document

    # Regex pattern to match ONLY charitable grant entries
    # Format: a. [Entity], ([Property]), City of [City], County of [County];
    #         [approve application for a proposed] grant up to $10,000 in charitable [affordable] housing grant. (Resolution XX-XXX)
    # Note: Some say "charitable housing grant", others "charitable affordable housing grant"
    # Note: Some say "approve application for a proposed grant up to" instead of just "grant up to"
    grant_pattern = re.compile(
        r'([a-z])\.\s*'                                    # Letter prefix (a. b. c.)
        r'([^(]+?),?\s*'                                   # Legal entity (comma before paren is optional)
        r'\(([^)]+)\),\s*'                                 # Property name in parentheses
        r'(?:(?:City|Census\s+Designated\s+Place)\s+of\s+([^,]+),\s*)?'  # City or CDP (optional)
        r'(?:Located\s+in\s+)?(?:unincorporated\s+)?'     # Handle "Located in unincorporated"
        r'(?:County\s+of\s+)?([A-Za-z\s]+?)[;,]\s*'        # County (letters/spaces only, non-greedy), semicolon or comma
        r'(?:approve\s+application\s+for\s+a\s+proposed\s+)?'  # Optional "approve application for a proposed"
        r'grant\s+up\s+to\s+\$[\d,]+\s+'                  # Grant amount
        r'(?:in\s+)?(?:a\s+)?'                            # Optional "in a" before charitable
        r'charitable\s+(?:affordable\s+)?housing\s+grant[.\s]*'  # MUST have "charitable" + "housing grant"
        r'\(Resolution\s+(\d{2}\s*-\s*\d+)\)',               # Resolution number (handles wrapped spaces)
        re.IGNORECASE
    )

    # Find all matches - use finditer to get positions for checking "pulled from agenda"
    for match in grant_pattern.finditer(full_text):
        letter, legal_entity, property_name, city, county, resolution = match.groups()

        # Check if this item was pulled from the agenda
        # Look at the text immediately after the match (next ~50 chars)
        after_match = full_text[match.end():match.end() + 50].lower()
        if 'pulled from' in after_match or 'pulled from the agenda' in after_match:
            continue  # Skip pulled items

        # Clean up extracted values
        legal_entity = clean_entity(legal_entity)
        property_name = clean_text(property_name)
        city = clean_text(city) if city else ""  # May be empty for unincorporated locations
        county = clean_text(county)
        resolution = re.sub(r'\s+', '', resolution)  # Remove spaces from resolution (e.g., "24- 278" -> "24-278")

        grant = MinutesGrant(
            resolution=resolution,
            legal_entity=legal_entity,
            property_name=property_name,
            city=city,
            county=county,
            meeting_date=meeting_date,
            source_file=source_file
        )
        grants.append(grant)

    return grants


def clean_text(text: str) -> str:
    """Clean up extracted text."""
    # Remove extra whitespace
    text = ' '.join(text.split())
    # Remove leading/trailing punctuation and whitespace
    text = text.strip(' ,;.')
    return text


def clean_entity(text: str) -> str:
    """Clean up legal entity text, removing motion/voting artifacts."""
    text = clean_text(text)

    # Remove common motion text patterns
    patterns_to_remove = [
        r'^.*?Motion\s+(?:by\s+\w+\.?\s*)?(?:Seconded\s+by\s+\w+\.?\s*)?.*?(?:unanimously|abstentions)[.,\s]*',
        r'^.*?(?:unanimously|abstentions)[.,\s]*',
        r'^\d+\.\s*[^:]+:\s*',  # "6. Authorize the giving of..."
        r'^[a-z]\.\s*',  # "a. " prefix
    ]

    for pattern in patterns_to_remove:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    return text.strip(' ,;.')


def extract_date_from_filename(filename: str) -> datetime | None:
    """Extract date from filename like 2023-12-08-minutes.pdf"""
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return datetime(year, month, day)
        except ValueError:
            return None
    return None


def parse_all_minutes(pdf_dir: str = "data/pdfs", deduplicate: bool = True) -> list[MinutesGrant]:
    """
    Parse all Meeting Minutes files (PDF and DOCX) in a directory.

    Args:
        pdf_dir: Directory containing the files
        deduplicate: If True, keep only the latest occurrence of each resolution

    Returns:
        List of all MinutesGrant objects
    """
    dir_path = Path(pdf_dir)
    all_grants = []

    # Find all minutes files (PDF and DOCX)
    pdf_files = list(dir_path.glob("*-minutes.pdf"))
    docx_files = list(dir_path.glob("*-minutes.docx"))
    minutes_files = sorted(pdf_files + docx_files, key=lambda x: x.name)

    pdf_count = len(pdf_files)
    docx_count = len(docx_files)
    print(f"Found {len(minutes_files)} minutes files ({pdf_count} PDF, {docx_count} DOCX)")

    for minutes_file in minutes_files:
        print(f"Parsing: {minutes_file.name}")
        grants = parse_minutes_file(minutes_file)
        print(f"  Found {len(grants)} grants")
        all_grants.extend(grants)

    # Deduplicate by resolution number, keeping the latest (most recent date)
    if deduplicate:
        original_count = len(all_grants)
        resolution_map = {}
        for grant in all_grants:
            existing = resolution_map.get(grant.resolution)
            if existing is None or grant.meeting_date > existing.meeting_date:
                resolution_map[grant.resolution] = grant
        all_grants = list(resolution_map.values())
        dup_count = original_count - len(all_grants)
        if dup_count > 0:
            print(f"Deduplicated {dup_count} duplicate resolutions (kept latest dates)")

    print(f"\nTotal grants from minutes: {len(all_grants)}")
    return all_grants


if __name__ == "__main__":
    grants = parse_all_minutes()
    for g in grants[:5]:  # Show first 5
        print(f"  {g.resolution}: {g.property_name} ({g.legal_entity}) - {g.city}, {g.county}")
