"""
src/sources/apify_linkedin.py
─────────────────────────────
Fetches job postings from LinkedIn via the Apify LinkedIn Jobs Scraper actor.
Queries and limits come from config.json — no hardcoded values here.

CRITICAL: Apify actor IDs use ~ separator in REST URLs, not /.
  Correct:   /v2/acts/curious_coder~linkedin-jobs-scraper/runs
  Wrong:     /v2/acts/curious_coder/linkedin-jobs-scraper/runs   ← 404
"""

import logging
import time
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

ACTOR_ID = "curious_coder~linkedin-jobs-scraper"   # ~ not /
BASE_URL  = "https://api.apify.com/v2"

_TPR_MAP = {
    "past-24h":   "r86400",
    "past-week":  "r604800",
    "past-month": "r2592000",
}


def fetch(apify_token: str, source_config: dict) -> list[dict]:
    """
    Run the LinkedIn actor and return normalized lead dicts.
    source_config keys used: queries, max_results, date_posted.
    Returns [] on any error — never crashes the run.
    """
    try:
        return _run_actor(apify_token, source_config)
    except Exception as exc:
        logger.error("LinkedIn source failed: %s", exc)
        return []


def _build_url(query: str, date_posted: str) -> str:
    tpr = _TPR_MAP.get(date_posted, "r86400")
    return (
        "https://www.linkedin.com/jobs/search/?"
        f"keywords={urllib.parse.quote(query)}"
        f"&location=France&f_TPR={tpr}"
    )


def _run_actor(token: str, source: dict) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    queries     = source.get("queries", [])
    max_results = source.get("max_results", 50)
    date_posted = source.get("date_posted", "past-week")

    search_urls = [_build_url(q, date_posted) for q in queries]

    # ── Start actor run ───────────────────────────────────────────────────────
    run_resp = httpx.post(
        f"{BASE_URL}/acts/{ACTOR_ID}/runs",
        headers=headers,
        json={
            "urls": search_urls,
            "count": max_results,
            "scrapeCompany": False,
        },
        timeout=30,
    )
    run_resp.raise_for_status()
    run_id = run_resp.json()["data"]["id"]
    logger.info("LinkedIn actor started | run_id=%s | %d queries", run_id, len(search_urls))

    # ── Poll until done ───────────────────────────────────────────────────────
    _wait_for_run(run_id, headers)

    # ── Fetch results ─────────────────────────────────────────────────────────
    dataset_resp = httpx.get(
        f"{BASE_URL}/actor-runs/{run_id}/dataset/items",
        headers=headers,
        params={"format": "json"},
        timeout=30,
    )
    dataset_resp.raise_for_status()
    items = dataset_resp.json()

    results = []
    for item in items:
        results.append({
            "raw_title":       item.get("title", ""),
            "raw_company":     item.get("companyName", ""),
            "raw_location":    item.get("location", ""),
            "raw_date":        item.get("postedAt", ""),
            "raw_description": item.get("descriptionText", "")[:1200],
            "source":          "linkedin",
            "url":             item.get("link", ""),
        })

    logger.info("LinkedIn: %d postings fetched", len(results))
    return results


def _wait_for_run(run_id: str, headers: dict, max_wait: int = 120) -> None:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = httpx.get(f"{BASE_URL}/actor-runs/{run_id}", headers=headers, timeout=10)
        resp.raise_for_status()
        status = resp.json()["data"]["status"]
        if status == "SUCCEEDED#��&WGW&��b7FGW2���$d��TB"�$$�%DTB"�%D��TB��UB"���&�6R'V�F��TW'&�"�b$�g�'V��'V���G�V�FVC��7FGW7�"��F��R�6�VW�R��&�6RF��V�WDW'&�"�b$�g�'V��'V���G�F�B��Bf��6�v�F�������v�G�2"�
