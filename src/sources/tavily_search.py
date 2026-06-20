"""
src/sources/tavily_search.py
─────────────────────────────
Two-phase Tavily strategy:
  1. Search for treasury/payments jobs on France Travail and HelloWork
  2. Filter results to individual offer URLs only (drop aggregator pages)
  3. Use Tavily extract() on individual URLs to get full page content
     → richer data for company name, location, exact title

Individual offer URLs we keep:
  - francetravail.fr/offres/recherche/detail/XXXXXX
  - hellowork.com/fr-fr/emplois/NNNNNNN.html

Everything else (Glassdoor, Indeed search pages, LinkedIn search pages,
jooble, etc.) is dropped.

Title parsing:
  France Travail: "Offre d'emploi {TITLE} - {DEPT} - {CITY} - {ID}"
    → raw_location = CITY
    → raw_title = TITLE
  HelloWork:      "Offre Emploi {CONTRACT} {TITLE} {CITY} ({CODE}) - Recrutement par {COMPANY} | Hellowork"
    → raw_company = COMPANY
    → raw_location = CITY
    → raw_title = clean job title
"""

import logging
import re

from tavily import TavilyClient

logger = logging.getLogger(__name__)

# Search queries targeting individual job offer pages
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

# URL patterns that indicate an INDIVIDUAL job offer
INDIVIDUAL_OFFER_PATTERNS = [
    r"candidat\.francetravail\.fr/offres/recherche/detail/[A-Z0-9]+$",
    r"hellowork\.com/fr-fr/emplois/\d+\.html$",
]

# URL substrings to drop (aggregator/list pages)
AGGREGATOR_SUBSTRINGS = [
    "/emploi/metier_",
    "/emploi-?",
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
    "hsfkramer.com",
    "eufintechs.com",
    "operatechventures.com",
    "/entreprises/",           # HelloWork company pages (not individual offers)
    "hellowork.com/fr-fr/emploi/",  # HelloWork aggregator listing
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
    """
    France Travail format:
      "Offre d'emploi {JOB TITLE} - {DEPT_CODE} - {CITY} - {OFFER_ID}"
      "Offre n° {ID} {JOB TITLE}"

    Returns dict with parsed_title and location.
    """
    # Strip the "Offre d'emploi " or "Offre n° XXXXXX " prefix
    parsed_title = title
    location = ""

    # Pattern: "Offre d'emploi JOB - DEPT - CITY - ID"
    m = re.match(
        r"Offre[s]? d.emploi\s+(.+?)\s+-\s+(\d{2,3})\s+-\s+([^-]+?)\s+-\s+[A-Z0-9]+$",
        title, re.IGNORECASE
    )
    if m:
        parsed_title = m.group(1).strip()
        location = m.group(3).strip()
        return {"parsed_title": parsed_title, "location": location}

    # Pattern: "Offre d'emploi JOB - DEPT - CITY" (no trailing ID)
    m = re.match(
        r"Offre[s]? d.emploi\s+(.+?)\s+-\s+(\d{2,3})\s+-\s+(.+)$",
        title, re.IGNORECASE
    )
    if m:
        parsed_title = m.group(1).strip()
        location = m.group(3).strip()
        return {"parsed_title": parsed_title, "location": location}

    # Pattern: "Offre n° ID TITLE"
    m = re.match(r"Offre\s+n°\s+[A-Z0-9]+\s+(.+)", title, re.IGNORECASE)
    if m:
        parsed_title = m.group(1).strip()
        return {"parsed_title": parsed_title, "location": location}

    # Minimal strip: remove "Offre d'emploi " prefix
    parsed_title = re.sub(r"^Offre[s]? d.emploi\s+", "", title, flags=re.IGNORECASE).strip()
    return {"parsed_title": parsed_title, "location": location}


def _parse_hellowork_title(title: str) -> dict:
    """
    HelloWork format (varies):
      "Offre Emploi {CONTRACT} {TITLE} {CITY} ({DEPT}) - Recrutement par {COMPANY} | Hellowork"
      "{TITLE} {CITY} ({DEPT}) - {COMPANY} - Hellowork"
      "{TITLE} - Hellowork"

    Returns dict with parsed_title, company, location.
    """
    parsed_title = title
    company = ""
    location = ""

    # Extract company: "par X | Hellowork" or "par X" or just before "| Hellowork"
    company_match = re.search(r"(?:par|by)\s+([^|\-]+?)(?:\s*\|\s*Hellowork|$)", title, re.IGNORECASE)
    if company_match:
        company = company_match.group(1).strip()

    # Extract location: "CITY (75)" or "CITY (75000)"
    loc_match = re.search(r"([\w\s-]+?)\s*\(\d{2,5}\)", title)
    if loc_match:
        location = loc_match.group(1).strip()
        # Clean "Paris 8e" → "Paris"
        location = re.sub(r"\s+\d+e?$", "", location).strip()

    # Clean the title: remove standard HelloWork prefixes and suffixes
    parsed_title = re.sub(r"^Offre\s+Emploi\s+(?:CDI|CDD|Alternance|Stage|Intérim)?\s*", "", title, flags=re.IGNORECASE).strip()
    parsed_title = re.sub(r"\s*-\s*Recrutement\s+par\s+.+?(?:\s*\|.*)?$", "", parsed_title, flags=re.IGNORECASE).strip()
    parsed_title = re.sub(r"\s*\|\s*Hellowork.*$", "", parsed_title, flags=re.IGNORECASE).strip()
    parsed_title = re.sub(r"\s*\(\d{2,5}\).*$", "", parsed_title).strip()  # remove "(75) ..."
    # Remove trailing city name if already extracted
    if location and parsed_title.lower().endswith(location.lower()):
        parsed_title = parsed_title[:-len(location)].strip().rstrip(" -")
    # Remove trailing H/F markers
    parsed_title = re.sub(r"\s+H/F\s*$", "", parsed_title, flags=re.IGNORECASE).strip()

    return {"parsed_title": parsed_title, "company": company, "location": location}


def fetch(tavily_key: str) -> list[dict]:
    """Search Tavily and return individual job offer leads. Returns [] on error."""
    try:
        return _run(tavily_key)
    except Exception as exc:
        logger.error("Tavily source failed: %s", exc)
        return []


def _run(api_key: str) -> list[dict]:
    client = TavilyClient(api_key=api_key)

    # Phase 1: search — collect individual offer URLs
    individual_urls = {}  # url → search_result dict

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

    # Phase 2: extract — get full page content for individual offers
    urls_to_extract = list(individual_urls.keys())[:20]  # cap at 20
    extracted = {}

    try:
        extract_resp = client.extract(urls=urls_to_extract)
        extracted = {r.get("url", ""): r for r in extract_resp.get("results", [])}
        logger.info("Tavily extract: %d/%d pages extracted",
                    len(extracted), len(urls_to_extract))
    except Exception as exc:
        logger.warning("Tavily extract failed, using search snippets: %s", exc)

    # Phase 3: build leads with parsed metadata
    results = []
    for url in urls_to_extract:
        search_data = individual_urls[url]
        extract_data = extracted.get(url, {})

        title = search_data.get("title", "")
        content = extract_data.get("raw_content", "") or search_data.get("content", "")

        # Parse structured metadata from title
        if "francetravail.fr" in url:
            parsed = _parse_francetravail_title(title)
            raw_title = parsed["parsed_title"]
            raw_company = ""  # not in France Travail titles
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
