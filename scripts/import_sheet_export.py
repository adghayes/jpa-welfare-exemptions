"""Import a full xlsx export of the collaborator's Google Sheet into canonical CSVs.

The Google Sheet is the master (maintained by hand, off-repo). This script
converts its key tabs into normalized CSVs that the validation scripts read:

    input/grants.csv           <- "CMFA-CSCDA Grants" tab
    input/sheet_parcels.csv    <- "Parcels" tab
    input/sheet_tax_rates.csv  <- "Tax Rates" tab

Usage:
    python scripts/import_sheet_export.py "input/2026-08-21 Full Download.xlsx"
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

TABS = {
    "CMFA-CSCDA Grants": Path("input/grants.csv"),
    "Parcels": Path("input/sheet_parcels.csv"),
    "Tax Rates": Path("input/sheet_tax_rates.csv"),
}

DATE_COLUMNS = {"Date", "Acquisition Date", "Estimated Closing"}


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if col in DATE_COLUMNS:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
        elif df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip().replace({"nan": "", "None": ""})
    # drop fully-empty spacer rows
    return df[~(df.astype(str).apply(lambda r: "".join(r), axis=1).str.strip() == "")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", type=Path, help="Full-workbook xlsx export of the sheet")
    args = parser.parse_args()

    xl = pd.ExcelFile(args.xlsx)
    missing = [t for t in TABS if t not in xl.sheet_names]
    if missing:
        print(f"ERROR: expected tabs not found: {missing}", file=sys.stderr)
        print(f"Available: {xl.sheet_names}", file=sys.stderr)
        return 1

    for tab, dest in TABS.items():
        df = normalize(xl.parse(tab, dtype=str))
        df.to_csv(dest, index=False)
        print(f"{tab!r}: {len(df)} rows -> {dest}")

    extra = [t for t in xl.sheet_names if t not in TABS and not t.startswith("ARCHIVE")]
    print(f"\nTabs not imported (informational): {extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
