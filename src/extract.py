"""
src/extract.py
───────────────
LangGraph node: raw job postings → Gemini 2.0 Flash → structured leads.

All leads sent in ONE batched Gemini call to avoid free-tier rate limits.
Falls back to keyword classification if Gemini is unavailable.
"""

import json
import logging
import os

from google import genai

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"
SIGNAL_TYPES = ["treasury hire", "payments hire", "AI/fintech hire"]

BATCH_PROMPT_HEADER = """\
You are an ICP signal extractor for a fintech consultancy.
Below are {n} job postings. For EACH one, extract the 7 fields as JSON.
Return a JSON ARRAY (one object per posting, same order).

Fields to extract per posting:
- company_name: the company hiring
- job_title: the job title
- signal_type: exactly one of: treasury hire, payments hire, AI/fintech hire
- location: city/country
- posting_date: YYYY-MM-DD if possible, else as-is
- source: keep the original value
- url: keep the original URL

Return ONLY a valid JSON array, no explanation, no markdown fences.

POSTINGS:
"""

POSTING_TEMPLATE = """--- Posting {i} ---
Title: {raw_title}
Company: {raw_company}
Location: {raw_location}
Date: {raw_date}
Description: {raw_description}
Source: {source}
URL: {url}
"""


def _classify_signal(title: str, description: str) -> str:
    text = (title + " " + description).lower()
    if any(w in text for w in ["treasury", "tresorier", "trésorier", "cash management", "iso 20022", "tresorerie"]):
        return "treasury hire"
    if any(w in text for w in ["payment", "paiement", "psp", "acquiring", "sepa", "open banking", "fintech"]):
        return "payments hire"
    return "AI/fintech hire"


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


def extract_node(state: dict) -> dict:
    """LangGraph node: reads raw_leads, writes leads."""
    raw_leads = state.get("raw_leads", [])

    gemini_ok, active_model, client = _init_gemini()
    if not gemini_ok:
        logger.warning("Gemini unavailable (%s) — keyword fallback for %d leads", active_model, len(raw_leads))

    if gemini_ok:
        structured, error = _batch_extract(client, raw_leads)
        if error:
            logger.warning("Batch extraction failed (%s) — falling back to keywords", error)
            structured = [_fallback(r) for r in raw_leads]
    else:
        structured = [_fallback(r) for r in raw_leads]

    logger.info("Extraction: %d/%d structured (gemini_ok=%s)", len(structured), len(raw_leads), gemini_ok)
    _write_debug(raw_leads, structured, gemini_ok, active_model, [])
    return {**state, "leads": structured}


def _init_gemini() -> tuple[bool, str, object]:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return False, "GEMINI_API_KEY not set", None
    try:
        client = genai.Client(api_key=api_key)
        logger.info("Gemini client created (model=%s)", GEMINI_MODEL)
        return True, GEMINI_MODEL, client
    except Exception as exc:
        logger.error("Gemini init FAILED: %s", exc)
        return False, str(exc)[:150], None


def _batch_extract(client, raw_leads: list[dict]) -> tuple[list[dict], str | None]:
    """Send all leads in ONE Gemini call. Returns (structured_list, error_or_None)."""
    try:
        postings_text = ""
        for i, raw in enumerate(raw_leads, 1):
            postings_text += POSTING_TEMPLATE.format(
                i=i,
                raw_title=raw.get("raw_title", ""),
                raw_company=raw.get("raw_company", ""),
                raw_location=raw.get("raw_location", ""),
                raw_date=raw.get("raw_date", ""),
                raw_description=raw.get("raw_description", "")[:800],  # enough for company/location
                source=raw.get("source", ""),
                url=raw.get("url", ""),
            )

        prompt = BATCH_PROMPT_HEADER.format(n=len(raw_leads)) + postings_text

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        raw_json = response.text.strip().strip("```json").strip("```").strip()
        results = json.loads(raw_json)

        if not isinstance(results, list):
            return [_fallback(r) for r in raw_leads], "Gemini returned non-list"

        # Validate each item and fill gaps
        structured = []
        for i, item in enumerate(results):
            if not isinstance(item, dict):
                item = _fallback(raw_leads[i]) if i < len(raw_leads) else {}
            if item.get("signal_type") not in SIGNAL_TYPES:
                item["signal_type"] = "AI/fintech hire"
            # Ensure source/url are kept if Gemini dropped them
            if not item.get("source") and i < len(raw_leads):
                item["source"] = raw_leads[i].get("source", "")
            if not item.get("url") and i < len(raw_leads):
                item["url"] = raw_leads[i].get("url", "")
            structured.append(item)

        # Pad if Gemini returned fewer items than expected
        while len(structured) < len(raw_leads):
            structured.append(_fallback(raw_leads[len(structured)]))

        logger.info("Batch Gemini extraction: %d/%d items parsed", len(results), len(raw_leads))
        return structured, None

    except json.JSONDecodeError as exc:
        return [_fallback(r) for r in raw_leads], f"JSON parse error: {exc}"
    except Exception as exc:
        return [_fallback(r) for r in raw_leads], str(exc)[:150]


def _write_debug(raw_leads, structured, gemini_ok, active_model, errors):
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
                "errors": errors[:3],
                "sources": sources,
            }, f, indent=2)
    except Exception as exc:
        logger.warning("Could not write debug file: %s", exc)
