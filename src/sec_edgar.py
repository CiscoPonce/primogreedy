"""SEC EDGAR integration — fetches 10-K/10-Q filings and extracts
MD&A (Item 7) and Risk Factors (Item 1A) for the analyst prompt.

Uses the EDGAR EFTS full-text search API (same endpoint used in
``src/discovery/insider_feed.py``) and BeautifulSoup for HTML parsing.
"""

import re
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from langchain_core.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.core.logger import get_logger

logger = get_logger(__name__)

_SEC_HEADERS = {
    "User-Agent": "PrimoGreedy/1.0 (contact@primogreedy.com)",
    "Accept": "application/json",
}
_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
_MAX_SECTION_CHARS = 2000
_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=_MAX_SECTION_CHARS, chunk_overlap=200
)

# Regex patterns for section headers in 10-K/10-Q filings
_MDA_PATTERN = re.compile(
    r"Item\s*7[\.\s\—\-]+.*?Management.s\s+Discussion|"
    r"Item\s*7[\.\s\—\-]|"
    r"Management.s\s+Discussion\s+and\s+Analysis",
    re.IGNORECASE,
)
_RISK_PATTERN = re.compile(
    r"Item\s*1A[\.\s\—\-]+.*?Risk\s+Factors|"
    r"Item\s*1A[\.\s\—\-]|"
    r"Risk\s+Factors",
    re.IGNORECASE,
)
_NEXT_ITEM_PATTERN = re.compile(r"Item\s*\d+[A-Z]?[\.\s\—\-]", re.IGNORECASE)


# ---------------------------------------------------------------------------
# EFTS search — find the most recent 10-K or 10-Q for a ticker
# ---------------------------------------------------------------------------

def _search_filings(ticker: str) -> dict | None:
    """Query EDGAR EFTS for the most recent annual/quarterly filing.

    Returns the first hit as a dict with ``file_url``, ``form_type``,
    ``file_date``, ``company_name``, or *None* if nothing found.
    """
    two_years_ago = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    params = {
        "q": "",
        "forms": "10-K,10-Q",
        "dateRange": "custom",
        "startdt": two_years_ago,
        "enddt": today,
        "tickers": ticker,
    }

    try:
        resp = requests.get(_EFTS_URL, params=params, headers=_SEC_HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.info("EDGAR EFTS returned %d for %s", resp.status_code, ticker)
            return None

        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return None

        src = hits[0].get("_source", {})
        file_num = src.get("file_num", "")
        accession = (
            src.get("accession_no", "")
            .replace("-", "")
        )

        primary_doc = src.get("file_description", "")
        if not primary_doc:
            primary_doc = src.get("display_names", [""])[0] if src.get("display_names") else ""

        entity_id = src.get("entity_id", "")

        return {
            "form_type": src.get("form_type", "10-K"),
            "file_date": src.get("file_date", ""),
            "company_name": src.get("display_names", [""])[0] if src.get("display_names") else ticker,
            "entity_id": entity_id,
            "accession": accession,
            "file_num": file_num,
        }

    except requests.RequestException as exc:
        logger.warning("EDGAR EFTS request failed for %s: %s", ticker, exc)
        return None


def _fetch_filing_index(entity_id: str, accession: str) -> str | None:
    """Fetch the filing index page and return the URL of the primary HTML document."""
    if not entity_id or not accession:
        return None

    index_url = (
        f"https://www.sec.gov/Archives/edgar/data/{entity_id}/{accession}/"
    )

    time.sleep(0.5)

    try:
        resp = requests.get(
            index_url,
            headers={**_SEC_HEADERS, "Accept": "text/html"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if href.endswith(".htm") or href.endswith(".html"):
                if not href.startswith("http"):
                    href = f"https://www.sec.gov{href}" if href.startswith("/") else f"{index_url}{href}"
                return href

        return None

    except requests.RequestException as exc:
        logger.warning("EDGAR filing index fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# HTML parser — extract MD&A and Risk Factors sections
# ---------------------------------------------------------------------------

def _extract_section(full_text: str, start_pattern: re.Pattern, label: str) -> str:
    """Find a section by *start_pattern* and return text up to the next Item header."""
    match = start_pattern.search(full_text)
    if not match:
        return ""

    start = match.end()
    remainder = full_text[start:]

    end_match = _NEXT_ITEM_PATTERN.search(remainder, pos=200)
    if end_match:
        section_text = remainder[: end_match.start()]
    else:
        section_text = remainder[:_MAX_SECTION_CHARS * 2]

    section_text = section_text.strip()
    if not section_text:
        return ""

    if len(section_text) > _MAX_SECTION_CHARS:
        chunks = _SPLITTER.split_text(section_text)
        section_text = chunks[0] if chunks else section_text[:_MAX_SECTION_CHARS]

    return section_text


def parse_mda_risk_factors(html: str) -> str:
    """Extract MD&A and Risk Factors from a 10-K/10-Q HTML document.

    Returns a formatted string ready for ``{sec_context}`` in the prompt,
    or an empty string if extraction fails.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "meta", "link"]):
            tag.decompose()

        full_text = soup.get_text(separator="\n", strip=True)

        mda = _extract_section(full_text, _MDA_PATTERN, "MD&A")
        risk = _extract_section(full_text, _RISK_PATTERN, "Risk Factors")

        if not mda and not risk:
            return ""

        parts = ["SEC FILING GROUND TRUTH:"]
        if mda:
            parts.append(f"\nMD&A SUMMARY (Item 7):\n{mda}")
        if risk:
            parts.append(f"\nRISK FACTORS (Item 1A):\n{risk}")

        return "\n".join(parts)

    except Exception as exc:
        logger.warning("SEC filing parse error: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Public @tool — used by analyst_node
# ---------------------------------------------------------------------------

@tool
def get_sec_filings(ticker: str) -> str:
    """Fetch the most recent 10-K or 10-Q filing from SEC EDGAR and extract
    MD&A and Risk Factors sections for investment analysis.

    Args:
        ticker: US stock ticker symbol (e.g. AAPL, MSFT)
    """
    if "." in ticker:
        return ""

    filing = _search_filings(ticker)
    if not filing:
        logger.info("No SEC filings found for %s", ticker)
        return ""

    logger.info(
        "Found %s for %s (filed %s)",
        filing["form_type"], ticker, filing["file_date"],
    )

    doc_url = _fetch_filing_index(filing["entity_id"], filing["accession"])
    if not doc_url:
        return (
            f"SEC FILING GROUND TRUTH:\n"
            f"Found {filing['form_type']} filed {filing['file_date']} "
            f"but could not retrieve document."
        )

    time.sleep(0.5)

    try:
        resp = requests.get(
            doc_url,
            headers={**_SEC_HEADERS, "Accept": "text/html"},
            timeout=30,
        )
        if resp.status_code != 200:
            return ""

        if len(resp.text) > 5_000_000:
            logger.info("SEC filing too large (%d bytes), truncating", len(resp.text))
            html = resp.text[:5_000_000]
        else:
            html = resp.text

    except requests.RequestException as exc:
        logger.warning("SEC filing fetch failed for %s: %s", ticker, exc)
        return ""

    result = parse_mda_risk_factors(html)

    if result:
        header = f"[Source: {filing['form_type']} filed {filing['file_date']}]"
        return f"{result}\n{header}"

    return ""
