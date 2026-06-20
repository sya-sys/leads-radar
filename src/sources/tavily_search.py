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
  - hellowork.com/fr-fr/entreprises/SLUG-NNNNN.html (company page with jobs)

Everything else (Glassdoor, Indeed search pages, LinkedIn search pages,
jooble, etc.) is dropped.
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

# URL patterns that indicate an INDIVIDUAL job offer (not an aggregator page)
INDIVIDUAL_OFFER_PATTERNS = [
    r"candidat\.francetravail\.fr/offres/recherche/detail/[A-Z0-9]+$",
    r"hellowork\.com/fr-fr/emplois/\d+\.html$",
]

# URL patterns to explicitly DROP (aggregator/search pages)
AGGREGATOR_PATTERNS = [
    r"/emploi/metier_",
    r"/emploi-\?",
    r"[?&](q|keywords|motscles|k)=",
    r"glassdoor\.com/Job/",
    r"indeed\.com/q-",
    r"jooble\.org",
    r"linkedin\.com/jobs/search",
    r"cadremploi\.fr/emploi/liste",
    r"bebee\.com/",
    r"jobleads\.com",
    r"selbyjennings\.com/en-us/jobs",
    r"efinancialcareers",
    r"iso20022\.org",
    r"societegenerale\.com",
    r"svb\.com",
    r"francefintech\.org",
    r"hsfkramer\.com",
    r"eufintechs\.com",
    r"operatechventures\.com",
]


def _is_individual_offer(url: str) -> bool:
    for pattern in INDIVIDUAL_OFFER_PATTERNS:
        if re.search(pattern, url):
            return True
    return False


def _is_aggregator(url: str) -> bool:
    for pattern in AGGREGATOR_PATTERNS:
        if re.search(pattern, url):
            return True
    return False


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
    individual_urls = {}  # url → {title, content, published_date}
    search_page_results = []

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
                # Non-aggregator general results still carried for snippet content
                elif url and "francetravail" not in url and "hellowork" not in url:
                    pass  # skip non-target domains entirely
            logger.info("Tavily search '%s' → %d results, %d individual",
                        query[:50], len(resp.get("results", [])), len(individual_urls))
        except Exception as exc:
            logger.warning("Tavily query failed '%s': %s", query[:40], exc)

    logger.info("Tavily: %d individual offer URLs found", len(individual_urls))
    if not individual_urls:
        return []

    # Phase 2: extract — get full page content for individual offers
    results = []
    urls_to_extract = list(individual_urls.keys())[:20]  # cap at 20 to stay within limits

    try:
        extract_resp = client.extract(urls=urls_to_extract)
        extracted = {r.get("url", ""): r for r in extract_resp.get("results", [])}
        logger.info("Tavily extract: %d/%d pages successfully extracted",
                    len(extracted), len(urls_to_extract))
    except Exception as exc:
        logger.warning("Tavily extract failed, using search snippets: %s", exc)
        extracted = {}

    for url in urls_to_extract:
        search_data = individual_urls[url]
        extract_data = extracted.get(url, {})

        # Use extracted content if available, fall back to search snippet
        content = extract_data.get("raw_content", "") or search_data.get("content", "")
        title = search_data.get("title", "")

        results.append({
            "raw_title": title,
            "raw_company": "",        # Gemini will extract from content
            "raw_location": "",       # Gemini will extract from content
            "raw_date": search_data.get("published_date", ""),
            "raw_description": content[:1500],  # more content = better extraction
            "source": "tavily",
            "url": url,
        })

    logger.info("Tavily: returning %d individual job offer leads", len(results))
    return results
