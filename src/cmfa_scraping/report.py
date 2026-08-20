"""
Report Generator

Creates diff reports comparing PDF-extracted data to the CSV.
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .matcher import MatchResult, CombinedGrant
from .minutes_parser import MinutesGrant


def generate_reports(match_results: list[MatchResult],
                     unmatched_pdf_grants: list[CombinedGrant],
                     unmatched_csv_df: pd.DataFrame,
                     output_dir: str = "output") -> dict:
    """
    Generate all reports.

    Args:
        match_results: Results from matching PDF to CSV
        unmatched_pdf_grants: Grants in PDF but not CSV
        unmatched_csv_df: CSV rows not matched to any PDF
        output_dir: Output directory

    Returns:
        Summary statistics dict
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Generate individual reports
    diff_report_path = generate_diff_report(match_results, output_path)
    new_grants_path = generate_new_grants_report(unmatched_pdf_grants, output_path)
    missing_pdf_path = generate_missing_pdf_report(unmatched_csv_df, output_path)
    summary_path = generate_summary_report(
        match_results, unmatched_pdf_grants, unmatched_csv_df, output_path
    )

    stats = calculate_stats(match_results, unmatched_pdf_grants, unmatched_csv_df)
    stats['reports'] = {
        'diff_report': str(diff_report_path),
        'new_grants': str(new_grants_path),
        'missing_pdf': str(missing_pdf_path),
        'summary': str(summary_path)
    }

    return stats


def generate_diff_report(match_results: list[MatchResult], output_path: Path) -> Path:
    """Generate CSV report of field-level differences."""
    rows = []

    for result in match_results:
        if result.match_type == "no_match":
            continue

        for field_name, (csv_val, pdf_val) in result.field_differences.items():
            rows.append({
                'Resolution': result.pdf_resolution or result.csv_resolution,
                'Property Name (CSV)': result.property_name_csv,
                'Property Name (PDF)': result.property_name_pdf,
                'Match Type': result.match_type,
                'Match Confidence': f"{result.match_confidence:.1f}%",
                'Field': field_name,
                'CSV Value': csv_val,
                'PDF Value': pdf_val,
            })

    df = pd.DataFrame(rows)
    output_file = output_path / "diff_report.csv"
    df.to_csv(output_file, index=False)

    return output_file


def generate_new_grants_report(grants: list[CombinedGrant], output_path: Path) -> Path:
    """Generate report of grants found in PDFs but not in CSV."""
    rows = []

    for grant in grants:
        rows.append({
            'Resolution': grant.resolution,
            'Property Name': grant.property_name,
            'Legal Entity': grant.legal_entity,
            'Nonprofit Partner': grant.nonprofit_partner,
            'City': grant.city,
            'County': grant.county,
            'Address': grant.address,
            'Total Units': grant.total_units,
            'Restricted Units': grant.restricted_units,
            'Meeting Date': grant.meeting_date.strftime('%Y-%m-%d'),
            'Source (Minutes)': grant.source_minutes,
            'Source (Staff Report)': grant.source_staff_report,
        })

    df = pd.DataFrame(rows)
    output_file = output_path / "new_grants.csv"
    df.to_csv(output_file, index=False)

    return output_file


def generate_missing_pdf_report(unmatched_df: pd.DataFrame, output_path: Path,
                                 pdf_grants: list[MinutesGrant] = None) -> Path:
    """Generate report of CSV rows not found in any PDF.

    If pdf_grants is provided, checks for potential matches by property name
    and splits into truly_missing vs potential_match files.
    """
    from fuzzywuzzy import fuzz
    from .matcher import normalize_property_name

    # Select relevant columns
    columns = [
        'Resolution', 'Property Name', 'Applicant / Entity', 'City', 'County',
        'Date', 'TOTAL UNITS', 'Rent Restricted'
    ]
    available_cols = [c for c in columns if c in unmatched_df.columns]

    truly_missing = []
    potential_matches = []

    for _, row in unmatched_df.iterrows():
        row_dict = {col: row.get(col) for col in available_cols}

        # Check for potential match by property name
        potential_match = None
        if pdf_grants is not None and 'Property Name' in unmatched_df.columns:
            csv_name = normalize_property_name(str(row.get('Property Name', '')))
            best_score = 0
            for grant in pdf_grants:
                pdf_name = normalize_property_name(grant.property_name)
                score = fuzz.ratio(csv_name, pdf_name)
                if score > best_score and score >= 80:
                    best_score = score
                    potential_match = {
                        'pdf_resolution': grant.resolution,
                        'pdf_property': grant.property_name,
                        'pdf_date': grant.meeting_date.strftime('%Y-%m-%d'),
                        'match_score': score
                    }

        if potential_match:
            row_dict['PDF Resolution'] = potential_match['pdf_resolution']
            row_dict['PDF Property'] = potential_match['pdf_property']
            row_dict['PDF Date'] = potential_match['pdf_date']
            row_dict['Match Score'] = f"{potential_match['match_score']}%"
            potential_matches.append(row_dict)
        else:
            truly_missing.append(row_dict)

    # Write combined report (original behavior)
    output_df = unmatched_df[available_cols].copy()
    output_file = output_path / "missing_from_pdf.csv"
    output_df.to_csv(output_file, index=False)

    # Also write separate files for clarity
    if truly_missing:
        truly_missing_df = pd.DataFrame(truly_missing)
        truly_missing_df.to_csv(output_path / "truly_missing_from_pdf.csv", index=False)

    if potential_matches:
        potential_df = pd.DataFrame(potential_matches)
        potential_df.to_csv(output_path / "potential_match_missing.csv", index=False)

    return output_file


