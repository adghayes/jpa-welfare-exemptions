#!/usr/bin/env python3
"""
Re-categorize San Diego tax rates from the scraped JSON data.

The original categorization incorrectly included the base 1% levy in other_rate
and missed some school district patterns.

San Diego County fund patterns:
- "COUNTY-PROPOSITION 13" (fund 501800): Base 1% levy - EXCLUDE from breakdown
- "GEN BOND [CITY]": City general obligation bonds -> city_rate
- "UNIF BOND [DISTRICT]": Unified School District bonds -> school_rate
- "HI BOND [DISTRICT]": High School bonds -> school_rate
- "ELEM": Elementary school -> school_rate
- "COMM COLL": Community College -> school_rate
- "MWD": Metropolitan Water District -> other_rate
- Everything else -> other_rate
"""

import csv
import json
import re
from pathlib import Path

INPUT_FILE = Path(__file__).parent.parent / "output" / "san_diego_tax_rates.json"
OUTPUT_FILE = Path(__file__).parent.parent / "output" / "find_parcels" / "tra_summary.csv"

# Map of city names to their bond name patterns
CITY_BOND_PATTERNS = {
    "Carlsbad": ["CARLSBAD"],
    "Chula Vista": ["CHULA VISTA"],
    "Coronado": ["CORONADO"],
    "Del Mar": ["DEL MAR"],
    "El Cajon": ["EL CAJON"],
    "Encinitas": ["ENCINITAS"],
    "Escondido": ["ESCONDIDO"],
    "Imperial Beach": ["IMPERIAL BEACH"],
    "La Mesa": ["LA MESA"],
    "Lemon Grove": ["LEMON GROVE"],
    "National City": ["NATIONAL CITY"],
    "Oceanside": ["OCEANSIDE"],
    "Poway": ["POWAY"],
    "San Diego": ["SAN DIEGO"],
    "San Marcos": ["SAN MARCOS"],
    "Santee": ["SANTEE"],
    "Solana Beach": ["SOLANA BEACH"],
    "Vista": ["VISTA"],
}


def categorize_fund(fund: dict, city: str) -> str:
    """
    Categorize a fund into: base, city, school, or other.
    """
    desc = fund["desc"].upper()
    fund_code = fund["fund"]

    # Base 1% levy - exclude from breakdown
    if fund_code == "501800" or "PROPOSITION 13" in desc:
        return "base"

    # School districts - look for these patterns:
    # - "UNIF BOND" = Unified School District
    # - "HI BOND" = High School District
    # - "ELEM" = Elementary
    # - "COMM COLL" = Community College
    if "UNIF BOND" in desc:
        return "school"
    if "HI BOND" in desc:
        return "school"
    if "ELEM" in desc:
        return "school"
    if "COMM COLL" in desc:
        return "school"

    # City bonds - look for "GEN BOND [CITY]" (not UNIF BOND which is school)
    # Also look for city-specific patterns that aren't schools
    city_patterns = CITY_BOND_PATTERNS.get(city, [city.upper()])
    if "GEN BOND" in desc:
        for pattern in city_patterns:
            if pattern in desc:
                return "city"

    # Everything else (water, misc) -> other
    return "other"


def process_city(city_data: dict) -> dict:
    """
    Process a single city's fund data and return categorized rates.
    """
    city = city_data["city"]
    total = city_data["total"]

    city_rate = 0.0
    school_rate = 0.0
    other_rate = 0.0

    for fund in city_data["funds"]:
        category = categorize_fund(fund, city)
        rate = fund["rate"]

        if category == "city":
            city_rate += rate
        elif category == "school":
            school_rate += rate
        elif category == "other":
            other_rate += rate
        # base is excluded

    return {
        "city": city,
        "total_rate": round(total, 5),
        "city_rate": round(city_rate, 5),
        "school_rate": round(school_rate, 5),
        "other_rate": round(other_rate, 5),
    }


def main():
    # Load scraped data
    with open(INPUT_FILE) as f:
        data = json.load(f)

    print(f"Processing {len(data)} cities from San Diego County")
    print("=" * 70)

    results = []
    for city_data in data:
        result = process_city(city_data)
        results.append(result)

        # Show breakdown
        print(f"{result['city']}: total={result['total_rate']:.4f}, "
              f"city={result['city_rate']:.4f}, school={result['school_rate']:.4f}, "
              f"other={result['other_rate']:.4f}")

    # Read existing tra_summary.csv to remove old San Diego entries
    existing_rows = []
    san_diego_cities = set(r["city"] for r in results)

    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Keep rows that aren't San Diego cities
                if row['tra'] not in san_diego_cities:
                    existing_rows.append(row)

    # Write updated file
    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['tra', 'total_rate', 'city_rate', 'school_rate', 'other_rate'])

        # Write existing non-San Diego rows
        for row in existing_rows:
            writer.writerow([
                row['tra'],
                row['total_rate'],
                row['city_rate'],
                row['school_rate'],
                row['other_rate']
            ])

        # Write new San Diego rows
        for result in sorted(results, key=lambda x: x['city']):
            writer.writerow([
                result['city'],
                result['total_rate'],
                result['city_rate'],
                result['school_rate'],
                result['other_rate'],
            ])

    print()
    print("=" * 70)
    print(f"Updated {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
