#!/usr/bin/env python3
"""
CMFA Welfare Tax Exemption Data Verification Tool

Usage:
    python scripts/cmfa_scrape.py download   # Download all PDFs
    python scripts/cmfa_scrape.py compare    # Compare against CSV (fast, minutes only)
    python scripts/cmfa_scrape.py all        # Download + compare
"""

import argparse
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cmfa_scraping.scraper import download_all_documents
from src.cmfa_scraping.minutes_parser import parse_all_minutes
from src.cmfa_scraping.matcher import load_csv, match_minutes_to_csv, find_unmatched_csv_rows
from src.cmfa_scraping.report import generate_reports_from_minutes


def cmd_download(args):
    """Download all meeting documents."""
    print("=" * 60)
    print("STEP 1: Downloading PDFs")
    print("=" * 60)

    stats = download_all_documents(
        output_dir=args.pdf_dir,
        min_year=args.min_year,
        doc_types=['minutes']  # Only download minutes for fast mode
    )

    print(f"\nDownload complete!")
    return stats


def cmd_compare(args):
    """Compare parsed data against CSV (fast mode - minutes only)."""
    print("=" * 60)
    print("CMFA Welfare Tax Exemption Verification")
    print("=" * 60)

    start_time = time.time()

    # Step 1: Parse minutes
    print("\n--- Parsing Meeting Minutes ---")
    minutes_grants = parse_all_minutes(args.pdf_dir)

    parse_time = time.time()
    print(f"Parsing took {parse_time - start_time:.1f}s")

    # Step 2: Load CSV
    print(f"\nLoading CSV: {args.csv_path}")
    csv_df = load_csv(args.csv_path)
    print(f"CSV rows: {len(csv_df)}")

    # Step 3: Match
    print("\n--- Matching grants to CSV ---")
    match_results, unmatched_pdf = match_minutes_to_csv(minutes_grants, csv_df)

    unmatched_csv = find_unmatched_csv_rows(csv_df, match_results)

    matched_count = sum(1 for r in match_results if r.match_type != 'no_match')
    exact_count = sum(1 for r in match_results if r.match_type == 'exact')
    fuzzy_count = sum(1 for r in match_results if r.match_type == 'fuzzy')

    print(f"\nMatched: {matched_count} ({exact_count} exact, {fuzzy_count} fuzzy)")
    print(f"New in PDF (not in CSV): {len(unmatched_pdf)}")
    print(f"In CSV but not PDF: {len(unmatched_csv)}")

    # Step 4: Generate reports
    print("\n--- Generating Reports ---")
    report_stats = generate_reports_from_minutes(
        match_results=match_results,
        unmatched_pdf_grants=unmatched_pdf,
        unmatched_csv_df=unmatched_csv,
        output_dir=args.output_dir,
        full_csv_df=csv_df,  # Pass full CSV for duplicate checking
        all_pdf_grants=minutes_grants  # Pass all PDF grants for property-name matching
    )

    total_time = time.time() - start_time
    print(f"\nReports generated in: {args.output_dir}/")
    for name, path in report_stats.get('reports', {}).items():
        print(f"  - {Path(path).name}")

    print(f"\nTotal time: {total_time:.1f}s")

    # Final summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"PDF grants found:         {len(minutes_grants)}")
    print(f"CSV rows:                 {len(csv_df)}")
    print(f"Exact matches:            {exact_count}")
    print(f"Fuzzy matches:            {fuzzy_count}")
    print(f"New grants (in PDF only): {len(unmatched_pdf)}")
    print(f"Missing (in CSV only):    {len(unmatched_csv)}")

    return report_stats


def cmd_all(args):
    """Run full pipeline: download + compare."""
    # Step 1: Download
    cmd_download(args)

    # Step 2: Compare
    print()
    return cmd_compare(args)


def main():
    parser = argparse.ArgumentParser(
        description="CMFA Welfare Tax Exemption Data Verification Tool"
    )
    parser.add_argument(
        'command',
        choices=['download', 'compare', 'all'],
        help="Command to run"
    )
    parser.add_argument(
        '--pdf-dir',
        default='data/cmfa_scraping/pdfs',
        help="Directory for PDF files (default: data/cmfa_scraping/pdfs)"
    )
    parser.add_argument(
        '--csv-path',
        default='input/grants.csv',
        help="Path to the CSV file (default: input/grants.csv)"
    )
    parser.add_argument(
        '--output-dir',
        default='output/cmfa_scraping',
        help="Output directory for reports (default: output/cmfa_scraping)"
    )
    parser.add_argument(
        '--min-year',
        type=int,
        default=2023,
        help="Minimum year to process (default: 2023)"
    )

    args = parser.parse_args()

    commands = {
        'download': cmd_download,
        'compare': cmd_compare,
        'all': cmd_all,
    }

    try:
        result = commands[args.command](args)
        return 0
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
