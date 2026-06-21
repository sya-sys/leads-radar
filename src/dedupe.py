"""
src/dedupe.py
──────────────
LangGraph node: removes duplicate leads within a run and vs. existing CSV.
Plus: CRM-safe merge — never overwrites rows that already have CRM data.

DEDUPLICATION:
  Fingerprint = sha256(company_name + job_title + posting_date + url).
  Same job reposted with new URL → different fingerprint (URL changes) → re-added.
  Same URL seen twice → deduplicated.

CRM-SAFE MERGE (merge_and_write):
  1. Read existing CSV into dict keyed by URL.
  2. For each new lead, skip if URL already exists (CRM data preserved).
  3. Rewrite full CSV: existing rows (with CRM cols intact) + new rows.
  4. On schema change (old CSV has fewer columns), new columns get empty strings.
"""

import csv
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "output", "leads.csv")


def dedupe_node(state: dict) -> dict:
    """LangGraph node. Reads state['leads'], writes state['new_leads']."""
    leads = state.get("leads", [])
    existing_hashes = _load_existing_hashes()

    new_leads = []
    seen_this_run: set[str] = set()

    for lead in leads:
        h = _fingerprint(lead)
        if h in existing_hashes or h in seen_this_run:
            logger.debug("Duplicate skipped: %s @ %s", lead.get("job_title"), lead.get("company_name"))
            continue
        seen_this_run.add(h)
        new_leads.append(lead)

    logger.info(
        "Deduplication: %d input → %d new leads (%d existing hashes, %d dupes this run)",
        len(leads), len(new_leads), len(existing_hashes), len(leads) - len(new_leads),
    )
    return {**state, "new_leads": new_leads}


def _fingerprint(lead: dict) -> str:
    key = "|".join([
        lead.get("company_name", "").strip().lower(),
        lead.get("job_title", "").strip().lower(),
        lead.get("posting_date", "").strip(),
        lead.get("url", "").strip(),
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def _load_existing_hashes() -> set[str]:
    hashes: set[str] = set()
    if not os.path.exists(OUTPUT_CSV):
        return hashes
    with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            hashes.add(_fingerprint(row))
    logger.info("Loaded %d existing lead hashes from CSV", len(hashes))
    return hashes


def merge_and_write(new_leads: list[dict], columns: list[str], crm_columns: list[str]) -> int:
    """
    CRM-safe merge:
    - Read all existing rows (preserving CRM column values).
    - Skip new leads whose URL already exists.
    - Rewrite full CSV with all rows (existing + new).
    - Missing columns from old schema get empty strings (safe schema migration).

    Returns number of truly new rows added.
    """
    existing_by_url: dict[str, dict] = {}

    if os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get("url", "").strip()
                if url:
                    existing_by_url[url] = dict(row)

    added = 0
    for lead in new_leads:
        url = lead.get("url", "").strip()
        if not url or url in existing_by_url:
            continue
        # Ensure CRM columns are always empty on new rows
        for col in crm_columns:
            lead.setdefault(col, "")
        existing_by_url[url] = lead
        added += 1

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in existing_by_url.values():
            writer.writerow(row)

    logger.info("Wrote %d total rows to %s (%d new)", len(existing_by_url), OUTPUT_CSV, added)
    return added


# Legacy alias — keeps DRY_RUN path in main.py working if still referenced
def write_csv(new_leads: list[dict]) -> int:
    """Deprecated: use merge_and_write. Kept for backward compat."""
    from src.config_loader import load_config, get_columns, get_crm_columns
    config = load_config()
    return merge_and_write(new_leads, get_columns(config), get_crm_columns(config))
