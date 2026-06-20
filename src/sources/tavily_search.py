"""
src/sources/tavily_search.py
─────────────────────────────
Two-phase Tavily strategy:
  1. Search for treasury/payments jobs on France Travail and HelloWork
  2. Filter results to individual offer URLs only (drop aggregator pages)
  3. Use Tavily extract() on individual URLs to get full page content

Title parsing:
  France Travail: "Offre d'emploi {TITLE} - {DEPT} - {CITY} - {ID}"
    → raw_location = CITY, raw_title = TITLE
  HelloWork: "Offre Emploi {CONTRACT} {TITLE} {CITY} ({CODE}) - Recrutement par {COMPANY} | Hellowork"
    → raw_company = COMPANY, raw_title = cleaned title (city included for Gemini)
    → raw_location = "" (Gemini extracts from title/description)
"""

import logging
import re

from tavily import TavilyClient

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "site:candidat.francetravail.fr/offres/recherche/detail tresorier",
    "site:candidat.francetravail.fr/offres/recherche/detail responsable tresorerie",
    "site:candidat.francetravail.fr/offres/recherche/detail responsable paiements fintech",
    "site:candidat.francetravail.fr/offres/recherche/detail head of payments",
    "site:hellowork.com/fr-fr/emplois tresorier responsable tresorerie",
    "site:hellowork.com/fr-fr/emplois head of payments fintech",
    "site:hellowork.com/fr-fr/emplois tresorerie paiements",
]

RESULTS_PER_QUERY = 5

INDIVIDUAL_OFFER_PATTERNS = [
    r"candidat\.francetravail\.fr/offres/recherche/detail/[A-Z0-9]+$",
    r"hellowork\.com/fr-fr/emplois/\d+\.html$",
]

AGGREGATOR_SUBSTRINGS = [
    "/emploi/metier_",
    "glassdoor.com",
    "indeed.com",
    "jooble.org",
    "linkedin.com/jobs",
    "cadremploi.fr",
    "bebee.com",
    "jobleads.com",
    "selbyjennings.com",
    "efinancialcareers",
    "iso20022.org",
    "francefintech.org",
    "/entreprises/",
    "hellowork.com/fr-fr/emploi/",
]


def _is_individual_offer(url: str) -> bool:
    for pattern in INDIVIDUAL_OFFER_PATTERNS:
        if re.search(pattern, url):
            return True
    return False


def _is_aggregator(url: str) -> bool:
    for substr in AGGREGATOR_SUBSTRINGS:
        if substr in url:
            return True
    return False


def _parse_francetravail_title(title: str) -> dict:
    """Parse France Travail offer title into job title and location.
    Format: "Offre d'emploi {JOB} - {DEPT} - {CITY} - {OFFER_ID}"
    """
    # Pattern with dept code + city + offer ID
    m = re.match(
        r"Offre[s]? d.emploi\s+(.+?)\s+-\s+(\d{2,3})\s+-\s+([^-]+?)\s+-\s+[A-Z0-9]+$",
        title, re.IGNORECASE
    )
    if m:
        city = m.group(3).strip()
        # Reject if city looks like an offer ID (all caps + digits)
        if not re.match(r'^[A-Z0-9]+$', city):
            return {"parsed_title": m.group(1).strip(), "location": city}
        return {"parsed_title": m.group(1).strip(), "location": ""}

    # Pattern without offer ID at end
    m = re.match(
        r"Offre[s]? d.emploi\s+(.+?)\s+-\s+(\d{2,3})\s+-\s+(.+)$",
        title, re.IGNORECASE
    )
    if m:
        city = m.group(3).strip()
        if not re.match(r'^[A-Z0-9]+$', city):
            return {"parsed_title": m.group(1).strip(), "location": city}
        return {"parsed_title": m.group(1).strip(), "location": ""}

    # "Offre n° ID TITLE"
    m = re.match(r"Offre\s+n°\s+[A-Z0-9]+\s+(.+)", title, re.IGNORECASE)
    if m:
        return {"parsed_title": m.group(1).strip(), "location": ""}

    parsed_title = re.sub(r"^Offre[s]? d.emploi\s+", "", title, flags=re.IGNORECASE).strip()
    return {"parsed_title": parsed_title, "location": ""}


