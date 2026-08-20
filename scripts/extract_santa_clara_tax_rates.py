#!/usr/bin/env python3
"""
Extract city-level tax rate summaries from Santa Clara County Tax Rate Book PDF.

Usage:
    python scripts/extract_santa_clara_tax_rates.py
"""

import csv
import re
import sys
from pathlib import Path

import pypdfium2 as pdfium

PDF_PATH = Path(__file__).parent.parent / "input" / "tax-rate-book-2025-2026.pdf"
OUTPUT_PATH = Path(__file__).parent.parent / "output" / "find_parcels" / "tra_summary.csv"

# All incorporated cities in Santa Clara County
# Maps city name to (PDF name pattern, TRA code prefix)
CITIES = {
    "Gilroy": ("GILROY CITY", "002"),
    "Los Gatos": ("TOWN OF LOS GATOS", "003"),
    "Morgan Hill": ("MORGAN HILL CITY", "004"),
    "Mountain View": ("MT VIEW CITY", "005"),
    "Palo Alto": ("PALO ALTO CITY", "006"),
    "Santa Clara": ("SANTA CLARA CITY", "007"),
    "Sunnyvale": ("SUNNYVALE CITY", "009"),
    "Campbell": ("CAMPBELL CITY", "010"),
    "Los Altos": ("LOS ALTOS CITY", "011"),
    "Milpitas": ("MILPITAS CITY", "012"),
    "Cupertino": ("CUPERTINO CITY", "013"),
    "Los Altos Hills": ("LOS ALTOS HILLS CITY", "014"),
    "Saratoga": ("SARATOGA CITY", "015"),
    "Monte Sereno": ("MONTE SERENO CITY", "016"),
    "San Jose": ("SAN JOSE CITY", "017"),
}


def parse_tra_block(text: str, city_pattern: str, tra_prefix: str) -> dict | None:
    """
    Parse a TRA block from the PDF text and extract rate components.

    Returns dict with: total_rate, city_rate, school_rate, other_rate
    """
    # Find the first TRA block for this city
    pattern = rf'{city_pattern}\s+{tra_prefix}-\d{{3}}'
    match = re.search(pattern, text)
    if not match:
        return None

    # Extract from match position - we'll stop after we see two ** totals
    start = match.start()
    remaining = text[start:]
    lines = remaining.split('\n')

    # Parse line items - stop after second ** total
    city_rate = 0.0
    school_rate = 0.0
    other_rate = 0.0
    totals = []  # Will collect both ** totals

    for line in lines:
        line = line.strip()

        # Grand totals marked with **
        if '**' in line:
            rate_match = re.search(r'(\d+\.\d+)\s*\*\*', line)
            if rate_match:
                totals.append(float(rate_match.group(1)))
                # Stop after we get both totals (main rate + water district)
                if len(totals) >= 2:
                    break
            continue

        # Skip subtotals marked with single *
        if line.endswith('*'):
            continue

        # Parse district lines
        parts = line.split()
        if len(parts) >= 3:
            try:
                account = parts[0]
                rate = float(parts[-1])
                district_name = ' '.join(parts[1:-1]).upper()

                # Categorize by account number and name
                if account.isdigit():
                    acct_num = int(account)

                    # City bonds (look for city name in district)
                    if 'CITY' in district_name and 'BOND' in district_name:
                        city_rate += rate
                    # School rates: elem, high, unified, college (accounts 11xxx-15xxx)
                    elif 11000 <= acct_num < 16000:
                        school_rate += rate
                    # Skip base levy (00001) and county retirement (00020)
                    elif acct_num in (1, 20):
                        pass
                    # Water district (77001) - include in other
                    elif acct_num == 77001:
                        other_rate += rate
                    # Everything else (hospital, open space, county bonds, library)
                    elif acct_num not in (1, 20):
                        other_rate += rate
            except (ValueError, IndexError):
                pass

    if len(totals) < 2:
        print(f"Warning: Expected 2 totals, found {len(totals)} for {city_pattern}")
        return None

    # Total rate = sum of both ** totals (main rate + water district)
    total_rate = totals[0] + totals[1]

    return {
        'total_rate': round(total_rate, 6),
        'city_rate': round(city_rate, 6),
        'school_rate': round(school_rate, 6),
        'other_rate': round(other_rate, 6),
    }


def extract_all_cities(pdf_path: Path) -> dict[str, dict]:
    """Extract tax rates for all target cities."""
    pdf = pdfium.PdfDocument(str(pdf_path))

    # Build full text from TRA pages (pages 13-200)
    full_text = ""
    for i in range(12, 200):
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
        sys.exit(1)

    print(f"Extracting tax rates from {PDF_PATH}")
    print("=" * 60)

    results = extract_all_cities(PDF_PATH)

    if not results:
        print("Error: No cities extracted")
        sys.exit(1)

    print()
    print("=" * 60)
    append_to_tra_summary(results, OUTPUT_PATH)
    print("Done!")


if __name__ == "__main__":
    main()
