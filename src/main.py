"""
src/main.py
────────────
Entry point. Orchestrates the full pipeline:

  1. Load config.json
  2. Fetch raw leads from all enabled sources
  3. Run the LangGraph pipeline: extract → dedupe
  4. CRM-safe merge: new leads appended, existing rows (with manual CRM data) never overwritten
  5. Write to Google Sheets
  6. Commit debug stats

HOW SOURCES ARE ROUTED:
  Each entry in config.json["sources"] has a "tool" field: "apify" or "tavily".
  - tool=apify + id=linkedin  → apify_linkedin.fetch()
  - tool=tavily               → tavily_search.fetch_source(source_config)
  Disabled sources (enabled=false) are skipped.

FAILURE ALERTING:
  If total new leads < config["alerts"]["min_leads_threshold"], we log a
  WARNING and write debug_run.json with status=ALERT so GitHub Actions
  can surface it. The pipeline does NOT exit(1) — it writes whatever it got.

DRY_RUN MODE:
  DRY_RUN=true skips all API calls and loads fixtures/sample_leads.json.
"""

import json
import logging
import os

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from src.config_loader import load_config, get_enabled_sources, get_columns, get_crm_columns, get_extraction_config
from src.sources import apify_linkedin, tavily_search
from src.extract import extract_node
from src.dedupe import dedupe_node, merge_and_write
from src import sheets

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")


class PipelineState(TypedDict, total=False):
    raw_leads: list[dict]
    leads: list[dict]
    new_leads: list[dict]


def build_pipeline():
    graph = StateGraph(PipelineState)
    graph.add_node("extract", extract_node)
    graph.add_node("dedupe", dedupe_node)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "dedupe")
    graph.add_edge("dedupe", END)
    return graph.compile()


def _load_fixtures() -> list[dict]:
    fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "sample_leads.json")
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


def _write_debug(stats: dict):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, "debug_run.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    logger.info("Debug stats written to %s", path)


def main():
    config = load_config()
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    errors: list[str] = []
    sources_stats: dict[str, int] = {}

    if dry_run:
        logger.info("═══ DRY RUN MODE ═══")
        raw_leads = _load_fixtures()
        logger.info("Loaded %d fixture leads", len(raw_leads))
    else:
        logger.info("═══ Leads Radar starting ═══")
        apify_token = os.environ.get("APIFY_TOKEN", "")
        tavily_key = os.environ.get("TAVILY_KEY", "")

        raw_leads = []
        for source in get_enabled_sources(config):
            sid = source["id"]
            tool = source.get("tool", "")
            try:
                if tool == "apify" and sid == "linkedin":
                    leads = apify_linkedin.fetch(apify_token, source)
                elif tool == "tavily":
                    leads = tavily_search.fetch_source(tavily_key, source)
                else:
                    logger.warning("Unknown tool '%s' for source '%s' — skipping", tool, sid)
                    leads = []

                sources_stats[sid] = len(leads)
                raw_leads += leads
                logger.info("Source '%s': %d raw leads", sid, len(leads))

            except Exception as exc:
                msg = f"Source '{sid}' failed: {exc}"
                logger.error(msg)
                errors.append(msg)
                sources_stats[sid] = 0

        logger.info("Total raw leads: %d", len(raw_leads))

    # ── Extract + dedupe ──────────────────────────────────────────────────────
    columns = get_columns(config)
    crm_columns = get_crm_columns(config)

    if dry_run:
        structured = []
        for r in raw_leads:
            row = {col: "" for col in columns}
            row.update({
                "company_name": r.get("raw_company", ""),
                "job_title": r.get("raw_title", ""),
                "signal_type": "treasury hire",
                "location": r.get("raw_location", ""),
                "posting_date": r.get("raw_date", ""),
                "source": r.get("source", ""),
                "url": r.get("url", ""),
            })
            structured.append(row)
        final_state = dedupe_node({"raw_leads": raw_leads, "leads": structured})
    else:
        pipeline = build_pipeline()
        final_state = pipeline.invoke({"raw_leads": raw_leads})

    new_leads = final_state.get("new_leads", [])
    logger.info("New leads after deduplication: %d", len(new_leads))

    # ── CRM-safe write ────────────────────────────────────────────────────────
    added = merge_and_write(new_leads, columns, crm_columns)
    logger.info("Added %d new rows to leads.csv (existing rows untouched)", added)

    # ── Google Sheets ─────────────────────────────────────────────────────────
    if not dry_run:
        try:
            sheets_count = sheets.append_leads(new_leads)
            logger.info("Google Sheets: %d rows appended", sheets_count)
        except Exception as exc:
            msg = f"Google Sheets failed: {exc}"
            logger.error(msg)
            errors.append(msg)

    # ── Debug stats + failure alert ───────────────────────────────────────────
    threshold = config.get("alerts", {}).get("min_leads_threshold", 3)
    status = "OK" if len(new_leads) >= threshold or dry_run else "ALERT"
    if status == "ALERT":
        logger.warning(
            "ALERT: only %d new leads found (threshold: %d). "
            "Check API keys and source availability.",
            len(new_leads), threshold
        )

    _write_debug({
        "status": status,
        "raw_count": len(raw_leads),
        "new_leads_count": added,
        "sources": sources_stats,
        "errors": errors,
        "dry_run": dry_run,
        "extraction_model": get_extraction_config(config).get("model", ""),
    })

    logger.info("═══ Done. Status: %s. %d new leads. ═══", status, added)


if __name__ == "__main__":
    main()
