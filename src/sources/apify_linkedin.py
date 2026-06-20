"""
src/sources/apify_linkedin.py
─────────────────────────────
Fetches job postings from LinkedIn via the Apify "LinkedIn Jobs Scraper" actor.

HOW APIFY WORKS (plain English):
  1. You send a POST to Apify's API to start a "run" of an actor (a scraper).
  2. Apify runs it on their servers (takes ~30-90 seconds).
  3. You poll until the run status is "SUCCEEDED".
  4. You fetch the results from the run's dataset.

We use httpx (a modern requests-like library) for all HTTP calls.
No Apify SDK — keeps our dependency list minimal.
"""

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# This is the public Apify actor ID for LinkedIn Jobs scraping.
# You can view it at: https://apify.com/bebity/linkedin-jobs-scraper
ACTOR_ID = "bebity/linkedin-jobs-scraper"

# Apify REST base URL
BASE_URL = "https://api.apify.com/v2"

# Keywords we want to search for on LinkedIn Jobs.
# Keep this list short — each keyword = API calls.
KEYWORDS = [
    "treasury director",
    "trésorier groupe",
    "head of payments",
    "responsable paiements",
    "fintech PSP",
    "ISO 20022",
    "SEPA open banking",
]

# We only care about France for now.
LOCATION = "France"

# Max results per keyword. 5 × 7 keywords = 35 LinkedIn results per run.
RESULTS_PER_KEYWORD = 5


def fetch(apify_token: str) -> list[dict]:
    """
    Run the LinkedIn Jobs actor on Apify and return raw job postings.

    Returns a list of dicts, each with keys:
        raw_title, raw_company, raw_location, raw_date, raw_description, source, url

    On any error: logs the problem and returns an empty list (never crashes the run).
    """
    try:
        return _run_actor(apify_token)
    except Exception as exc:
        logger.error("LinkedIn source failed: %s", exc)
        return []


def _run_actor(token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}

    all_results = []

    for keyword in KEYWORDS:
        # ── Step 1: Start the actor run ───────────────────────────────────────
        run_resp = httpx.post(
            f"{BASE_URL}/acts/{ACTOR_ID}/runs",
            headers=headers,
            json={
                "queries": keyword,
                "location": LOCATION,
                "maxResults": RESULTS_PER_KEYWORD,
            },
            timeout=30,
        )
        run_resp.raise_for_status()
        run_id = run_resp.json()["data"]["id"]
        logger.info("LinkedIn actor started | keyword=%s | run_id=%s", keyword, run_id)

        # ── Step 2: Poll until the run finishes ───────────────────────────────
        _wait_for_run(run_id, headers)

        # ── Step 3: Fetch results from the run's dataset ──────────────────────
        dataset_resp = httpx.get(
            f"{BASE_URL}/actor-runs/{run_id}/dataset/items",
            headers=headers,
            params={"format": "json"},
            timeout=30,
        )
        dataset_resp.raise_for_status()
        items = dataset_resp.json()

        # ── Step 4: Normalize to our standard shape ───────────────────────────
        for item in items:
            all_results.append({
                "raw_title": item.get("title", ""),
                "raw_company": item.get("companyName", ""),
                "raw_location": item.get("location", ""),
                "raw_date": item.get("postedAt", ""),
                "raw_description": item.get("description", "")[:1000],  # cap length
                "source": "linkedin",
                "url": item.get("url", ""),
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

        time.sleep(5)  # wait 5 seconds before next poll

    raise TimeoutError(f"Apify run {run_id} did not finish within {max_wait}s")
