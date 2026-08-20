"""
CMFA Meeting Documents Scraper

Discovers and downloads meeting documents (agenda, staff report, minutes) from the CMFA website.
Organizes documents by meeting date into subdirectories.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


MEETINGS_URL = "https://www.cmfa-ca.com/resources/meetings/"
BASE_URL = "https://www.cmfa-ca.com"


@dataclass
class MeetingDocument:
    """A document associated with a meeting."""
    url: str
    doc_type: str  # 'agenda', 'staff_report', 'minutes'
    file_format: str  # 'pdf', 'docx'


@dataclass
class Meeting:
    """A CMFA board meeting with its associated documents."""
    date: datetime
    documents: list[MeetingDocument]

    @property
    def date_str(self) -> str:
        return self.date.strftime('%Y-%m-%d')


def parse_meeting_date(date_text: str) -> Optional[datetime]:
    """
    Parse a meeting date from text like "November 21, 2025" or "January 6, 2023".
    """
    date_text = date_text.strip()

    # Try common formats
    formats = [
        "%B %d, %Y",   # November 21, 2025
        "%B %d %Y",    # November 21 2025
        "%m/%d/%Y",    # 11/21/2025
        "%m-%d-%Y",    # 11-21-2025
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_text, fmt)
        except ValueError:
            continue

    return None


def get_doc_type_from_link_text(link_text: str) -> Optional[str]:
    """Determine document type from link text."""
    text_lower = link_text.lower().strip()

    if 'agenda' in text_lower:
        return 'agenda'
    elif 'staff' in text_lower or 'report' in text_lower:
        return 'staff_report'
    elif 'minutes' in text_lower:
        return 'minutes'

    return None


def get_file_format(url: str) -> str:
    """Determine file format from URL."""
    url_lower = url.lower()
    if '.docx' in url_lower or '.doc' in url_lower:
        return 'docx'
    return 'pdf'


def get_meetings(min_year: int = 2023) -> list[Meeting]:
    """
    Scrape the CMFA meetings page to discover all meetings and their documents.

    Parses the page structure where each meeting is an <li> with:
    - <span class="header"> containing the date
    - Links to agenda, staff report, and minutes

    Returns list of Meeting objects sorted by date (newest first).
    """
    response = requests.get(MEETINGS_URL, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, 'html.parser')
    meetings = []

    # Find the downloads list
    downloads_ul = soup.find('ul', id='downloads')
    if not downloads_ul:
        print("Warning: Could not find downloads list on page")
        return []

    # Each <li> is a meeting
    for li in downloads_ul.find_all('li', recursive=False):
        # Get the date from the header span
        header_span = li.find('span', class_='header')
        if not header_span:
            continue

        date_text = header_span.get_text(strip=True)
        meeting_date = parse_meeting_date(date_text)

        if meeting_date is None:
            print(f"Warning: Could not parse date: {date_text}")
            continue

        # Filter by year
        if meeting_date.year < min_year:
            continue

        # Find all document links in this <li>
        documents = []
        for link in li.find_all('a', href=True):
            href = link['href']
            link_text = link.get_text(strip=True)

            # Make absolute URL
            if href.startswith('/'):
                href = urljoin(BASE_URL, href)
            elif not href.startswith('http'):
                href = urljoin(MEETINGS_URL, href)

            # Determine document type
            doc_type = get_doc_type_from_link_text(link_text)
            if doc_type is None:
                continue

            # Check file format
            file_format = get_file_format(href)

            documents.append(MeetingDocument(
                url=href,
                doc_type=doc_type,
                file_format=file_format
            ))

        if documents:
            meetings.append(Meeting(date=meeting_date, documents=documents))

    # Sort by date, newest first
    return sorted(meetings, key=lambda m: m.date, reverse=True)


def download_meeting_documents(
    meeting: Meeting,
    output_dir: Path,
    doc_types: list[str] = None
) -> dict:
    """
    Download all documents for a single meeting.

    Saves to: output_dir/YYYY-MM-DD/{doc_type}.pdf

    Args:
        meeting: Meeting object with documents
        output_dir: Base directory (e.g., data/cmfa_scraping/meetings)
        doc_types: List of doc types to download (default: all)

    Returns:
        Dict with download statistics
    """
    if doc_types is None:
        doc_types = ['agenda', 'staff_report', 'minutes']

    # Create meeting directory
    meeting_dir = output_dir / meeting.date_str
    meeting_dir.mkdir(parents=True, exist_ok=True)

    stats = {'downloaded': 0, 'skipped': 0, 'failed': 0}

    for doc in meeting.documents:
        if doc.doc_type not in doc_types:
            continue

        # Standardized filename: {doc_type}.pdf
        ext = doc.file_format
        filename = f"{doc.doc_type}.{ext}"
        output_path = meeting_dir / filename

        # Check for existing file (also check .pdf if looking for .docx)
        pdf_path = meeting_dir / f"{doc.doc_type}.pdf"
        if output_path.exists() or (ext != 'pdf' and pdf_path.exists()):
            stats['skipped'] += 1
            continue

        try:
            print(f"  Downloading: {meeting.date_str}/{filename}")
            response = requests.get(doc.url, timeout=120)
            response.raise_for_status()
            output_path.write_bytes(response.content)
            stats['downloaded'] += 1
        except Exception as e:
            print(f"  Failed: {filename} - {e}")
            stats['failed'] += 1

    return stats


def download_all_meetings(
    output_dir: str = "data/cmfa_scraping/meetings",
    min_year: int = 2023,
    doc_types: list[str] = None
) -> dict:
    """
    Download all meeting documents from the CMFA website.

    Organizes documents into subdirectories by meeting date:
        output_dir/2025-11-21/agenda.pdf
        output_dir/2025-11-21/staff_report.pdf
        output_dir/2025-11-21/minutes.pdf

    Args:
        output_dir: Base directory for meetings
        min_year: Minimum year to include
        doc_types: List of document types to download
                   Default: ['agenda', 'staff_report', 'minutes']

    Returns:
        Dict with download statistics
    """
    if doc_types is None:
        doc_types = ['agenda', 'staff_report', 'minutes']

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"Discovering meetings from {MEETINGS_URL}...")
    meetings = get_meetings(min_year=min_year)

    print(f"Found {len(meetings)} meetings from {min_year} onwards")

    total_stats = {'downloaded': 0, 'skipped': 0, 'failed': 0, 'meetings': len(meetings)}

    for meeting in meetings:
        print(f"\n{meeting.date_str}:")
        stats = download_meeting_documents(
            meeting=meeting,
            output_dir=output_path,
            doc_types=doc_types
        )

        for key in ['downloaded', 'skipped', 'failed']:
            total_stats[key] += stats[key]

    print(f"\n{'='*50}")
    print(f"Download complete:")
    print(f"  Meetings: {total_stats['meetings']}")
    print(f"  Downloaded: {total_stats['downloaded']}")
    print(f"  Skipped (existing): {total_stats['skipped']}")
    print(f"  Failed: {total_stats['failed']}")

    return total_stats


# Legacy functions for backwards compatibility with existing scripts

def get_meeting_documents(min_year: int = 2023, include_docx: bool = True) -> list[dict]:
    """
    Legacy function - returns flat list of documents.

    Prefer using get_meetings() for new code.
    """
    meetings = get_meetings(min_year=min_year)
    documents = []

    for meeting in meetings:
        for doc in meeting.documents:
            if not include_docx and doc.file_format == 'docx':
                continue

            documents.append({
                'url': doc.url,
                'doc_type': doc.doc_type,
                'date': meeting.date,
                'filename': doc.url.split('/')[-1],
                'file_format': doc.file_format,
            })

    return sorted(documents, key=lambda x: x['date'])


def download_all_documents(
    output_dir: str = "data/cmfa_scraping/pdfs",
    min_year: int = 2023,
    doc_types: list[str] = None
) -> dict:
    """
    Legacy function - downloads to flat directory structure.

    Prefer using download_all_meetings() for new code.
    """
    if doc_types is None:
        doc_types = ['staff_report', 'minutes']

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"Discovering documents from {MEETINGS_URL}...")
    documents = get_meeting_documents(min_year=min_year)

    # Filter by doc_type
    documents = [d for d in documents if d['doc_type'] in doc_types]

    print(f"Found {len(documents)} documents from {min_year} onwards")

    stats = {'downloaded': 0, 'skipped': 0, 'failed': 0}

    for doc in documents:
        date = doc['date']
        doc_type = doc['doc_type']
        ext = doc.get('file_format', 'pdf')

        filename = f"{date.strftime('%Y-%m-%d')}-{doc_type}.{ext}"
        output_file = output_path / filename

        # Check for existing
        pdf_path = output_path / f"{date.strftime('%Y-%m-%d')}-{doc_type}.pdf"
        if output_file.exists() or (ext != 'pdf' and pdf_path.exists()):
            print(f"  Skipping (exists): {filename}")
            stats['skipped'] += 1
            continue

        try:
            print(f"  Downloading: {filename}")
            response = requests.get(doc['url'], timeout=120)
            response.raise_for_status()
            output_file.write_bytes(response.content)
            stats['downloaded'] += 1
        except Exception as e:
            print(f"  Failed: {filename} - {e}")
            stats['failed'] += 1

    print(f"\nDownload complete: {stats['downloaded']} downloaded, "
          f"{stats['skipped']} skipped, {stats['failed']} failed")

    return stats


if __name__ == "__main__":
    # Use new meeting-based download by default
    download_all_meetings()
