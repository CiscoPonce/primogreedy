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

def _resolve_cik(ticker: str) -> str | None:
    """Resolve a ticker to a zero-padded 10-digit CIK via EDGAR's company file.

    EDGAR's full-text search does not honour a ``tickers`` filter (it silently
    returns unrelated companies), so we must look the ticker up ourselves and
    instead search/filter by CIK.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        for entry in resp.json().values():
            if str(entry.get("ticker", "")).upper() == ticker.upper():
                return str(entry.get("cik_str", "")).zfill(10)
    except (requests.RequestException, ValueError) as exc:
        logger.warning("EDGAR CIK lookup failed for %s: %s", ticker, exc)
    return None


def _search_filings(ticker: str) -> dict | None:
    """Query EDGAR for the most recent annual/quarterly filing.

    Uses the submissions API (authoritative JSON per CIK) rather than the
    full-text search endpoint, which cannot be filtered by ticker.  Returns a
    dict with ``form_type``, ``file_date``, ``company_name``, ``cik`` and
    ``accession`` (no dashes), or *None* if nothing found.
    """
    cik = _resolve_cik(ticker)
    if not cik:
        logger.info("No CIK found for %s", ticker)
        return None

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.info("EDGAR submissions returned %d for %s", resp.status_code, ticker)
            return None
        data = resp.json()

        company_name = data.get("name", ticker)
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        # `recent` is ordered newest-first; pick the newest 10-K, else 10-Q.
        for wanted in ("10-K", "10-Q"):
            for form, date, accession, primary_doc in zip(
                forms, dates, accessions, primary_docs
            ):
                if form != wanted:
                    continue
                return {
                    "form_type": form,
                    "file_date": date,
                    "company_name": company_name,
                    "cik": str(int(cik)),  # path form drops leading zeros
                    "accession": accession.replace("-", ""),
                    "primary_doc": primary_doc,
                }

        logger.info("No 10-K/10-Q found for %s", ticker)
        return None

    except (requests.RequestException, ValueError) as exc:
        logger.warning("EDGAR submissions request failed for %s: %s", ticker, exc)
        return None


def _fetch_filing_index(cik: str, accession: str, primary_doc: str = "") -> str | None:
    """Return the URL of the primary HTML document for a filing.

    Prefers the known ``primary_doc`` filename from the submissions API; falls
    back to scraping the filing index page.
    """
    if not cik or not accession:
        return None

    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"

    if primary_doc:
        return f"{index_url}{primary_doc}"

    time.sleep(0.5)

    try:
        resp = requests.get(
            index_url,
            headers={**_SEC_HEADERS, "Accept": "text/html"},
            timeout=10,
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

_TOC_END_PATTERN = re.compile(
    r"Item\s*\d+[A-Z]?\s*$|Item\s*\d+[A-Z]?[\.\s]",
    re.IGNORECASE,
)


def _looks_like_prose(text: str) -> bool:
    """Heuristic: does this text look like filing body prose (vs a TOC stub)?

    Table-of-contents entries are dominated by item numbers and page numbers
    (``Item 7A. 44``) whereas real MD&A / Risk Factors prose is sentence-heavy.
    """
    stripped = text.strip()
    if len(stripped) < 200:
        return False
    # Count alphabetic words of length >= 4 (TOC has almost none).
    words = re.findall(r"[A-Za-z]{4,}", stripped)
    return len(words) >= 40


def _extract_section(full_text: str, start_pattern: re.Pattern, label: str) -> str:
    """Find a section by *start_pattern* and return its body text.

    Filings reference each Item at least twice: once in the table of contents
    (followed by a page number) and once at the actual section start (followed
    by prose).  We now iterate over every match, skip the TOC stubs, and return
    the first occurrence that is followed by prose.
    """
    for match in start_pattern.finditer(full_text):
        start = match.end()
        remainder = full_text[start:]

        end_match = _NEXT_ITEM_PATTERN.search(remainder, pos=200)
        if end_match:
            section_text = remainder[: end_match.start()]
        else:
            section_text = remainder[:_MAX_SECTION_CHARS * 2]

        section_text = section_text.strip()
        if not _looks_like_prose(section_text):
            continue

        if len(section_text) > _MAX_SECTION_CHARS:
            chunks = _SPLITTER.split_text(section_text)
            section_text = chunks[0] if chunks else section_text[:_MAX_SECTION_CHARS]
        return section_text

    return ""


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

    doc_url = _fetch_filing_index(
        filing["cik"], filing["accession"], filing.get("primary_doc", "")
    )
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
            timeout=15,
        )
        if resp.status_code != 200:
            return ""

        if len(resp.text) > 2_000_000:
            logger.info("SEC filing too large (%d bytes), truncating", len(resp.text))
            html = resp.text[:2_000_000]
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