def generate_summary_report(match_results: list[MatchResult],
                            unmatched_pdf: list[CombinedGrant],
                            unmatched_csv: pd.DataFrame,
                            output_path: Path) -> Path:
    """Generate human-readable summary report."""
    stats = calculate_stats(match_results, unmatched_pdf, unmatched_csv)

    lines = [
        "=" * 60,
        "CMFA Welfare Tax Exemption Data Verification Summary",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        "",
        "MATCHING STATISTICS",
        "-" * 40,
        f"Total grants in PDFs:        {stats['total_pdf_grants']}",
        f"Total rows in CSV:           {stats['total_csv_rows']}",
        "",
        f"Exact matches:               {stats['exact_matches']}",
        f"Fuzzy matches:               {stats['fuzzy_matches']}",
        f"Total matched:               {stats['total_matched']}",
        "",
        f"NEW in PDF (not in CSV):     {stats['new_in_pdf']}",
        f"MISSING PDF (in CSV only):   {stats['missing_from_pdf']}",
        "",
        "DISCREPANCIES",
        "-" * 40,
        f"Records with differences:    {stats['records_with_diffs']}",
        f"Total field differences:     {stats['total_field_diffs']}",
        "",
    ]

    # Field-level breakdown
    if stats['field_diff_breakdown']:
        lines.append("Differences by field:")
        for field, count in sorted(stats['field_diff_breakdown'].items(),
                                   key=lambda x: -x[1]):
            lines.append(f"  {field}: {count}")
        lines.append("")

    # Low confidence matches
    low_conf_matches = [r for r in match_results
                        if r.match_type == "fuzzy" and r.match_confidence < 80]
    if low_conf_matches:
        lines.append("LOW CONFIDENCE MATCHES (< 80%)")
        lines.append("-" * 40)
        for r in low_conf_matches[:10]:  # Show first 10
            lines.append(f"  {r.pdf_resolution}: {r.property_name_pdf}")
            lines.append(f"    -> {r.property_name_csv} ({r.match_confidence:.1f}%)")
        if len(low_conf_matches) > 10:
            lines.append(f"  ... and {len(low_conf_matches) - 10} more")
        lines.append("")

    lines.append("=" * 60)
    lines.append("See individual reports for details:")
    lines.append("  - diff_report.csv: Field-level differences")
    lines.append("  - new_grants.csv: Grants found in PDFs but not CSV")
    lines.append("  - missing_from_pdf.csv: CSV rows not found in PDFs")
    lines.append("=" * 60)

    output_file = output_path / "summary.txt"
    output_file.write_text('\n'.join(lines))

    # Also save as JSON for programmatic use
    json_file = output_path / "summary.json"
    json_file.write_text(json.dumps(stats, indent=2, default=str))

    return output_file


