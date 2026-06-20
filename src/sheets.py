"""
src/sheets.py
──────────────
Writes new leads to a Google Sheet.

HOW GOOGLE SHEETS AUTH WORKS (plain English):
  Google uses a "service account" — a special bot account that belongs to your
  Google Cloud project. You download a JSON key file for it, share your Sheet
  with its email address, and it can then read/write that sheet.

  We store the key JSON in an environment variable (GOOGLE_SHEETS_CREDENTIALS)
  to avoid committing secrets. gspread handles the OAuth handshake for us.

SHEET STRUCTURE:
  Row 1:  Headers (written once, on first run)
  Row 2+: One lead per row, appended each daily run

The sheet ID comes from GOOGLE_SHEET_ID env var.
"""

import json
import logging
import os

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

# The Google APIs we need access to
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_HEADERS = [
    "company_name",
    "job_title",
    "signal_type",
    "location",
    "posting_date",
    "source",
    "url",
]


def _get_client() -> gspread.Client:
    """
    Authenticate with Google using the service account credentials from env.
    Returns a gspread client object.
    """
    raw = os.environ["GOOGLE_SHEETS_CREDENTIALS"]
    creds_dict = json.loads(raw)  # parse the JSON string from env
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def append_leads(new_leads: list[dict]) -> int:
    """
    Append new leads to the Google Sheet.

    - If the sheet is empty, writes headers first.
    - Always appends rows (never overwrites existing data).
    - Returns the number of rows written.
    - Logs error and returns 0 on any failure (never crashes the run).
    """
    if not new_leads:
        logger.info("No new leads — skipping Google Sheets write.")
        return 0

    try:
        client = _get_client()
        sheet_id = os.environ["GOOGLE_SHEET_ID"]

        # Open the spreadsheet by ID, then the first worksheet (tab)
        spreadsheet = client.open_by_key(sheet_id)
        worksheet = spreadsheet.sheet1

        # Check if we need to write headers (sheet is empty)
        existing = worksheet.get_all_values()
        if not existing:
            worksheet.append_row(SHEET_HEADERS)
            logger.info("Google Sheets: wrote header row")

        # Build rows in the same column order as SHEET_HEADERS
        rows = [
            [lead.get(col, "") for col in SHEET_HEADERS]
            for lead in new_leads
        ]

        # Append all rows in one API call (efficient)
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")
        logger.info("Google Sheets: appended %d rows", len(rows))
        return len(rows)

    except Exception as exc:
        logger.error("Google Sheets write failed: %s", exc)
        return 0
