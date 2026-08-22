"""CSCDA Packet Parser

The per-meeting packet PDF ("Staff Reports and Meeting Minutes") contains:
  1. a copy of the agenda,
  2. MINUTES of the *previous* regular meeting,
  3. per-item staff reports, each starting on a page headed
     "Agenda Item No. {N}{letter}".

Grant-item staff reports carry the deal details CSCDA agendas omit:

    PROJECT: Creekside Villas
    PURPOSE: Approve the Governmental Grant and Regulatory Agreement ...
    EXECUTIVE SUMMARY: ... 144 units ... 100% of the units will be rent restricted ...
    PROJECT DESCRIPTION: ... located at 220 47th Street, San Diego, California.
    <applicant / nonprofit-partner prose>
    Transaction Terms:
      Financing: Conventional
      Government Grant: $5,000
      Regulatory Term: 20 Years (plus successive 5-year extensions)
      Estimated Closing: September, 2026

CSCDA does not assign per-grant resolution numbers in these documents (the
attached resolutions are blank "26H-__" templates), so no resolution field
is extracted. The MINUTES section records each previous-meeting grant item's
outcome ("Unanimously approved" / "Item was continued") and is parsed into
per-property approval statuses — the minutes are also the corrected record
when an agenda misstates a city or county.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


@dataclass
class PacketGrant:
    """Deal details for one grant item, from its staff report."""
    property_name: str
    meeting_date: str = ""
    agenda_item: str = ""          # e.g. "6a"
    city: str = ""
    county: str = ""
    address: str = ""
    total_units: int | None = None
    rent_restricted_pct: str = ""  # e.g. "100% at 80% AMI"
    nonprofit_partner: str = ""
    financing: str = ""
    grant_amount: int | None = None
    term_years: int | None = None
    estimated_closing: str = ""


@dataclass
class MinutesOutcome:
    """Outcome of one grant item at the previous meeting, from the minutes."""
    property_name: str
    entity: str = ""
    city: str = ""
    county: str = ""
    meeting_date: str = ""         # date of the meeting the minutes describe
    status: str = ""               # "approved" | "continued" | "other"
    detail: str = ""


def extract_pages(pdf_path: Path) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


# ---------------------------------------------------------------- staff reports

ITEM_HEADER = re.compile(r"^Agenda Item No\.\s*(\d+[a-z]?)", re.MULTILINE)


def split_items(pages: list[str]) -> list[tuple[str, str]]:
    """Group pages into (item_number, text) chunks by 'Agenda Item No.' headers."""
    items = []
    current_no, buf = None, []
    for page in pages:
        m = ITEM_HEADER.match(page.strip()[:60])
        if m:
            if current_no is not None:
                items.append((current_no, "\n".join(buf)))
            current_no, buf = m.group(1), [page]
        elif current_no is not None:
            buf.append(page)
    if current_no is not None:
        items.append((current_no, "\n".join(buf)))
    return items


def _search(pattern: str, text: str, flags=re.IGNORECASE) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def _county(text: str) -> str:
    """County name, tolerating a line wrap inside it ("County of\\nLos Angeles")."""
    m = re.search(r"County of\s+([A-Z][A-Za-z]*(?:[ \n][A-Z][A-Za-z]*){0,2})\s*[.,]", text)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def parse_grant_item(item_no: str, text: str, meeting_date: str) -> PacketGrant | None:
    if "governmental grant and regulatory agreement" not in text.lower():
        return None
    name = _search(r"PROJECT:\s*(.+)", text, re.IGNORECASE)
    if not name:
        return None

    g = PacketGrant(property_name=name, meeting_date=meeting_date, agenda_item=item_no)
    g.city = _search(r"City of\s+([A-Z][A-Za-z .]+?)\s*,", text, 0)
    g.county = _county(text)
    # addresses usually end ", California." or "CA 90005"; periods inside
    # (S., Ave., Blvd.) must not terminate the match
    g.address = _search(
        r"located at\s+(.{5,120}?)(?:\s+in the City\b|,?\s*(?:California|CA\s*\d{5}|CA\b))",
        text, re.IGNORECASE | re.DOTALL,
    ) or _search(r"located at\s+([^\n]+)", text)
    g.address = re.sub(r"\s+", " ", g.address).strip(" .,")
    if g.address and g.city and g.city.lower() not in g.address.lower():
        g.address = f"{g.address}, {g.city}, CA"

    units = _search(r"(\d[\d,]*)[-\s]unit", text) or _search(r"of\s+(\d[\d,]*)\s+units", text)
    if units:
        g.total_units = int(units.replace(",", ""))

    pct = _search(r"(\d+)%\s+of the units will be rent[-\s]restricted", text)
    ami = _search(r"restricted to\s+(\d+)%\s+or less of area median income", text)
    if pct:
        g.rent_restricted_pct = f"{pct}%" + (f" at {ami}% AMI" if ami else "")

    g.financing = _search(r"Financing:\s*(.+)", text)
    amt = _search(r"Government Grant:\s*\$\s*([\d,]+)", text)
    if amt:
        g.grant_amount = int(amt.replace(",", ""))
    term = _search(r"Regulatory Term:\s*(\d+)\s*Years", text)
    if term:
        g.term_years = int(term)
    g.estimated_closing = _search(r"Estimated Closing:\s*(.+)", text)

    # nonprofit partner: "<Name> is a 501(c)(3) nonprofit ..." prose pattern;
    # anchor the name to a sentence/line start so the full name is captured
    np = re.search(
        r"(?:^|\.\s+|\n)([A-Z][A-Za-z0-9&.\'()“”\- ]{2,70}?)"
        r"(?:\s*\([^)]{1,20}\))?\s*(?:,\s*a|,?\s+is a)\s+501\(?c\)?\(?3\)?",
        text)
    if np:
        g.nonprofit_partner = re.sub(r"\s+", " ", np.group(1)).strip().rstrip(",")
    return g


def parse_packet_grants(pdf_path: Path, meeting_date: str = "") -> list[PacketGrant]:
    pages = extract_pages(pdf_path)
    grants = []
    for item_no, text in split_items(pages):
        g = parse_grant_item(item_no, text, meeting_date)
        if g:
            grants.append(g)
    return grants


# ---------------------------------------------------------------------- minutes

GRANT_SECTION = re.compile(
    r"regulatory agreement and grant in connection with", re.IGNORECASE)
SUBITEM = re.compile(r"^[a-z]\.\s+(.*)")


def parse_packet_minutes(pdf_path: Path) -> list[MinutesOutcome]:
    """Parse the MINUTES section: previous-meeting grant items and outcomes."""
    pages = extract_pages(pdf_path)
    # minutes run from the page starting "MINUTES" to the first Agenda Item page
    start = end = None
    for i, p in enumerate(pages):
        head = p.strip()[:40]
        if start is None and head.startswith("MINUTES"):
            start = i
        elif start is not None and ITEM_HEADER.match(head):
            end = i
            break
    if start is None:
        return []
    text = "\n".join(pages[start:end])

    date = ""
    dm = re.search(r"MINUTES\s+REGULAR MEETING[^\n]*\n[^\n]*\n([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if not dm:
        dm = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if dm:
        from datetime import datetime
        try:
            date = datetime.strptime(dm.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Collect each lettered sub-item's full block of lines (heads wrap across
    # lines), then split the block into the item head and the outcome text at
    # the first motion/outcome sentence.
    blocks: list[str] = []
    in_section = False
    buf: list[str] = []

    def close_block():
        if buf:
            blocks.append(" ".join(buf))
            buf.clear()

    for line in text.split("\n"):
        s = line.strip()
        if GRANT_SECTION.search(s):
            in_section = True
            continue
        if in_section and re.match(r"^\d+\.\s", s):
            close_block()
            in_section = False
            continue
        if not in_section:
            continue
        if SUBITEM.match(s):
            close_block()
            buf.append(SUBITEM.match(s).group(1))
        elif buf and s:
            buf.append(s)
    close_block()

    outcomes: list[MinutesOutcome] = []
    OUTCOME_START = re.compile(
        r"\b(Motion to|Item was|Unanimously|The item was|No action)\b")
    for block in blocks:
        m = OUTCOME_START.search(block)
        head, tail = (block[:m.start()], block[m.start():]) if m else (block, "")
        pm = re.search(r"\(([^)]+)\)", head)
        o = MinutesOutcome(
            property_name=pm.group(1).strip() if pm else head.strip()[:60],
            entity=head[:pm.start()].strip().rstrip(", ") if pm else "",
            city=_search(r"City of\s+([A-Z][A-Za-z .]+?)\s*,", head, 0),
            county=_county(head),
            meeting_date=date,
        )
        low = tail.lower()
        if "continued" in low:
            o.status = "continued"
        elif "approved" in low:
            o.status = "approved"
        elif tail.strip():
            o.status = "other"
            o.detail = " ".join(tail.split())[:160]
        outcomes.append(o)
    return outcomes


if __name__ == "__main__":
    import sys

    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "data/cscda_scraping/meetings/2026-08-06/packet.pdf")
    date = pdf.parent.name
    print("== staff reports:")
    for g in parse_packet_grants(pdf, date):
        print(f"  [{g.agenda_item}] {g.property_name} | {g.city}, {g.county} | "
              f"{g.total_units}u {g.rent_restricted_pct} | ${g.grant_amount} "
              f"{g.term_years}yr | close {g.estimated_closing} | NP: {g.nonprofit_partner}")
        print(f"       addr: {g.address}")
    print("== minutes outcomes:")
    for o in parse_packet_minutes(pdf):
        print(f"  {o.meeting_date} {o.property_name} ({o.city}, {o.county}): {o.status} {o.detail}")
