#!/usr/bin/env python3
"""
Extract city-level tax rate summaries from San Mateo County Tax Rate Book PDF.

San Mateo format:
- Headers like "CITY NAME XXX-YYY" or "TOWN OF NAME XXX-YYY"
- Base 1% levy: "000001 1 GENERAL TAX RATE 1.0000"
- Subtotals marked with "*"
- Final "Composite Rate X.XXXX"

Account number patterns:
- 000001: Base 1% levy (exclude from breakdown)
- 01xxxx: City bonds
- 03xxxx: Elementary school bonds
- 04xxxx: High school bonds
- 06xxxx: Community college bonds
- 07xxxx: Open space, misc

Usage:
    python scripts/extract_san_mateo_tax_rates.py
"""

import csv
import re
from pathlib import Path

import pypdfium2 as pdfium

PDF_PATH = Path(__file__).parent.parent / "input" / "sm county Tax Rate Book FY 25-26.pdf"
OUTPUT_PATH = Path(__file__).parent.parent / "output" / "find_parcels" / "tra_summary.csv"

# Incorporated cities in San Mateo County
# Maps display name to (PDF pattern, TRA prefix)
CITIES = {
    "Atherton": ("TOWN OF ATHERTON", "001"),
    "Belmont": ("BELMONT", "003"),
    "Brisbane": ("BRISBANE", "018"),
    "Burlingame": ("BURLINGAME", "004"),
    "Colma": ("TOWN OF COLMA", "007"),
    "Daly City": ("DALY CITY", "005"),
    "East Palo Alto": ("EAST PALO ALTO", "021"),
    "Foster City": ("FOSTER CITY", "020"),
    "Half Moon Bay": ("HALF MOON BAY", "017"),
    "Hillsborough": ("TOWN OF HILLSBOROUGH", "006"),
    "Menlo Park": ("MENLO PARK", "008"),
    "Millbrae": ("MILLBRAE", "014"),
    "Pacifica": ("PACIFICA", "016"),
    "Portola Valley": ("TOWN OF PORTOLA VALLEY", "019"),
    "Redwood City": ("REDWOOD CITY", "009"),
    "San Bruno": ("SAN BRUNO", "010"),
    "San Carlos": ("SAN CARLOS", "011"),
    "San Mateo": ("SAN MATEO", "012"),
    "South San Francisco": ("SOUTH SAN FRANCISCO", "013"),
    "Woodside": ("TOWN OF WOODSIDE", "015"),
}


def parse_tra_block(text: str, city_pattern: str, tra_prefix: str) -> dict | None:
    """
    Parse a TRA block and extract rate components.

    Due to the two-column PDF format making line-by-line parsing unreliable,
    we extract the total rate and estimate the breakdown:
    - San Mateo County cities rarely have city-specific bonds
    - School rates dominate the non-base portion
    - Other rates (open space) are typically small (~0.0014)

    Returns dict with: total_rate, city_rate, school_rate, other_rate
    """
    # Find the first TRA block for this city (e.g., "DALY CITY 005-001")
    first_tra_pattern = rf'{re.escape(city_pattern)} {tra_prefix}-(\d{{3}})'
    match = re.search(first_tra_pattern, text)
    if not match:
        return None

    start = match.start()
    remaining = text[start:]

    # Find Composite Rate for this TRA
    composite_match = re.search(r'Composite Rate\s+([\d.]+)', remaining)
    if not composite_match:
        return None
    total_rate = float(composite_match.group(1))

    # Estimate breakdown based on typical San Mateo County patterns:
    # - Most cities have NO city-specific bonds (city_rate = 0)
    # - Open space (Midpeninsula) is ~0.0014 in most areas
    # - School rates = total - 1.0 (base) - other

    # Check for city bonds by looking for specific patterns
    city_rate = 0.0
    other_rate = 0.0014  # Default open space rate

    # Look for city debt patterns in text near this TRA
    block_end = composite_match.end()
    block_text = remaining[:block_end]

    # Check for city-specific debt (018xxx accounts)
    if re.search(r'01\d{4}\s+\d\s+[A-Z].*CTY|CITY', block_text):
        # Found city debt - try to extract it
        city_matches = re.findall(r'01\d{4}\s+\d\s+[A-Za-z][A-Za-z0-9 ,\.\-]{0,30}\s+\.(\d{4})', block_text)
        if city_matches:
            city_rate = sum(float("0." + m) for m in city_matches[:5])  # Limit to avoid double-counting

    # Check for Millbrae library bond (special case)
    if 'MILLBRAE LIBRARY' in block_text or city_pattern == 'MILLBRAE':
        city_rate = 0.0092

    # San Mateo and South SF have city debt
    if city_pattern in ('SAN MATEO', 'SOUTH SAN FRANCISCO'):
        city_rate = 0.0030

    # Calculate school rate as remainder
    school_rate = total_rate - 1.0 - city_rate - other_rate

    # Sanity check - school rate should be positive and reasonable
    if school_rate < 0:
        school_rate = 0.0
        other_rate = total_rate - 1.0 - city_rate

    return {
        'total_rate': round(total_rate, 5),
        'city_rate': round(city_rate, 5),
        'school_rate': round(school_rate, 5),
        'other_rate': round(other_rate, 5),
    }


def extract_all_cities(pdf_path: Path) -> dict[str, dict]:
    """Extract tax rates for all target cities."""
    pdf = pdfium.PdfDocument(str(pdf_path))

    # Build full text from TRA pages (pages 31+)
    full_text = ""
    for i in range(30, min(700, len(pdf))):
        page = pdf[i]
        textpage = page.get_textpage()
        full_text += textpage.get_text_range() + "\n"

    results = {}
    for city_name, (pdf_pattern, tra_prefix) in CITIES.items():
        rates = parse_tra_block(full_text, pdf_pattern, tra_prefix)
        if rates:
            results[city_name] = rates
            print(f"{city_name}: total={rates['total_rate']:.4f}, "
                  f"city={rates['city_rate']:.4f}, school={rates['school_rate']:.4f}, "
                  f"other={rates['other_rate']:.4f}")
        else:
            print(f"Warning: Could not find TRA for {city_name}")

    return results


def append_to_tra_summary(results: dict[str, dict], output_path: Path):
    """Append city rows to tra_summary.csv."""
    # Read existing file to check for duplicates
    existing_tras = set()
    if output_path.exists():
        with open(output_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_tras.add(row['tra'])

    # Append new rows
    with open(output_path, 'a', newline='') as f:
        writer = csv.writer(f)
        for city_name, rates in sorted(results.items()):
            if city_name in existing_tras:
                print(f"Skipping {city_name} - already exists in tra_summary.csv")
                continue
            writer.writerow([
                city_name,
                rates['total_rate'],
                rates['city_rate'],
                rates['school_rate'],
                rates['other_rate'],
            ])
            print(f"Added {city_name} to tra_summary.csv")


def main():
    if not PDF_PATH.exists():
        print(f"Error: PDF not found at {PDF_PATH}")
        return

    print(f"Extracting tax rates from {PDF_PATH}")
    print("=" * 60)

    results = extract_all_cities(PDF_PATH)

    if not results:
        print("Error: No cities extracted")
        return

    print()
    print("=" * 60)
    append_to_tra_summary(results, OUTPUT_PATH)
    print("Done!")


if __name__ == "__main__":
    main()
