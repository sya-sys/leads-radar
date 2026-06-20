"""
src/sources/apify_france_travail.py
────────────────────────────────────
Fetches job postings from France Travail (ex-Pôle Emploi) via Apify.

Actor: https://apify.com/drobnikj/france-travail-scraper
Same pattern as apify_linkedin.py — start run → poll → fetch dataset → normalize.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

ACTOR_ID = "drobnikj~france-travail-scraper"
BASE_URL = "https://api.apify.com/v2"

# France Travail-specific keywords (more French-language weighted)
KEYWORDS = [
    "trésorerie",
    "cash management",
    "paiements PSP",
    "responsable paiements",
    "fintech SEPA",
    "open banking",
]

RESULTS_PER_KEYWORD = 5


def fetch(apify_token: str) -> list[dict]:
    """Fetch France Travail postings. Returns [] on any error."""
    try:
        return _run_actor(apify_token)
    except Exception as exc:
        logger.error("France Travail source failed: %s", exc)
        return []


def _run_actor(token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    all_results = []

    for keyword in KEYWORDS:
        run_resp = httpx.post(
            f"{BASE_URL}/acts/{ACTOR_ID}/runs",
            headers=headers,
            json={
                "keyword": keyword,
                "maxItems": RESULTS_PER_KEYWORD,
            },
            timeout=30,
        )
        run_resp.raise_for_status()
        run_id = run_resp.json()["data"]["id"]
        logger.info("FranceTravail actor started | keyword=%s | run_id=%s", keyword, run_id)

        _wait_for_run(run_id, headers)

        dataset_resp = httpx.get(
            f"{BASE_URL}/actor-runs/{run_id}/dataset/items",
            headers=headers,
            params={"format": "json"},
            timeout=30,
        )
        dataset_resp.raise_for_status()
        items = dataset_resp.json()

        for item in items:
            all_results.append({
                "raw_title": item.get("intitule", ""),
                "raw_company": item.get("entreprise", {}).get("nom", ""),
                "raw_location": item.get("lieuTravail", {}).get("libelle", ""),
                "raw_date": item.get("dateCreation", ""),
                "raw_description": item.get("description", "")[:1000],
                "source": "france_travail",
                "url": item.get("origineOffre", {}).get("urlOrigine", ""),
            })

    logger.info("France Travail: fetched %d postings", len(all_results))
    return all_results


def _wait_for_run(run_id: str, headers: dict, max_wait: int = 120) -> None:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = httpx.get(f"{BASE_URL}/actor-runs/{run_id}", headers=headers, timeout=10)
        resp.raise_for_status()
        status = resp.json()["data"]["status"]
        if status == "SUCCEEDED":
            return
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended with status: {status}")
        time.sleep(5)
    raise TimeoutError(f"Apify run {run_id} did not finish within {max_wait}s")
