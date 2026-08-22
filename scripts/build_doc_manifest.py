"""Build a manifest of source-document URLs per meeting, for review links.

For each meeting, records the most descriptive public document available:
CMFA -> staff report (fallback: agenda, minutes), CSCDA -> the combined
staff-reports-and-minutes packet (fallback: agenda).

Output: output/pipeline/doc_urls.csv
    agency, meeting_date, doc_type, url

Fetches only the two index pages (no document downloads).

Usage:
    python scripts/build_doc_manifest.py
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.cmfa_scraping.scraper import get_meetings  # noqa: E402
from src.cscda_scraping.scraper import list_meetings  # noqa: E402

OUT = Path("output/pipeline/doc_urls.csv")
CMFA_PREFERENCE = ["staff_report", "agenda", "minutes"]


def main() -> None:
    rows = []

    for meeting in get_meetings(min_year=2023):
        by_type = {}
        for doc in meeting.documents:
            by_type.setdefault(doc.doc_type, doc.url)
        for doc_type in CMFA_PREFERENCE:
            if doc_type in by_type:
                rows.append({"agency": "CMFA", "meeting_date": meeting.date_str,
                             "doc_type": doc_type, "url": by_type[doc_type]})
                break

    for meeting in list_meetings(min_year=2025):
        if meeting.packet_url:
            rows.append({"agency": "CSCDA", "meeting_date": meeting.date,
                         "doc_type": "packet", "url": meeting.packet_url})
        elif meeting.agenda_url:
            rows.append({"agency": "CSCDA", "meeting_date": meeting.date,
                         "doc_type": "agenda", "url": meeting.agenda_url})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["agency", "meeting_date", "doc_type", "url"])
        w.writeheader()
        w.writerows(rows)
    counts = {}
    for r in rows:
        counts[r["agency"]] = counts.get(r["agency"], 0) + 1
    print(f"wrote {OUT}: {len(rows)} meetings {counts}")


if __name__ == "__main__":
    main()