def calculate_stats(match_results: list[MatchResult],
                    unmatched_pdf: list[CombinedGrant],
                    unmatched_csv: pd.DataFrame) -> dict:
    """Calculate summary statistics."""
    exact_matches = sum(1 for r in match_results if r.match_type == "exact")
    fuzzy_matches = sum(1 for r in match_results if r.match_type == "fuzzy")
    no_matches = sum(1 for r in match_results if r.match_type == "no_match")

    records_with_diffs = sum(1 for r in match_results if r.field_differences)
    total_field_diffs = sum(len(r.field_differences) for r in match_results)

    # Field-level breakdown
    field_breakdown = {}
    for r in match_results:
        for field in r.field_differences:
            field_breakdown[field] = field_breakdown.get(field, 0) + 1

    return {
        'total_pdf_grants': len(match_results),
        'total_csv_rows': len(match_results) - no_matches + len(unmatched_csv),
        'exact_matches': exact_matches,
        'fuzzy_matches': fuzzy_matches,
        'total_matched': exact_matches + fuzzy_matches,
        'new_in_pdf': len(unmatched_pdf),
        'missing_from_pdf': len(unmatched_csv),
        'records_with_diffs': records_with_diffs,
        'total_field_diffs': total_field_diffs,
        'field_diff_breakdown': field_breakdown,
    }


