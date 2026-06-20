"""
src/main.py
────────────
Entry point. Orchestrates the full pipeline:

  1. Load env vars
  2. Fetch raw leads from all sources (plain Python, in parallel-ish)
  3. Run the LangGraph pipeline: extract → dedupe → output
  4. Write results to CSV (backup) and Google Sheets (primary)

HOW LANGGRAPH PIPELINE WORKS:
  We build a StateGraph with two nodes:
    - "extract": calls Claude Haiku on each raw lead → structured lead
    - "dedupe":  removes duplicates (vs. previous runs and within this run)

  The graph flows: START → extract → dedupe → END
  State is a plain dict passed between nodes.

DRY_RUN MODE:
  If DRY_RUN=true, we skip all API calls and load fixtures/sample_leads.json.
  The LangGraph pipeline still runs (so you can test extraction locally),
  but Haiku is also mocked — it returns the fixture data as-is without changes.
"""

import json
import logging
import os
import sys

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

# Load .env file if it exists (does nothing in GitHub Actions — secrets come from env)
load_dotenv()

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Import our modules ────────────────────────────────────────────────────────
from src.sources import apify_linkedin, apify_france_travail, apify_hellowork, tavily_search
from src.extract import extract_node
from src.dedupe import dedupe_node, write_csv
from src import sheets

# ── LangGraph state schema ────────────────────────────────────────────────────
# TypedDict tells LangGraph what keys the state dict will have.
# This is optional but makes the code easier to read and debug.

class PipelineState(TypedDict, total=False):
    raw_leads: list[dict]   # raw dicts from scrapers
    leads: list[dict]       # structured dicts from Haiku
    new_leads: list[dict]   # deduplicated leads ready for output


# ── Build the LangGraph pipeline ──────────────────────────────────────────────

def build_pipeline():
    graph = StateGraph(PipelineState)
    graph.add_node("extract", extract_node)
    graph.add_node("dedupe", dedupe_node)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "dedupe")
    graph.add_edge("dedupe", END)
    return graph.compile()


# ── DRY RUN helpers ───────────────────────────────────────────────────────────

def _load_fixtures() -> list[dict]:
    """Load sample_leads.json for local testing."""
    fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "sample_leads.json")
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


# ── Main ──────────────────────────────────────────────────────────────────────

def _write_source_debug(raw_leads: list[dict]) -> None:
    """Write per-source counts to output/debug_sources.json for every run."""
    import json as _json
    import os as _os
    sources = {}
    for r in raw_leads:
        s = r.get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1
    debug_path = _os.path.join(_os.path.dirname(__file__), "..", "output", "debug_sources.json")
    _os.makedirs(_os.path.dirname(debug_path), exist_ok=True)
    with open(debug_path, "w") as f:
        _json.dump({"total": len(raw_leads), "by_source": sources}, f, indent=2)
    logger.info("Source debug: %s", sources)



def main():
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"

    if dry_run:
        logger.info("═══ DRY RUN MODE — no API calls will be made ═══")
    else:
        logger.info("═══ Leads Radar starting ═══")

    # ── Step 1: Fetch raw leads from all sources ──────────────────────────────
    if dry_run:
        raw_leads = _load_fixtures()
        logger.info("Loaded %d fixture leads", len(raw_leads))
    else:
        apify_token = os.environ["APIFY_TOKEN"]
        tavily_key = os.environ["TAVILY_KEY"]

        # Each source handles its own errors — they return [] on failure
        raw_leads = []
        raw_leads += apify_linkedin.fetch(apify_token)
        raw_leads += apify_france_travail.fetch(apify_token)
        raw_leads += apify_hellowork.fetch(apify_token)
        raw_leads += tavily_search.fetch(tavily_key)

        logger.info("Total raw leads fetched: %d", len(raw_leads))

    # Always write diagnostic file so we can inspect via git even on empty runs
    _write_source_debug(raw_leads)

    if not raw_leads:
        logger.warning("No raw leads fetched from any source. Exiting.")
        sys.exit(0)

    # ── Step 2: Run the LangGraph pipeline ───────────────────────────────────
    # In DRY_RUN, we mock extraction: treat raw leads as already structured.
    if dry_run:
        # Rename keys to match the "structured" schema that dedupe expects.
        structured = []
        for r in raw_leads:
            structured.append({
                "company_name": r.get("raw_company", ""),
                "job_title": r.get("raw_title", ""),
                "signal_type": "treasury hire",  # default for fixture data
                "location": r.get("raw_location", ""),
                "posting_date": r.get("raw_date", ""),
                "source": r.get("source", ""),
                "url": r.get("url", ""),
            })
        # Run only the dedupe node (skip Haiku calls)
        pipeline = build_pipeline()
        # Inject pre-structured leads into the state, bypassing extract node
        # by pre-populating "leads" in the initial state
        final_state = dedupe_node({"raw_leads": raw_leads, "leads": structured})
    else:
        pipeline = build_pipeline()
        initial_state: PipelineState = {"raw_leads": raw_leads}
        final_state = pipeline.invoke(initial_state)

    new_leads = final_state.get("new_leads", [])
    logger.info("New leads after deduplication: %d", len(new_leads))

    # ── Step 3: Write outputs ─────────────────────────────────────────────────
    csv_count = write_csv(new_leads)

    if dry_run:
        logger.info("DRY RUN: skipping Google Sheets write (%d leads would be sent)", len(new_leads))
    else:
        sheets_count = sheets.append_leads(new_leads)
        logger.info("Google Sheets: %d rows appended", sheets_count)

    logger.info("═══ Done. %d new leads written to CSV. ═══", csv_count)


if __name__ == "__main__":
    main()
