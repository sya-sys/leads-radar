"""
src/sources/tavily_search.py
─────────────────────────────
Fetches open-web job signals via Tavily's search API.

Tavily is different from Apify:
  - It's a search engine (not a scraper), so results come back instantly.
  - We ask it to find recent job postings on the open web that match our keywords.
  - Results won't be structured like job boards — we get snippets and URLs,
    which Claude Haiku then interprets in the extraction step.

The tavily-python SDK handles auth and HTTP for us.
"""

import logging

from tavily import TavilyClient

logger = logging.getLogger(__name__)

# Search queries — phrased as natural-language questions to get better Tavily results.
# Covers LinkedIn, France Travail, HelloWork, and general web.
QUERIES = [
    # General open-web
    "treasury director job posting France 2026",
    "trésorier groupe recrutement France 2026",
    "head of payments PSP job France 2026",
    "fintech SEPA PSD3 open position France",
    "cash management ISO 20022 hire France",
    "open banking embedded finance job Paris",
    # France Travail (formerly Pôle Emploi) specific
    "site:francetravail.fr trésorier paiements recrutement",
    "site:francetravail.fr responsable paiements fintech",
    # HelloWork specific
    "site:hellowork.com trésorier responsable paiements France",
    "site:hellowork.com head payments treasury France",
]

# How many web results to fetch per query (Tavily max is 10)
RESULTS_PER_QUERY = 3


def fetch(tavily_key: str) -> list[dict]:
    """
    Search the open web via Tavily. Returns normalized lead dicts.
    Returns [] on any error.
    """
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
            search_depth="basic",    # "basic" is cheaper; "advanced" does deeper crawling
            max_results=RESULTS_PER_QUERY,
            include_answer=False,    # we don't need Tavily's AI answer, just raw results
        )

        for result in response.get("results", []):
            all_results.append({
                # Tavily returns a title and snippet — not a structured job posting.
                # We pass both to Claude Haiku for extraction.
                "raw_title": result.get("title", ""),
                "raw_company": "",            # unknown at this stage — Haiku will extract
                "raw_location": "",           # unknown at this stage
             