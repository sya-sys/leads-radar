"""
src/extract.py
───────────────
LangGraph node: raw job postings → Gemini 2.0 Flash → structured leads.

Uses google-genai SDK (the newer replacement for google-generativeai).
Falls back to keyword classification if Gemini is unavailable.
"""

import json
import logging
import os

from google import genai

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"
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
- Return ONLY a JSON object, no explanation, no markdown fences.

JSON:"""


def _classify_signal(title: str, description: str) -> str:
    """Keyword fallback when Gemini is unavailable."""
    text = (title + " " + description).lower()
    if any(w in text for w in ["treasury", "tresorier", "trésorier", "cash management", "iso 20022", "tresorerie"]):
        return "treasury hire"
    if any(w in text for w in ["payment", "paiement", "psp", "acquiring", "sepa", "open banking", "fintech"]):
        return "payments hire"
    return "AI/fintech hire"


def extract_node(state: dict) -> dict:
    """LangGraph node: reads raw_leads, writes leads."""
    raw_leads = state.get("raw_leads", [])

    gemini_ok, active_model, client = _init_gemini()
    if not gemini_ok:
        logger.warning("Gemini unavailable (%s) — keyword fallback for %d leads", active_model, len(raw_leads))

    structured = []
    gemini_errors = []

    for raw in raw_leads:
        lead = _extract_one(client, raw, gemini_errors) if gemini_ok else _fallback(raw)
        if lead:
            structured.append(lead)

    logger.info("Extraction: %d/%d structured (gemini_ok=%s model=%s)", len(structured), len(raw_leads), gemini_ok, active_model)
    if gemini_errors:
        logger.warning("First Gemini error: %s", gemini_errors[0])

    _write_debug(raw_leads, structured, gemini_ok, active_model, gemini_errors)
    return {**state, "leads": structured}


def _init_gemini() -> tuple[bool, str, object]:
    """Configure Gemini and verify connectivity."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return False, "GEMINI_API_KEY not set", None
    try:
        client = genai.Client(api_key=api_key)
        # Quick connectivity test
        client.models.generate_content(
            model=GEMINI_MODEL,
            contents="ok",
        )
        logger.info("Gemini API test: OK (model=%s)", GEMINI_MODEL)
        return True, GEMINI_MODEL, client
    except Exception as exc:
        logger.error("Gemini init FAILED: %s", exc)
        return False, str(exc)[:150], None


def _extract_one(client, raw: dict, errors: list) -> dict:
    """Call Gemini to extract one lead. Falls back to keyword on failure."""
    try:
        prompt = EXTRACTION_PROMPT.format(
            raw_title=raw.get("raw_title", ""),
            raw_company=raw.get("raw_company", ""),
            raw_location=raw.get("raw_location", ""),
            raw_date=raw.get("raw_date", ""),
            raw_description=raw.get("raw_description", "")[:800],
            source=raw.get("source", ""),
            url=raw.get("url", ""),
            signal_types=", ".join(SIGNAL_TYPES),
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        raw_json = response.text.strip().strip("```json").strip("```").strip()
        lead = json.loads(raw_json)
        if lead.get("signal_type") not in SIGNAL_TYPES:
            lead["signal_type"] = "AI/fintech hire"
        return lead
    except Exception as exc:
        if not errors:
            errors.append(str(exc))
        logger.warning("Gemini extraction failed for '%s': %s", raw.get("raw_title"), exc)
        return _fallback(raw)


def _fallback(raw: dict) -> dict:
    return {
        "company_name": raw.get("raw_company", ""),
        "job_title": raw.get("raw_title", ""),
        "signal_type": _classify_signal(raw.get("raw_title", ""), raw.get("raw_description", "")),
        "location": raw.get("raw_location", ""),
        "posting_date": raw.get("raw_date", ""),
        "source": raw.get("source", ""),
        "url": raw.get("url", ""),
    }


def _write_debug(raw_leads, structured, gemini_ok, active_model, gemini_errors):
    try:
        debug_path = os.path.join(os.path.dirname(__file__), "..", "output", "debug_run.json")
        os.makedirs(os.path.dirname(debug_path), exist_ok=True)
        sources = {}
        for r in raw_leads:
            s = r.get("source", "unknown")
            sources[s] = sources.get(s, 0) + 1
        with open(debug_path, "w") as f:
            json.dump({
                "raw_count": len(raw_leads),
                "structured_count": len(structured),
                "gemini_ok": gemini_ok,
                "active_model": active_model,
                "gemini_errors": gemini_errors[:3],
                "sources": sources,
            }, f, indent=2)
    except Exception as exc:
        logger.warning("Could not write debug file: %s", exc)
