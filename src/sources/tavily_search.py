"""
src/sources/tavily_search.py
─────────────────────────────
Fetches job signals via Tavily's search + extract API.
All configuration (queries, sites, result counts) comes from config.json.

TWO-PHASE STRATEGY per Tavily source:
  Phase 1 — Search: site: operator queries → collect individual offer URLs
  Phase 2 — Extract: fetch full page content for each URL

SUPPORTED SOURCE IDs (all use tool=tavily):
  france_travail, hellowork, malt, boamp — any site can be added to config.json
"""

import logging
import re

from tavily import TavilyClient

logger = logging.getLogger(__name__)

# URL patterns that indicate an individual offer page (not a listing/aggregator)
INDIVIDUAL_OFFER_PATTERNS = [
    r"candidat\.francetravail\.fr/offres/recherche/detail/[A-Z0-9]+$",
    r"hellowork\.com/fr-fr/emplois/\d+\.html$",
    r"malt\.fr/project/[a-z0-9-]+$",
    r"boamp\.fr/avis/detail/",
]

# Substrings that indicate aggregator/listing pages — skip these
AGGREGATOR_SUBSTRINGS = [
    "/emploi/metier_", "glassdoor.com", "indeed.com", "jooble.org",
    "linkedin.com/jobs", "cadremploi.fr", "bebee.com", "jobleads.com",
    "selbyjennings.com", "efinancialcareers", "francetravail.fr/offres/recherche?",
    "hellowork.com/fr-fr/emploi/",  # listing pages (not individual offers)
]


def fetch_source(tavily_key: str, source_config: dict) -> list[dict]:
    """
    Fetch leads for a single Tavily source config entry.
    Returns normalized lead dicts ready for extraction.
    """
    try:
        return _run(tavily_key, source_config)
    except Exception as exc:
        logger.error("Tavily source '%s' failed: %s", source_config.get("id"), exc)
        return []


def _run(api_key: str, source: dict) -> list[dict]:
    client = TavilyClient(api_key=api_key)
    sid = source["id"]
    site = source.get("site", "")
    queries = source.get("queries", [])
    results_per_query = source.get("results_per_query", 5)

    # Phase 1: collect candidate URLs via site: search
    candidate_urls: list[tuple[str, dict]] = []  # (url, search_result)

    for query in queries:
        full_query = f"site:{site} {query}" if site else query
        try:
            response = client.search(
                query=full_query,
                search_depth="basic",
                max_results=results_per_query,
                include_answer=False,
            )
            for result in response.get("results", []):
                url = result.get("url", "")
                if _is_individual_offer(url, sid):
                    candidate_urls.append((url, result))
                else:
                    logger.debug("Skipped aggregator URL: %s", url)

            logger.info("Tavily '%s' query='%s' → %d results, %d individual offers",
                        sid, query, len(response.get("results", [])),
                        sum(1 for u, _ in candidate_urls))
        except Exception as exc:
            logger.warning("Tavily query failed ('%s'): %s", full_query, exc)

    if not candidate_urls:
        logger.info("Tavily '%s': no individual offer URLs found", sid)
        return []

    # Deduplicate URLs
    seen: set[str] = set()
    unique_urls: list[tuple[str, dict]] = []
    for url, result in candidate_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append((url, result))

    # Phase 2: extract full content for individual offer pages
    urls_to_extract = [u for u, _ in unique_urls]
    extracted_content: dict[str, str] = {}

    try:
        extract_response = client.extract(urls=urls_to_extract)
        for item in extract_response.get("results", []):
            extracted_content[item["url"]] = item.get("raw_content", "")
    except Exception as exc:
        logger.warning("Tavily extract failed for '%s': %s — using search snippets", sid, exc)

    # Build normalized lead dicts
    leads = []
    for url, search_result in unique_urls:
        raw_title = search_result.get("title", "")
        raw_description = extracted_content.get(url) or search_result.get("content", "")

        parsed = _parse_title(raw_title, sid)

        leads.append({
            "raw_title": parsed.get("parsed_title", raw_title),
            "raw_company": parsed.get("company", ""),
            "raw_location": parsed.get("location", ""),
            "raw_date": search_result.get("published_date", ""),
            "raw_description": raw_description[:1200],
            "source": sid,
            "url": url,
        })

    logger.info("Tavily '%s': %d leads ready for extraction", sid, len(leads))
    return leads


def _is_individual_offer(url: str, source_id: str) -> bool:
    """True if URL looks like an individual job offer page, not a listing."""
    if any(sub in url for sub in AGGREGATOR_SUBSTRINGS):
        return False
    # For known sites, require pattern match
    if source_id in ("france_travail", "hellowork"):
        return any(re.search(pat, url) for pat in INDIVIDUAL_OFFER_PATTERNS)
    # For other sites (malt, boamp), accept any non-aggregator URL
    return True


