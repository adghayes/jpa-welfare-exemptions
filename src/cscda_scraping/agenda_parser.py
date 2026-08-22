"""CSCDA Agenda Parser

Extracts grant items from CSCDA meeting agendas. CSCDA lists them under a
numbered item reading "Consideration of a regulatory agreement and grant in
connection with the acquisition and financing of the following projects...",
with lettered sub-items in the same shape as CMFA's:

    a. CDR Verdana Development, LP (Verdana Apartments), City of San Diego,
       County of San Diego.

Unlike CMFA agendas, no resolution number or grant amount appears in the
agenda itself — those live in the combined staff-report/minutes packet.
"""

import re
from pathlib import Path

from src.cmfa_scraping.agenda_parser import (
    AgendaGrant,
    extract_text_from_pdf,
    parse_grant_line,
)

# Section triggers (lowercased substring match on the numbered item text)
AUTHORIZE_TRIGGER = "regulatory agreement and grant"
PRELIMINARY_TRIGGERS = ("acceptance of application", "preliminary")


def parse_agenda_grants(text: str) -> list[AgendaGrant]:
    lines = text.split("\n")
    grants: list[AgendaGrant] = []

    current_section = None
    item_text = ""
    item_section = None

    def flush():
        nonlocal item_text, item_section
        if item_text and item_section:
            grant = parse_grant_line(item_text, item_section)
            if grant is None and "(" in item_text and ")" not in item_text:
                # agenda typo: unclosed property parenthesis, e.g.
                # "Sawtelle 2481 LLC (Sawtelle 2481 Apartments, City of ..." (2026-04-02)
                repaired = item_text.replace(", City of", "), City of", 1)
                grant = parse_grant_line(repaired, item_section)
            if grant:
                # CSCDA lines end "County of X." — CMFA's shared regex keeps the dot
                grant.county = grant.county.rstrip(". ").strip()
                grant.city = grant.city.rstrip(". ").strip()
                grants.append(grant)
        item_text = ""
        item_section = None

    # Numbered items can wrap lines; accumulate each numbered item's full text
    # to test triggers against the whole item, not just its first line.
    numbered_buf = ""

    for line in lines:
        s = line.strip()
        if re.match(r"^\d+\.\s", s):
            flush()
            numbered_buf = s.lower()
            current_section = None
            if AUTHORIZE_TRIGGER in numbered_buf:
                current_section = "authorize"
            continue
        if current_section is None and numbered_buf and not re.match(r"^[a-z]\.\s", s):
            # continuation of the numbered item header
            numbered_buf += " " + s.lower()
            if AUTHORIZE_TRIGGER in numbered_buf:
                current_section = "authorize"
            elif all(t in numbered_buf for t in PRELIMINARY_TRIGGERS):
                current_section = "preliminary"
            continue
        if current_section:
            if re.match(r"^[a-z]\.\s", s):
                flush()
                item_text = s[2:].strip()
                item_section = current_section
            elif item_text and s:
                item_text += " " + s

    flush()
    return grants


def parse_agenda_pdf(pdf_path: Path) -> list[AgendaGrant]:
    return parse_agenda_grants(extract_text_from_pdf(pdf_path))


if __name__ == "__main__":
    import sys

    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "data/cscda_scraping/meetings/2026-08-20/agenda.pdf"
    )
    for i, g in enumerate(parse_agenda_pdf(pdf), 1):
        print(f"{i}. {g.property_name} | {g.entity} | {g.city}, {g.county} | {g.item_type}")
