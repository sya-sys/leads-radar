"""
src/dedupe.py
──────────────
LangGraph node: removes duplicate leads.

HOW DEDUPLICATION WORKS:
  For each lead, we build a "fingerprint" — a hash of three fields:
    company_name + job_title + posting_date

  If two leads from different sources describe the same job at the same company
  on the same date, they'll produce the same hash and one will be dropped.

  We also load the existing leads.csv (if it exists) and deduplicate against it,
  so we never add a job we already saw in a previous run.

WHY NOT A DATABASE?
  For this volume (hundreds of leads/month), a CSV is plenty.
  No Postgres, no SQLite, no infrastructure to manage.

Input state key:  "leads"         — list of structured lead dicts
Output state key: "new_leads"     — deduplicated list (only leads not seen before)
"""

import csv
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

# Path to the CSV that persists across runs (committed to the repo)
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "output", "leads.csv")

CSV_FIELDS = [
    "company_name",
    "job_title",
    "signal_type",
    "location",
    "posting_date",
    "source",
    "url",
]


def dedupe_node(state: dict) -> dict:
    """
    LangGraph node function.
    Reads state["leads"], writes state["new_leads"].
    """
    leads = state.get("leads", [])
    existing_hashes = _load_existing_hashes()

    new_leads = []
    seen_this_run = set()

    for lead in leads:
        h = _fingerprint(lead)
        if h in existing_hashes or h in seen_this_run:
            logger.debug("Duplicate skipped: %s @ %s", lead.get("job_title"), lead.get("company_name"))
            continue
        seen_this_run.add(h)
        new_leads.append(lead)

    logger.info(
        "Deduplication: %d input → %d new leads (%d existing, %d dupes within run)",
        len(leads),
        len(new_leads),
        len(existing_hashes),
        len(leads) - len(new_leads),
    )
    return {**state, "new_leads": new_leads}


def _fingerprint(lead: dict) -> str:
    """Create a stable hash from company + title + date."""
    key = "|".join([
        lead.get("company_name", "").strip().lower(),
        lead.get("job_title", "").strip().lower(),
        lead.get("posting_date", "").strip(),
        lead.get("url", "").strip(),
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def _load_existing_hashes() -> set[str]:
    """Read the existing CSV and build a set of known fingerprints."""
    hashes = set()
    if not os.path.exists(OUTPUT_CSV):
        return hashes  # first run — nothing to compare against

    with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            hashes.add(_fingerprint(row))

    logger.info("Loaded %d existing lead hashes from CSV", len(hashes))
    return hashes


def write_csv(new_leads: list[dict]) -> int:
    """
    Append new leads to the CSV file.
    Creates the file with a header if it doesn't exist yet.
    Returns the number of rows written.
    """
    if not new_leads:
        logger.info("No new leads to write.")
        return 0

    file_exists = os.path.exists(OUTPUT_CSV)

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")

        if not file_exists:
            writer.writeheader()  # write column names on first run

        writer.writerows(new_leads)

    logger.info("Wrote %d new leads to %s", len(new_leads), OUTPUT_CSV)
    return len(new_leads)
