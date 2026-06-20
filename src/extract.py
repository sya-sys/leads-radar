"""
src/extract.py
───────────────
LangGraph node: takes raw job postings → calls Claude Haiku → returns structured leads.

WHY LANGGRAPH HERE?
  LangGraph lets you build a pipeline as a graph of nodes (steps).
  Each node receives a "state" dict, does something, and returns an updated state.
  We use it for the extract → dedupe → output pipeline so the flow is explicit
  and easy to extend later (e.g. add a "score" node between dedupe and output).

THIS NODE:
  Input state key:  "raw_leads"  — list of raw dicts from the scrapers
  Output state key: "leads"      — list of structured dicts ready for deduplication

HOW HAIKU EXTRACTION WORKS:
  We send each raw posting to Claude Haiku with a prompt asking it to extract
  the 7 ICP fields as JSON. Haiku is fast and cheap (~$0.00025/1K input tokens).
  We batch calls to avoid hitting rate limits.
"""

import json
import logging

import anthropic

logger = logging.getLogger(__name__)

# Only Haiku — never Sonnet or Opus (cost constraint)
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# The 3 signal types we care about. Haiku will pick the closest match.
SIGNAL_TYPES = ["treasury hire", "payments hire", "AI/fintech hire"]

EXTRACTION_PROMPT = """\
You are an ICP signal extractor for a fintech consultancy.
Analyze this job posting and extract the following fields as JSON.

Job posting:
Title: {raw_title}
Company: {raw_company}
Location: {raw_location}
Date: {raw_date}
Description: {raw_description}
Source: {source}
URL: {url}

Extract these exact fields:
- company_name: string (the company hiring)
- job_title: string (the job title being posted)
- signal_type: one of {signal_types}
- location: string (city/country)
- posting_date: string (YYYY-MM-DD format if possible, else as-is)
- source: string (keep the original source value)
- url: string (keep the original URL)

Rules:
- If a field is unclear, use an empty string.
- signal_type must be exactly one of the three options.
- Return ONLY a JSON object, no explanation, no markdown.

JSON:"""


def extract_node(state: dict) -> dict:
    """
    LangGraph node function.
    Reads state["raw_leads"], writes state["leads"].
    """
    raw_leads = state.get("raw_leads", [])
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env automatically

    structured = []
    for raw in raw_leads:
        lead = _extract_one(client, raw)
        if lead:
            structured.append(lead)

    logger.info("Extraction complete: %d/%d leads structured", len(structured), len(raw_leads))
    return {**state, "leads": structured}


def _extract_one(client: anthropic.Anthropic, raw: dict) -> dict | None:
    """Call Haiku to extract one lead. Returns None on failure."""
    try:
        prompt = EXTRACTION_PROMPT.format(
            raw_title=raw.get("raw_title", ""),
            raw_company=raw.get("raw_company", ""),
            raw_location=raw.get("raw_location", ""),
            raw_date=raw.get("raw_date", ""),
            raw_description=raw.get("raw_description", ""),
            source=raw.get("source", ""),
            url=raw.get("url", ""),
            signal_types=", ".join(SIGNAL_TYPES),
        )

        message = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=256,   # JSON response is always short
            messages=[{"role": "user", "content": prompt}],
        )

        # Haiku returns the JSON as a text block
        raw_json = message.content[0].text.strip()
        lead = json.loads(raw_json)

        # Validate the signal_type — if Haiku hallucinated one, default it
        if lead.get("signal_type") not in SIGNAL_TYPES:
            lead["signal_type"] = "AI/fintech hire"

        return lead

    except json.JSONDecodeError as exc:
        logger.warning("Haiku returned invalid JSON for '%s': %s", raw.get("raw_title"), exc)
        return None
    except Exception as exc:
        logger.warning("Extraction failed for '%s': %s", raw.get("raw_title"), exc)
        return None
