import os
import random
import time

import requests
from .logger import get_logger

logger = get_logger(__name__)

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_MAX_RETRIES = 3


def _brave_request(
    params: dict,
    api_key: str,
    accept: str = "application/json",
) -> requests.Response | None:
    """Send a Brave Search request with retry + exponential backoff on 429.

    A small random jitter (0-2 s) is added before the first attempt to
    spread out concurrent requests from parallel region fan-out.
    """
    headers = {
        "Accept": accept,
        "X-Subscription-Token": api_key,
    }

    time.sleep(random.uniform(0, 2.0))

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(
                _BRAVE_URL, headers=headers, params=params, timeout=15,
            )
            if resp.status_code == 429:
                wait = (2 ** (attempt + 1)) + random.uniform(0, 1)
                logger.info(
                    "Brave 429 (attempt %d/%d), retrying in %.1fs…",
                    attempt + 1, _MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError:
            raise
        except requests.exceptions.RequestException as exc:
            if attempt < _MAX_RETRIES - 1:
                wait = (2 ** (attempt + 1)) + random.uniform(0, 1)
                logger.info("Brave network error, retrying in %.1fs: %s", wait, exc)
                time.sleep(wait)
            else:
                raise

    return None


def brave_search(query: str, count: int = 5, freshness: str = "") -> str:
    """Single implementation of Brave Search used across the whole project.

    Args:
        query: The search query string.
        count: Number of results to request (max 20).
        freshness: Optional freshness filter (e.g. ``"pw"`` for past week,
                   ``"pm"`` for past month).

    Returns:
        A newline-joined string of ``"- title: description"`` lines, or an
        error message.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        logger.warning("BRAVE_API_KEY not set – search skipped")
        return "No Brave API key found."

    params: dict = {"q": query, "count": count}
    if freshness:
        params["freshness"] = freshness

    try:
        resp = _brave_request(params, api_key)
        if resp is None:
            logger.warning("Brave rate-limit exhausted for query: %s", query)
            return "Rate limit hit – try again shortly."

        results = resp.json().get("web", {}).get("results", [])
        if not results:
            logger.info("Brave returned 0 results for: %s", query)
            return "No results found."

        return "\n".join(
            f"- {r.get('title', '')}: {r.get('description', '')}"
            for r in results
        )

    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            logger.warning("Brave rate-limit hit for query: %s", query)
            return "Rate limit hit – try again shortly."
        logger.error("Brave HTTP error: %s", exc)
        return f"Search HTTP error: {exc}"
    except requests.exceptions.RequestException as exc:
        logger.error("Brave network error: %s", exc)
        return f"Search error: {exc}"


def brave_search_raw(query: str, count: int = 10, freshness: str = "pm") -> str:
    """Return the raw combined text of titles + descriptions.

    Useful for regex-based ticker extraction in niche_hunter / scanner.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        logger.warning("BRAVE_API_KEY not set – raw search skipped")
        return ""

    params: dict = {"q": query, "count": count}
    if freshness:
        params["freshness"] = freshness

    try:
        resp = _brave_request(params, api_key)
        if resp is None:
            logger.warning("Brave rate-limit exhausted for raw query")
            return ""

        results = resp.json().get("web", {}).get("results", [])
        return " ".join(f"{r['title']} {r['description']}" for r in results)
    except requests.exceptions.RequestException as exc:
        logger.error("Brave raw-search error: %s", exc)
        return ""
