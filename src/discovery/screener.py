"""yFinance-based micro-cap screener (Proposal A).

Replaces the noisy Brave-search-then-LLM-extract approach with direct
programmatic screening.  Brave Search is still used as a *supplementary*
trending signal in the scout node.
"""

import random
import yfinance as yf
from src.core.logger import get_logger
from src.core.ticker_utils import normalize_price

logger = get_logger(__name__)

MAX_MARKET_CAP = 300_000_000
MIN_MARKET_CAP = 10_000_000
MAX_PRICE = 30.00

# Pre-curated universe of US micro-cap-heavy indices / lists.
# yfinance can pull constituents for some indices; for broader coverage
# we maintain seed lists that get refreshed via Brave trending data.
_SEED_POOLS: dict[str, list[str]] = {
    "USA": [
        # Russell Micro-Cap sampling – real tickers updated periodically
        "BSFC", "CEAD", "STRM", "GHSI", "INBS", "TTOO", "ARDS", "APRE",
        "WBUY", "SLNH", "PKBO", "SNCE", "TPST", "EDBL", "SOPA", "RCAT",
        "BMEA", "JCSE", "PROC", "VBLT", "ATHE", "SXTC", "REVB", "NUVB",
        "HNVR", "COYA", "MNTS", "GWAV", "AEHL", "REBN",
    ],
    "UK": [
        "AFC.L", "BOTB.L", "CML.L", "DUKE.L", "FLO.L", "GAW.L",
        "JET2.L", "KIE.L", "PURP.L", "SDI.L", "TET.L", "WINK.L",
    ],
    "Canada": [
        "QUIS.V", "NCI.TO", "CHE.UN.TO", "TVE.TO", "CJ.TO",
        "BYL.V", "FPC.TO", "GBR.V", "RHC.V", "STC.V",
    ],
    "Australia": [
        "VUL.AX", "PEN.AX", "LKE.AX", "NVX.AX", "RNU.AX",
        "SYA.AX", "GL1.AX", "EMN.AX", "BRK.AX", "ADN.AX",
    ],
}


def screen_microcaps(
    region: str = "USA",
    extra_tickers: list[str] | None = None,
    max_results: int = 20,
) -> list[dict]:
    """Screen for micro-cap stocks using yFinance data.

    Args:
        region: Market region key (USA, UK, Canada, Australia).
        extra_tickers: Additional ticker symbols to include (e.g. from
            Brave trending search).  Merged with the seed pool.
        max_results: Maximum candidates to return.

    Returns:
        List of dicts with keys: ticker, price, market_cap, eps,
        book_value, sector, free_cashflow, total_cash, currency, info.
    """
    pool = list(_SEED_POOLS.get(region, []))
    if extra_tickers:
        pool.extend(t for t in extra_tickers if t not in pool)

    random.shuffle(pool)

    passed: list[dict] = []

    for ticker in pool:
        if len(passed) >= max_results:
            break
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            mkt_cap = info.get("marketCap", 0) or 0
            if not (MIN_MARKET_CAP < mkt_cap < MAX_MARKET_CAP):
                continue

            price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
            currency = info.get("currency", "USD")
            price = normalize_price(price, ticker, currency)

            if price <= 0 or price > MAX_PRICE:
                continue

            passed.append({
                "ticker": ticker,
                "price": price,
                "market_cap": mkt_cap,
                "eps": info.get("trailingEps", 0) or 0,
                "book_value": info.get("bookValue", 0) or 0,
                "sector": info.get("sector", "Unknown"),
                "free_cashflow": info.get("freeCashflow", 0) or 0,
                "total_cash": info.get("totalCash", 0) or 0,
                "ebitda": info.get("ebitda", 0) or 0,
                "total_debt": info.get("totalDebt", 0) or 0,
                "current_ratio": info.get("currentRatio", 0) or 0,
                "price_to_book": info.get("priceToBook", 0) or 0,
                "short_name": info.get("shortName", ticker),
                "currency": currency,
                "info": info,
            })
            logger.info("Screener PASS: %s  cap=$%s  price=$%.2f", ticker, f"{mkt_cap:,.0f}", price)

        except Exception as exc:
            logger.debug("Screener skip %s: %s", ticker, exc)
            continue

    logger.info("Screener returned %d candidates for %s", len(passed), region)
    return passed


def get_trending_tickers_from_brave(region: str = "USA") -> list[str]:
    """Use Brave Search to discover currently-trending micro-cap tickers.

    These are merged into the screener pool so the system still benefits
    from web-sourced discovery while using yFinance for validation.
    """
    from src.core.search import brave_search_raw
    from src.core.ticker_utils import extract_tickers

    queries = [
        f"best undervalued microcap stocks {region} 2026",
        f"hidden gem penny stocks {region} insider buying",
        f"small cap stocks breaking out {region} this week",
        f"reddit microcap stocks {region} deep value",
        f"unusual volume small cap {region} today",
    ]

    raw_text = brave_search_raw(random.choice(queries), count=15, freshness="pw")
    if not raw_text:
        return []

    candidates = extract_tickers(raw_text)
    logger.info("Brave trending found %d raw candidates for %s", len(candidates), region)
    return candidates[:20]
