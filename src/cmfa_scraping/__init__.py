"""
CMFA Scraping Module

Tools for scraping and parsing CMFA meeting documents (minutes, staff reports)
to extract welfare tax exemption grant information.
"""

from .scraper import download_all_documents, get_meeting_documents
from .minutes_parser import parse_all_minutes, MinutesGrant
from .matcher import load_csv, match_minutes_to_csv, find_unmatched_csv_rows
from .report import generate_reports_from_minutes
from .staff_report_parser import StaffReportGrant