def _parse_hellowork_title(title: str) -> dict:
    """Extract company and clean job title from a HelloWork offer title.
    City is kept in the title — Gemini extracts it from title/description.
    """
    company = ""

    # Extract company: "Recrutement par X | Hellowork" or "par X" near end
    company_match = re.search(
        r'(?:par|by)\s+([^|\-]+?)(?:\s*\|\s*Hellowork|$)', title, re.IGNORECASE
    )
    if company_match:
        company = company_match.group(1).strip()

    # Clean title: strip boilerplate prefix/suffix
    parsed_title = re.sub(
        r'^Offre\s+Emploi\s+(?:CDI|CDD|Alternance|Stage|Int[ée]rim)?\s*',
        '', title, flags=re.IGNORECASE
    ).strip()
    parsed_title = re.sub(
        r'\s*-\s*Recrutement\s+par\s+.+?(?:\s*\|.*)?$',
        '', parsed_title, flags=re.IGNORECASE
    ).strip()
    parsed_title = re.sub(r'\s*\|\s*Hellowork.*$', '', parsed_title, flags=re.IGNORECASE).strip()
    parsed_title = re.sub(r'\s*\(\d{2,5}\).*$', '', parsed_title).strip()
    parsed_title = re.sub(r'\s+H/F\s*$', '', parsed_title, flags=re.IGNORECASE).strip()

    return {"parsed_title": parsed_title, "company": company, "location": ""}


def fetch(tavily_key: str) -> list[dict]:
    try:
        return _run(tavily_key)
    except Exception as exc:
        logger.error("Tavily source failed: %s", exc)
        return []


def _run(api_key: str) -> list[dict]:
    client = TavilyClient(api_key=api_key)

    # Phase 1: search for individual offer URLs
    individual_urls: dict[str, dict] = {}

    for query in SEARCH_QUERIES:
        try:
            resp = client.search(
                query=query,
                search_depth="basic",
                max_results=RESULTS_PER_QUERY,
                include_answer=False,
            )
            for r in resp.get("results", []):
                url = r.get("url", "")
                if _is_aggregator(url):
                    continue
                if _is_individual_offer(url):
                    individual_urls[url] = r
            logger.info("Tavily search '%s' → %d individual total",
                        query[:55], len(individual_urls))
        except Exception as exc:
            logger.warning("Tavily query failed '%s': %s", query[:40], exc)

    logger.info("Tavily: %d individual offer URLs found", len(individual_urls))
    if not individual_urls:
        return []

    # Phase 2: extract full page content
    urls_to_extract = list(individual_urls.keys())[:20]
    extracted: dict[str, dict] = {}

    try:
        extract_resp = client.extract(urls=urls_to_extract)
        extracted = {r.get("url", ""): r for r in extract_resp.get("results", [])}
        logger.info("Tavily extract: %d/%d pages extracted", len(extracted), len(urls_to_extract))
    except Exception as exc:
        logger.warning("Tavily extract failed, using search snippets: %s", exc)

    # Phase 3: build structured leads
    results = []
    for url in urls_to_extract:
        search_data = individual_urls[url]
        extract_data = extracted.get(url, {})

        title = search_data.get("title", "")
        content = extract_data.get("raw_content", "") or search_data.get("content", "")

        if "francetravail.fr" in url:
            parsed = _parse_francetravail_title(title)
            raw_title = parsed["parsed_title"]
            raw_company = ""
            raw_location = parsed["location"]
        elif "hellowork.com" in url:
            parsed = _parse_hellowork_title(title)
            raw_title = parsed["parsed_title"]
            raw_company = parsed["company"]
            raw_location = parsed["location"]
        else:
            raw_title = title
            raw_company = ""
            raw_location = ""

        results.append({
            "raw_title": raw_title,
            "raw_company": raw_company,
            "raw_location": raw_location,
            "raw_date": search_data.get("published_date", ""),
            "raw_description": content[:1500],
            "source": "tavily",
            "url": url,
        })

    logger.info("Tavily: returning %d individual job offer leads", len(results))
    return results
