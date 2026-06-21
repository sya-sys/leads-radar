"""
src/extract.py
───────────────
LangGraph node: raw job postings → Gemini 2.0 Flash → structured leads.

BATCH STRATEGY:
  All leads are batched into ONE Gemini call (free tier: 15 RPM).
  Gemini returns a JSON array — one object per posting, same order.

FIELDS EXTRACTED:
  Core:      company_name, job_title, signal_type, location, posting_date, source, url
  Company:   company_sector, company_size
  Role:      seniority, contract_type, is_new_role
  Signal:    tech_mentioned, transformation_context, project_type, service_offer
  Score:     icp_score, urgency

CRITICAL PRIORITY RULES (enforced in prompt):
  - If Title/Company/Location are pre-parsed (non-empty), use them AS-IS.
  - Only fall back to Description for fields that are empty.
  - Never copy page boilerplate into any field.
"""

import json
import logging
import os

from google import genai

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.0-flash"
SIGNAL_TYPES = ["treasury hire", "payments hire", "AI/fintech hire"]

BATCH_PROMPT_HEADER = """\
You are an ICP signal extractor for a fintech/treasury/payments consultancy.
Below are {n} job postings. For EACH one, extract ALL fields as JSON.
Return a JSON ARRAY (one object per posting, same order as input).

Fields to extract per posting:
- company_name       : the company hiring
- company_sector     : one of: fintech / corporate / bank / insurance / retail / public / other
- company_size       : one of: startup / PME / mid-cap / grand-groupe / unknown
- job_title          : the exact job title
- seniority          : one of: junior / mid / senior / director / C-level / unknown
- contract_type      : one of: CDI / CDD / Intérim / Freelance / Alternance / unknown
- is_new_role        : "true" if posting says "création de poste" or "nouveau poste", else "false", else "unknown"
- tech_mentioned     : comma-separated list of tools, technologies, standards, frameworks mentioned (e.g. "Kyriba, SAP, ISO 20022, SWIFT, PCI DSS"). Empty string if none.
- transformation_context : max 10 words describing the project context (e.g. "migration SAP S/4HANA en cours", "création centre de trésorerie"). Empty string if unclear.
- signal_type        : exactly one of: treasury hire / payments hire / AI/fintech hire
- project_type       : one of: treasury_setup / PSP_migration / ERP_integration / compliance / cash_pooling / reconciliation / other / unknown
- service_offer      : max 15 words — what a treasury/payments consultant should pitch to this company. Empty string if unclear.
- icp_score          : integer 1-5 (5 = perfect fit: Freelance + création de poste + known tech stack)
- urgency            : one of: low / medium / high (high = Intérim or Freelance contract)
- location           : city/country
- posting_date       : YYYY-MM-DD if possible, else as-is
- source             : keep the original value exactly
- url                : keep the original URL exactly

CRITICAL PRIORITY RULES:
- If "Title:" is non-empty → use it EXACTLY as job_title
- If "Company:" is non-empty → use it EXACTLY as company_name
- If "Location:" is non-empty → use it EXACTLY as location (already parsed — trust it)
- Only extract from Description when the above fields are empty
- Never copy page boilerplate (offer IDs, "| France Travail", "| Hellowork") into any field
- CRM fields (outreach_status, contact_name, notes, opportunity_type) → always empty string

Return ONLY a valid JSON array. No explanation, no markdown fences.

POSTINGS:
"""


def extract_node(state: dict) -> dict:
    """LangGraph node. Reads state['raw_leads'], writes state['leads']."""
    raw_leads = state.get("raw_leads", [])
    if not raw_leads:
        return {**state, "leads": []}

    # Read description max chars from env (set by main.py from config)
    desc_max = int(os.getenv("EXTRACTION_DESC_MAX_CHARS", "1200"))

    api_key = os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=api_key)

    structured = _batch_extract(client, raw_leads, desc_max)
    logger.info("Extraction complete: %d/%d leads structured", len(structured), len(raw_leads))
    return {**state, "leads": structured}


def _build_posting_block(i: int, raw: dict, desc_max: int) -> str:
    return (
        f"--- POSTING {i+1} ---\n"
        f"Title: {raw.get('raw_title', '')}\n"
        f"Company: {raw.get('raw_company', '')}\n"
        f"Location: {raw.get('raw_location', '')}\n"
        f"Date: {raw.get('raw_date', '')}\n"
        f"Source: {raw.get('source', '')}\n"
        f"URL: {raw.get('url', '')}\n"
        f"Description: {raw.get('raw_description', '')[:desc_max]}\n"
    )


def _batch_extract(client, raw_leads: list[dict], desc_max: int) -> list[dict]:
    """Send all leads in one Gemini call. Falls back per-lead on parse error."""
    postings_block = "\n".join(
        _build_posting_block(i, r, desc_max) for i, r in enumerate(raw_leads)
    )
    prompt = BATCH_PROMPT_HEADER.format(n=len(raw_leads)) + postings_block

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw_json = response.text.strip()

        # Strip markdown fences if Gemini adds them despite instructions
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
            raw_json = raw_json.strip()

        results = json.loads(raw_json)

        if not isinstance(results, list) or len(results) != len(raw_leads):
            logger.warning(
                "Gemini returned %d results for %d leads — falling back per-lead",
                len(results) if isinstance(results, list) else 0,
                len(raw_leads),
            )
            return _fallback_per_lead(client, raw_leads, desc_max)

        return [_validate(lead) for lead in results]

    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Batch extraction failed (%s) — falling back per-lead", exc)
        return _fallback_per_lead(client, raw_leads, desc_max)


def _fallback_per_lead(client, raw_leads: list[dict], desc_max: int) -> list[dict]:
    """One Gemini call per lead — used only when batch parse fails."""
    results = []
    for i, raw in enumerate(raw_leads):
        try:
            prompt = BATCH_PROMPT_HEADER.format(n=1) + _build_posting_block(0, raw, desc_max)
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            raw_json = response.text.strip().lstrip("```json").rstrip("```").strip()
            parsed = json.loads(raw_json)
            if isinstance(parsed, list) and parsed:
                results.append(_validate(parsed[0]))
            elif isinstance(parsed, dict):
                results.append(_validate(parsed))
        except Exception as exc:
            logger.warning("Per-lead extraction failed for '%s': %s", raw.get("raw_title"), exc)
    return results


def _validate(lead: dict) -> dict:
    """Ensure signal_type is valid; clamp icp_score 1–5."""
    if lead.get("signal_type") not in SIGNAL_TYPES:
        lead["signal_type"] = "AI/fintech hire"
    try:
        lead["icp_score"] = max(1, min(5, int(lead.get("icp_score", 1))))
    except (ValueError, TypeError):
        lead["icp_score"] = 1
    return lead
