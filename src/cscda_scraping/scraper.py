"""Scrape CSCDA meeting documents from cscda.org/agendas/.

Page structure: one <h6> per meeting date ("August 20, 2026") followed by a
<ul> of document links — "CSCDA Agenda" (agenda PDF) and "Staff Reports and
Meeting Minutes" (combined packet PDF). Other links (CSCDC/CIA/CSFA/EIS
committee agendas) are ignored.

Layout mirrors the CMFA scraper:
    data/cscda_scraping/meetings/YYYY-MM-DD/agenda.pdf
    data/cscda_scraping/meetings/YYYY-MM-DD/packet.pdf
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

AGENDAS_URL = "https://cscda.org/agendas/"
USER_AGENT = "Mozilla/5.0 (research; property-tax analysis)"
RATE_LIMIT = 0.5  # seconds between downloads


@dataclass
class Meeting:
    date: str  # YYYY-MM-DD
    agenda_url: str = ""
    packet_url: str = ""
    other_links: list = field(default_factory=list)


def list_meetings(min_year: int = 2025) -> list[Meeting]:
    """Parse the agendas page into Meeting entries, newest first."""
    resp = requests.get(AGENDAS_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    meetings = []
    for h in soup.find_all("h6"):
        text = h.get_text(strip=True)
        m = re.match(r"^([A-Z][a-z]+\.? \d{1,2}, \d{4})$", text)
        if not m:
            continue
        raw = m.group(1).replace(".", "")
        try:
            date = datetime.strptime(raw, "%B %d, %Y")
        except ValueError:
            try:
                date = datetime.strptime(raw, "%b %d, %Y")
            except ValueError:
                continue  # e.g. "Sept 17, 2020" — nonstandard abbreviation, pre-min_year anyway
        if date.year < min_year:
            continue
        meeting = Meeting(date=date.strftime("%Y-%m-%d"))
        ul = h.find_next_sibling("ul")
        if ul is None:
            continue
        for a in ul.find_all("a"):
            label = a.get_text(strip=True).lower()
            href = a.get("href", "")
            if not href.lower().endswith(".pdf"):
                continue
            if label == "cscda agenda":
                meeting.agenda_url = href
            elif "staff report" in label or "minutes" in label:
                meeting.packet_url = href
            else:
                meeting.other_links.append((a.get_text(strip=True), href))
        meetings.append(meeting)
    return meetings


def download_all_meetings(
    output_dir: str = "data/cscda_scraping/meetings",
    min_year: int = 2025,
    include_packets: bool = True,
) -> dict:
    """Download agenda (and optionally packet) PDFs; skips existing files."""
    out = Path(output_dir)
    stats = {"meetings": 0, "downloaded": 0, "skipped": 0, "failed": 0}

    for meeting in list_meetings(min_year=min_year):
        stats["meetings"] += 1
        targets = [("agenda.pdf", meeting.agenda_url)]
        if include_packets:
            targets.append(("packet.pdf", meeting.packet_url))
        for filename, url in targets:
            if not url:
                continue
            dest = out / meeting.date / filename
            if dest.exists():
                stats["skipped"] += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                stats["downloaded"] += 1
                print(f"  {meeting.date}/{filename}  ({len(resp.content):,} bytes)")
            except Exception as exc:
                stats["failed"] += 1
                print(f"  FAILED {meeting.date}/{filename}: {exc}")
            time.sleep(RATE_LIMIT)

    print(f"\nCSCDA download complete: {stats}")
    return stats


if __name__ == "__main__":
    download_all_meetings()
