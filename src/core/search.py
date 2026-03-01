import os
import requests
from .logger import get_logger

logger = get_logger(__name__)


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

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }
    params: dict = {"q": query, "count": count}
    if freshness:
        params["freshness"] = freshness

    try:
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers=headers,
            params=params,
            timeout=15,
        )
        response.raise_for_status()

        results = response.json().get("web", {}).get("results", [])
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

    headers = {"X-Subscription-Token": api_key}
    params: dict = {"q": query, "count": count}
    if freshness:
        params["freshness"] = freshness

    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return " ".join(f"{r['title']} {r['description']}" for r in results)
    except requests.exceptions.RequestException as exc:
        logger.error("Brave raw-search error: %s", exc)
        return ""
