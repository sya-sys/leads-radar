"""
src/sources/tavily_search.py
─────────────────────────────
Fetches open-web job signals via Tavily's search API.
Covers LinkedIn, France Travail, HelloWork, and general web.
"""

import logging

from tavily import TavilyClient

logger = logging.getLogger(__name__)

QUERIES = [
    # General open-web
    "treasury director job posting France 2026",
    "tresorier groupe recrutement France 2026",
    "head of payments PSP job France 2026",
    "fintech SEPA PSD3 open position France",
    "cash management ISO 20022 hire France",
    "open banking embedded finance job Paris",
    # France Travail specific
    "site:francetravail.fr tresorier paiements recrutement",
    "site:francetravail.fr responsable paiements fintech",
    # HelloWork specific
    "site:hellowork.com tresorier responsable paiements France",
    "site:hellowork.com head payments treasury France",
]

RESULTS_PER_QUERY = 3


def fetch(tavily_key: str) -> list[dict]:
    """Search the open web via Tavily. Returns [] on any error."""
    try:
        return _search(tavily_key)
    except Exception as exc:
        logger.error("Tavily source failed: %s", exc)
        return []


def _search(api_key: str) -> list[dict]:
    client = TavilyClient(api_key=api_key)
    all_results = []

    for query in QUERIES:
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=RESULTS_PER_QUERY,
            include_answer=False,
        )

        for result in response.get("results", []):
            all_results.append({
                "raw_title": result.get("title", ""),
                "raw_company": "",
                "raw_location": "",
                "raw_date": result.get("published_date", ""),
                "raw_description": result.get("content", "")[:1000],
                "source": "tavily",
                "url": result.get("url", ""),
            })

        logger.info("Tavily: query='%s' -> %d results", query, len(response.get("results", [])))

    logger.info("Tavily: fetched %d total results", len(all_results))
    return all_results