def _parse_title(title: str, source_id: str) -> dict:
    """Extract structured fields from page title before sending to Gemini."""
    if source_id == "france_travail":
        return _parse_francetravail_title(title)
    if source_id == "hellowork":
        return _parse_hellowork_title(title)
    return {"parsed_title": title, "company": "", "location": ""}


def _parse_francetravail_title(title: str) -> dict:
    # Strip attribution suffixes Tavily adds
    title = re.sub(r"\s*\|\s*France Travail\s*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s*-\s*France Travail\s*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s*\|.*$", "", title).strip()

    def _is_offer_id(s: str) -> bool:
        """France Travail offer IDs always start with 3 digits (e.g. 209FRGQ)."""
        return bool(re.match(r"^\d{3}", s))

    # "Offre d'emploi TITLE - DEPT - CITY - OFFER_ID"
    m = re.match(r"Offre[s]? d.emploi\s+(.+?)\s+-\s+(\d{2,3})\s+-\s+([^-]+?)\s+-\s+([A-Z0-9]+)$", title, re.IGNORECASE)
    if m:
        city = m.group(3).strip()
        return {"parsed_title": m.group(1).strip(), "company": "", "location": "" if _is_offer_id(city) else city}

    # "Offre d'emploi TITLE - DEPT - CITY"
    m = re.match(r"Offre[s]? d.emploi\s+(.+?)\s+-\s+(\d{2,3})\s+-\s+(.+)$", title, re.IGNORECASE)
    if m:
        city = m.group(3).strip()
        return {"parsed_title": m.group(1).strip(), "company": "", "location": "" if _is_offer_id(city) else city}

    # "TITLE - DEPT - CITY - OFFER_ID" (no prefix)
    m = re.match(r"(.+?)\s+-\s+(\d{2,3})\s+-\s+([^-]+?)\s+-\s+([A-Z0-9]+)$", title)
    if m:
        city, offer_id = m.group(3).strip(), m.group(4)
        if _is_offer_id(offer_id) and not _is_offer_id(city):
            return {"parsed_title": m.group(1).strip(), "company": "", "location": city}
        return {"parsed_title": m.group(1).strip(), "company": "", "location": ""}

    # "Offre n° OFFER_ID TITLE"
    m = re.match(r"Offre\s+n°\s+[A-Z0-9]+\s+(.+)", title, re.IGNORECASE)
    if m:
        return {"parsed_title": m.group(1).strip(), "company": "", "location": ""}

    # "TITLE - CITY - OFFER_ID" (no dept code, e.g. Monaco)
    m = re.match(r"(.+?)\s+-\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s-]{2,20})\s+-\s+(\d{3}[A-Z0-9]+)$", title)
    if m:
        city = m.group(2).strip()
        parsed = re.sub(r"^Offre[s]? d.emploi\s+", "", m.group(1).strip(), flags=re.IGNORECASE).strip()
        return {"parsed_title": parsed, "company": "", "location": city}

    # "TITLE - OFFER_ID"
    m = re.match(r"(.+?)\s+-\s+(\d{3}[A-Z0-9]+)$", title)
    if m:
        parsed = re.sub(r"^Offre[s]? d.emploi\s+", "", m.group(1).strip(), flags=re.IGNORECASE).strip()
        return {"parsed_title": parsed, "company": "", "location": ""}

    parsed_title = re.sub(r"^Offre[s]? d.emploi\s+", "", title, flags=re.IGNORECASE).strip()
    return {"parsed_title": parsed_title, "company": "", "location": ""}


def _parse_hellowork_title(title: str) -> dict:
    """Extract company from HelloWork title. Leave location for Gemini."""
    company = ""
    m = re.search(r'(?:par|by)\s+([^|\-]+?)(?:\s*\|\s*Hellowork|$)', title, re.IGNORECASE)
    if m:
        company = m.group(1).strip()

    parsed_title = re.sub(r'^Offre\s+Emploi\s+(?:CDI|CDD|Alternance|Stage|Int[ée]rim)?\s*', '', title, flags=re.IGNORECASE).strip()
    parsed_title = re.sub(r'\s*-\s*Recrutement\s+par\s+.+?(?:\s*\|.*)?$', '', parsed_title, flags=re.IGNORECASE).strip()
    parsed_title = re.sub(r'\s*\|\s*Hellowork.*$', '', parsed_title, flags=re.IGNORECASE).strip()
    parsed_title = re.sub(r'\s*\(\d{2,5}\).*$', '', parsed_title).strip()
    parsed_title = re.sub(r'\s+H/F\s*$', '', parsed_title, flags=re.IGNORECASE).strip()

    return {"parsed_title": parsed_title, "company": company, "location": ""}
