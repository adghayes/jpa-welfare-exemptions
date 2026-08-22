"""Render the dataset as a color-coded review workbook (.xlsx).

Every cell whose value came from a manual source is filled with that source's
color; script-generated cells have no fill. A Legend tab documents the scheme.
The xlsx is a *render* of the repo's provenance data — the repo CSVs remain
the source of truth; uploading the xlsx to Google Drive (with conversion)
yields a native, color-coded Google Sheet for review.

Currently feeds on output/pipeline/basic_list.csv plus the four grants that
predate the meeting archives (fully manual, from the collaborator sheet).
The merge step (provenance-stamped dataset) will reuse render_workbook()
with its real per-cell provenance map.

Usage:
    python scripts/publish_review_sheet.py [-o output/pipeline/review.xlsx]
"""

import argparse
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# provenance source -> fill color. Generated/computed cells are filled (very
# lightly); manual cells are UNFILLED — so any column or value a reviewer adds
# directly in the sheet defaults, correctly, to "manual".
GENERATED_FILL = "EAF1EA"             # very light green — script-generated
SOURCE_FILLS = {
    "cmfa-meeting-docs": GENERATED_FILL,
    "cscda-agenda": GENERATED_FILL,
    "cscda-agenda+packet": GENERATED_FILL,
    "la-county-api": GENERATED_FILL,
    "collaborator-sheet": None,       # manual: no fill
    "manual-repo-edit": None,         # manual: no fill
}

LEGEND = [
    ("filled = automated", "Value obtained by automation (document scraping, county API calls) — reproducible by re-running the pipeline"),
    ("no fill = manual", "Human-entered: collaborator sheet research, repo manual/ overrides, sheet formulas — and anything added directly in this sheet"),
]
LEGEND_FILLS = {"filled = automated": GENERATED_FILL, "no fill = manual": None}

HEADER_FILL = PatternFill("solid", fgColor="2F3B33")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)


def render_workbook(df: pd.DataFrame, cell_sources: dict, out_path: Path) -> None:
    """Write df to out_path with per-cell provenance fills.

    cell_sources: {(row_index, column_name): source_string} for every cell
    whose source is NOT script-generated; missing keys mean generated.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Dataset"

    for c, col in enumerate(df.columns, start=1):
        cell = ws.cell(row=1, column=c, value=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        width = max(12, min(38, int(df[col].astype(str).str.len().quantile(0.9)) + 2))
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.freeze_panes = "C2"

    for r, (idx, row) in enumerate(df.iterrows(), start=2):
        row_default = SOURCE_FILLS.get(row.get("source", ""), None)
        for c, col in enumerate(df.columns, start=1):
            cell = ws.cell(row=r, column=c, value=row[col] if row[col] != "" else None)
            if row[col] == "":
                continue
            source = cell_sources.get((idx, col))
            fill = SOURCE_FILLS.get(source) if source is not None else row_default
            if fill:
                cell.fill = PatternFill("solid", fgColor=fill)

    legend = wb.create_sheet("Legend")
    legend.column_dimensions["A"].width = 22
    legend.column_dimensions["B"].width = 110
    legend.cell(row=1, column=1, value="Cell color").font = Font(bold=True)
    legend.cell(row=1, column=2, value="Meaning").font = Font(bold=True)
    for i, (source, meaning) in enumerate(LEGEND, start=2):
        c = legend.cell(row=i, column=1, value=source)
        fill = LEGEND_FILLS.get(source)
        if fill:
            c.fill = PatternFill("solid", fgColor=fill)
        legend.cell(row=i, column=2, value=meaning)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    print(f"wrote {out_path} ({out_path.stat().st_size:,} bytes)")


# ---------------------------------------------------------- current data feed

MANUAL_PRE_ARCHIVE_IDS = ["192", "193", "223", "238"]  # grants predating meeting archives

SHEET_TO_LIST = {
    "Property Name": "property_name",
    "Applicant / Entity": "entity",
    "City": "city",
    "County": "county",
    "Agency": "agency",
    "Resolution": "resolution",
    "Date": "meeting_date",
    "Investor 1": "investor_1",
    "Nonprofit Partner": "nonprofit_partner",
    "Total Unit Count": "total_units",
    "Address": "address",
    "Grant Description": "grant_description",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path,
                        default=Path("output/pipeline/review.xlsx"))
    parser.add_argument("--sample", type=int, default=0,
                        help="keep only N generated rows per agency (plus all "
                             "manual rows) — small file for size-limited uploads")
    args = parser.parse_args()

    df = pd.read_csv("output/pipeline/basic_list.csv", dtype=str).fillna("")
    if args.sample:
        df = df.groupby("agency", group_keys=False).head(args.sample)

    # the four grants that predate the meeting archives: fully manual rows
    sheet = pd.read_csv("input/grants.csv", dtype=str).fillna("")
    manual = sheet[sheet["Project ID"].isin(MANUAL_PRE_ARCHIVE_IDS)]
    manual_rows = []
    for _, r in manual.iterrows():
        row = {list_col: r[sheet_col] for sheet_col, list_col in SHEET_TO_LIST.items()}
        row["item_type"] = "authorize"
        row["source"] = "collaborator-sheet"
        manual_rows.append(row)
    df = pd.concat([df, pd.DataFrame(manual_rows)], ignore_index=True).fillna("")
    df = df.sort_values(["agency", "meeting_date"], ignore_index=True)

    cell_sources = {}
    for idx, row in df.iterrows():
        if row["source"] == "collaborator-sheet":
            for col in df.columns:
                if row[col] != "" and col != "source":
                    cell_sources[(idx, col)] = "collaborator-sheet"

    render_workbook(df, cell_sources, args.output)


if __name__ == "__main__":
    main()
