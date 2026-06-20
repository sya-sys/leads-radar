"""
src/sources/apify_linkedin.py
─────────────────────────────
Fetches job postings from LinkedIn via the Apify LinkedIn Jobs Scraper actor.
Actor: curious_coder/linkedin-jobs-scraper (URL-based, no login required)
https://apify.com/curious_coder/linkedin-jobs-scraper
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

ACTOR_ID = "curious_coder~linkedin-jobs-scraper"
BASE_URL = "https://api.apify.com/v2"

# LinkedIn Jobs search URLs — last 24 hours (f_TPR=r86400), France.
SEARCH_URLS = [
    "https://www.linkedin.com/jobs/search/?keywords=treasury+director&location=France&f_TPR=r86400",
    "https://www.linkedin.com/jobs/search/?keywords=head+of+payments&location=France&f_TPR=r86400",
    "https://www.linkedin.com/jobs/search/?keywords=tr%C3%A9sorier+groupe&location=France&f_TPR=r86400",
    "https://www.linkedin.com/jobs/search/?keywords=responsable+paiements&location=France&f_TPR=r86400",
    "https://www.linkedin.com/jobs/search/?keywords=fintech+PSP&location=France&f_TPR=r86400",
    "https://www.linkedin.com/jobs/search/?keywords=ISO+20022&location=France&f_TPR=r86400",
    "https://www.linkedin.com/jobs/search/?keywords=SEPA+open+banking&location=France&f_TPR=r86400",
]

MAX_RESULTS = 50


def fetch(apify_token: str) -> list[dict]:
    """
    Run the LinkedIn Jobs actor on Apify and return raw job postings.
    Returns [] on any error (never crashes the pipeline).
    """
    try:
        return _run_actor(apify_token)
    except Exception as exc:
        logger.error("LinkedIn source failed: %s", exc)
        return []


def _run_actor(token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}

    # Start a single actor run with all search URLs
    run_resp = httpx.post(
        f"{BASE_URL}/acts/{ACTOR_ID}/runs",
        headers=headers,
        json={
            "urls": SEARCH_URLS,
            "count": MAX_RESULTS,
            "scrapeCompany": False,
        },
        timeout=30,
    )
    run_resp.raise_for_status()
    run_id = run_resp.json()["data"]["id"]
    logger.info("LinkedIn actor started | run_id=%s | urls=%d", run_id, len(SEARCH_URLS))

    _wait_for_run(run_id, headers)

    dataset_resp = httpx.get(
        f"{BASE_URL}/actor-runs/{run_id}/dataset/items",
        headers=headers,
        params={"format": "json"},
        timeout=30,
    )
    dataset_resp.raise_for_status()
    items = dataset_resp.json()

    all_results = []
    for item in items:
        all_results.append({
            "raw_title": item.get("title", ""),
            "raw_company": item.get("companyName", ""),
            "raw_location": item.get("location", ""),
            "raw_date": item.get("postedAt", ""),
            "raw_description": item.get("descriptionText", "")[:1000],
            "source": "linkedin",
            "url": item.get("link", ""),
        })

    logger.info("LinkedIn: fetched %d postings", len(all_results))
    return all_results


def _wait_for_run(run_id: str, headers: dict, max_wait: int = 120) -> None:
    """Poll the run status until SUCCEEDED or timeout."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        status_resp = httpx.get(
            f"{BASE_URL}/actor-runs/{run_id}",
            headers=headers,
            timeout=10,
        )
        status_resp.raise_for_status()
        status = status_resp.json()["data"]["status"]

        if status == "SUCCEEDED":
            return
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended with status: {status}")

        time.sleep(5)

    raise TimeoutError(f"Apify run {run_id} did not finish within {max_wait}s")