def generate_reports_from_minutes(match_results: list[MatchResult],
                                   unmatched_pdf_grants: list[MinutesGrant],
                                   unmatched_csv_df: pd.DataFrame,
                                   output_dir: str = "output",
                                   full_csv_df: pd.DataFrame = None,
                                   all_pdf_grants: list[MinutesGrant] = None) -> dict:
    """
    Generate reports from minutes-only matching (fast mode).

    Args:
        match_results: Results from matching PDF to CSV
        unmatched_pdf_grants: MinutesGrant objects not in CSV
        unmatched_csv_df: CSV rows not matched to any PDF
        output_dir: Output directory
        full_csv_df: Full CSV dataframe for duplicate checking
        all_pdf_grants: All parsed PDF grants for property-name matching

    Returns:
        Summary statistics dict
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Generate individual reports
    diff_report_path = generate_diff_report(match_results, output_path)
    new_grants_path = generate_new_grants_report_from_minutes(
        unmatched_pdf_grants, output_path, csv_df=full_csv_df
    )
    missing_pdf_path = generate_missing_pdf_report(
        unmatched_csv_df, output_path, pdf_grants=all_pdf_grants
    )
    summary_path = generate_summary_report_from_minutes(
        match_results, unmatched_pdf_grants, unmatched_csv_df, output_path
    )

    stats = calculate_stats_from_minutes(match_results, unmatched_pdf_grants, unmatched_csv_df)
    stats['reports'] = {
        'diff_report': str(diff_report_path),
        'new_grants': str(new_grants_path),
        'missing_pdf': str(missing_pdf_path),
        'summary': str(summary_path)
    }

    return stats


def generate_new_grants_report_from_minutes(grants: list[MinutesGrant], output_path: Path,
                                            csv_df: pd.DataFrame = None) -> Path:
    """Generate report of grants found in PDFs but not in CSV (minutes-only mode).

    Also checks for potential duplicates by fuzzy matching property names.
    """
    from fuzzywuzzy import fuzz
    from .matcher import normalize_property_name

    rows = []
    truly_new = []
    potential_duplicates = []

    for grant in grants:
        row = {
            'Resolution': grant.resolution,
            'Property Name': grant.property_name,
            'Legal Entity': grant.legal_entity,
            'City': grant.city,
            'County': grant.county,
            'Meeting Date': grant.meeting_date.strftime('%Y-%m-%d'),
            'Source File': grant.source_file,
        }

        # Check for potential duplicate in CSV by property name (ignoring date)
        potential_match = None
        if csv_df is not None and 'Property Name' in csv_df.columns:
            pdf_name = normalize_property_name(grant.property_name)
            best_score = 0
            for idx, csv_row in csv_df.iterrows():
                csv_name = normalize_property_name(str(csv_row.get('Property Name', '')))
                score = fuzz.ratio(pdf_name, csv_name)
                if score > best_score and score >= 80:
                    best_score = score
                    potential_match = {
                        'csv_resolution': csv_row.get('Resolution', ''),
                        'csv_property': csv_row.get('Property Name', ''),
                        'csv_date': str(csv_row.get('Date', '')),
                        'match_score': score
                    }

        if potential_match:
            row['Potential CSV Match'] = potential_match['csv_property']
            row['CSV Resolution'] = potential_match['csv_resolution']
            row['CSV Date'] = potential_match['csv_date']
            row['Match Score'] = f"{potential_match['match_score']}%"
            potential_duplicates.append(row)
        else:
            row['Potential CSV Match'] = ''
            row['CSV Resolution'] = ''
            row['CSV Date'] = ''
            row['Match Score'] = ''
            truly_new.append(row)

        rows.append(row)

    # Write combined report
    df = pd.DataFrame(rows)
    output_file = output_path / "new_grants.csv"
    df.to_csv(output_file, index=False)

    # Also write separate files for clarity
    if truly_new:
        truly_new_df = pd.DataFrame(truly_new)
        truly_new_df.to_csv(output_path / "truly_new_grants.csv", index=False)

    if potential_duplicates:
        dup_df = pd.DataFrame(potential_duplicates)
        dup_df.to_csv(output_path / "potential_duplicate_grants.csv", index=False)

    return output_file


def generate_summary_report_from_minutes(match_results: list[MatchResult],
                                          unmatched_pdf: list[MinutesGrant],
                                          unmatched_csv: pd.DataFrame,
                                          output_path: Path) -> Path:
    """Generate human-readable summary report (minutes-only mode)."""
    stats = calculate_stats_from_minutes(match_results, unmatched_pdf, unmatched_csv)

    lines = [
        "=" * 60,
        "CMFA Welfare Tax Exemption Data Verification Summary",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        "",
        "MATCHING STATISTICS",
        "-" * 40,
        f"Total grants in PDFs:        {stats['total_pdf_grants']}",
        f"Total rows in CSV:           {stats['total_csv_rows']}",
        "",
        f"Exact matches:               {stats['exact_matches']}",
        f"Fuzzy matches:               {stats['fuzzy_matches']}",
        f"Total matched:               {stats['total_matched']}",
        "",
        f"NEW in PDF (not in CSV):     {stats['new_in_pdf']}",
        f"MISSING PDF (in CSV only):   {stats['missing_from_pdf']}",
        "",
        "DISCREPANCIES",
        "-" * 40,
        f"Records with differences:    {stats['records_with_diffs']}",
        f"Total field differences:     {stats['total_field_diffs']}",
        "",
    ]

    # Field-level breakdown
    if stats['field_diff_breakdown']:
        lines.append("Differences by field:")
        for field, count in sorted(stats['field_diff_breakdown'].items(),
                                   key=lambda x: -x[1]):
            lines.append(f"  {field}: {count}")
        lines.append("")

    # Low confidence matches
    low_conf_matches = [r for r in match_results
                        if r.match_type == "fuzzy" and r.match_confidence < 80]
    if low_conf_matches:
        lines.append("LOW CONFIDENCE MATCHES (< 80%)")
        lines.append("-" * 40)
        for r in low_conf_matches[:10]:  # Show first 10
            lines.append(f"  {r.pdf_resolution}: {r.property_name_pdf}")
            lines.append(f"    -> {r.property_name_csv} ({r.match_confidence:.1f}%)")
        if len(low_conf_matches) > 10:
            lines.append(f"  ... and {len(low_conf_matches) - 10} more")
        lines.append("")

    lines.append("=" * 60)
    lines.append("See individual reports for details:")
    lines.append("  - diff_report.csv: Field-level differences")
    lines.append("  - new_grants.csv: Grants found in PDFs but not CSV")
    lines.append("  - missing_from_pdf.csv: CSV rows not found in PDFs")
    lines.append("=" * 60)

    output_file = output_path / "summary.txt"
    output_file.write_text('\n'.join(lines))

    # Also save as JSON for programmatic use
    json_file = output_path / "summary.json"
    json_file.write_text(json.dumps(stats, indent=2, default=str))

    return output_file


def calculate_stats_from_minutes(match_results: list[MatchResult],
                                  unmatched_pdf: list[MinutesGrant],
                                  unmatched_csv: pd.DataFrame) -> dict:
    """Calculate summary statistics (minutes-only mode)."""
    exact_matches = sum(1 for r in match_results if r.match_type == "exact")
    fuzzy_matches = sum(1 for r in match_results if r.match_type == "fuzzy")
    no_matches = sum(1 for r in match_results if r.match_type == "no_match")

    records_with_diffs = sum(1 for r in match_results if r.field_differences)
    total_field_diffs = sum(len(r.field_differences) for r in match_results)

    # Field-level breakdown
    field_breakdown = {}
    for r in match_results:
        for field in r.field_differences:
            field_breakdown[field] = field_breakdown.get(field, 0) + 1

    return {
        'total_pdf_grants': len(match_results),
        'total_csv_rows': len(match_results) - no_matches + len(unmatched_csv),
        'exact_matches': exact_matches,
        'fuzzy_matches': fuzzy_matches,
        'total_matched': exact_matches + fuzzy_matches,
        'new_in_pdf': len(unmatched_pdf),
        'missing_from_pdf': len(unmatched_csv),
        'records_with_diffs': records_with_diffs,
        'total_field_diffs': total_field_diffs,
        'field_diff_breakdown': field_breakdown,
    }
