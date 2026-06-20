"""
src/extract.py
───────────────
LangGraph node: raw job postings → Claude Haiku → structured leads.
"""

import json
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
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


def _classify_signal(title: str, description: str) -> str:
    """Keyword fallback when Haiku is unavailable."""
    text = (title + " " + description).lower()
    if any(w in text for w in ["treasury", "tresorier", "trésorier", "cash management", "iso 20022"]):
        return "treasury hire"
    if any(w in text for w in ["payment", "paiement", "psp", "acquiring", "sepa", "open banking"]):
        return "payments hire"
    return "AI/fintech hire"


def extract_node(state: dict) -> dict:
    """LangGraph node: reads raw_leads, writes leads."""
    raw_leads = state.get("raw_leads", [])

    # Test Anthropic API once before processing all leads
    haiku_ok = _test_haiku()
    if not haiku_ok:
        logger.warning("Haiku unavailable — using keyword fallback for all %d leads", len(raw_leads))

    client = anthropic.Anthropic() if haiku_ok else None
    structured = []
    haiku_errors = []

    for raw in raw_leads:
        lead = _extract_one(client, raw, haiku_errors) if haiku_ok else _fallback(raw)
        if lead:
            structured.append(lead)

    logger.info("Extraction complete: %d/%d leads structured (haiku_ok=%s)", len(structured), len(raw_leads), haiku_ok)
    if haiku_errors:
        logger.warning("First Haiku error: %s", haiku_errors[0])

    # Write debug file so we can inspect via git
    _write_debug(raw_leads, structured, haiku_ok, haiku_errors)

    return {**state, "leads": structured}


def _test_haiku() -> bool:
    """Quick API test — returns True if Haiku responds."""
    try:
        client = anthropic.Anthropic()
        client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=5,
            messages=[{"role": "user", "content": "ok"}],
        )
        logger.info("Haiku API test: OK (model=%s)", HAIKU_MODEL)
        return True
    except Exception as exc:
        logger.error("Haiku API test FAILED: %s", exc)
        return False


def _extract_one(client, raw: dict, errors: list) -> dict | None:
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
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_json = message.content[0].text.strip()
        lead = json.loads(raw_json)
        if lead.get("signal_type") not in SIGNAL_TYPES:
            lead["signal_type"] = "AI/fintech hire"
        return lead
    except Exception as exc:
        if not errors:
            errors.append(str(exc))
        logger.warning("Extraction failed for '%s': %s", raw.get("raw_title"), exc)
        return _fallback(raw)   # fallback so we never lose a lead


def _fallback(raw: dict) -> dict:
    """Build a structured lead from raw fields without Haiku."""
    return {
        "company_name": raw.get("raw_company", ""),
        "job_title": raw.get("raw_title", ""),
        "signal_type": _classify_signal(raw.get("raw_title", ""), raw.get("raw_description", "")),
        "location": raw.get("raw_location", ""),
        "posting_date": raw.get("raw_date", ""),
        "source": raw.get("source", ""),
        "url": raw.get("url", ""),
    }


def _write_debug(raw_leads, structured, haiku_ok, haiku_errors):
    """Write debug_run.json next to leads.csv for post-run inspection."""
    try:
        debug_path = os.path.join(os.path.dirname(__file__), "..", "output", "debug_run.json")
        os.makedirs(os.path.dirname(debug_path), exist_ok=True)
        summary = {
            "raw_count": len(raw_leads),
            "structured_count": len(structured),
            "haiku_ok": haiku_ok,
            "haiku_errors": haiku_errors[:3],
            "sources": {},
        }
        for r in raw_leads:
            s = r.get("source", "unknown")
            summary["sources"][s] = summary["sources"].get(s, 0) + 1
        with open(debug_path, "w") as f:
            json.dump(summary, f, indent=2)
        logger.info("Debug summary written to %s", debug_path)
    except Exception as exc:
        logger.warning("Could not write debug file: %s", exc)
