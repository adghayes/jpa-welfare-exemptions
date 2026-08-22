"""Build a manifest of source-document URLs per meeting, for review links.

Records every public document per meeting: CMFA's agenda, staff report, and
minutes; CSCDA's agenda and combined staff-reports-and-minutes packet (the
packet fills the staff_report_url column; CSCDA minutes_url points at the
NEXT meeting's packet, because that is where each meeting's adopted minutes
are published).

Output: output/pipeline/doc_urls.csv
    agency, meeting_date, agenda_url, staff_report_url, minutes_url

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


def main() -> None:
    rows = []

    for meeting in get_meetings(min_year=2023):
        by_type = {}
        for doc in meeting.documents:
            by_type.setdefault(doc.doc_type, doc.url)
        rows.append({
            "agency": "CMFA", "meeting_date": meeting.date_str,
            "agenda_url": by_type.get("agenda", ""),
            "staff_report_url": by_type.get("staff_report", ""),
            "minutes_url": by_type.get("minutes", ""),
        })

    cscda = sorted(list_meetings(min_year=2025), key=lambda m: m.date)
    for i, meeting in enumerate(cscda):
        next_packet = cscda[i + 1].packet_url if i + 1 < len(cscda) else ""
        rows.append({
            "agency": "CSCDA", "meeting_date": meeting.date,
            "agenda_url": meeting.agenda_url,
            "staff_report_url": meeting.packet_url,
            "minutes_url": next_packet,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["agency", "meeting_date", "agenda_url",
                                          "staff_report_url", "minutes_url"])
        w.writeheader()
        w.writerows(rows)
    counts = {}
    for r in rows:
        counts[r["agency"]] = counts.get(r["agency"], 0) + 1
    print(f"wrote {OUT}: {len(rows)} meetings {counts}")


if __name__ == "__main__":
    main()
