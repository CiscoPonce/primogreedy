"""Micro-cap screener.

Discovery now runs in two stages:

  1. **Dynamic whole-market scan** (primary) — uses the Yahoo Finance
     screener (``yfinance.EquityQuery`` + ``yf.screen``) to pull *every*
     equity in a region that matches the market-cap / price band plus a
     liquidity floor.  This replaces the old behaviour where discovery was
     limited to a hand-curated ~40-ticker seed list and could therefore never
     surface a gem outside that list.

  2. **Seed pool + web signals** (fallback / augmentation) — the historical
     seed lists and Brave trending tickers are still merged in so nothing that
     used to be found is lost, and so the pipeline still works if the Yahoo
     screener endpoint is unavailable.

Every candidate — dynamic or seed — must pass the same hard liquidity and
quality filters before it is allowed into the expensive analyst step.
"""

import random

import yfinance as yf

from src.core.logger import get_logger
from src.core.ticker_utils import normalize_price, currency_to_usd

try:  # yfinance >= 1.6 provides the screener query API
    from yfinance import EquityQuery  # noqa: F401
    _HAS_SCREENER = True
except Exception:  # pragma: no cover - very old yfinance
    EquityQuery = None
    _HAS_SCREENER = False

logger = get_logger(__name__)

MAX_MARKET_CAP = 500_000_000
MIN_MARKET_CAP = 5_000_000
MAX_PRICE = 30.00

# --- Liquidity / quality floors (new) -------------------------------------
# Micro-caps with no trading volume cannot be entered or exited without
# moving the price, so they must never consume the region's analyst slot.
MIN_AVG_DOLLAR_VOLUME = 150_000      # 3-month avg price * volume, in USD
MIN_AVG_VOLUME = 25_000              # 3-month average shares/day
MIN_PRICE = 0.10                     # sub-penny shells are untradeable
MAX_SHORT_PCT_FLOAT = 0.30           # >30% short -> squeeze/borrow risk
MAX_INTRADAY_MOVE = 0.50             # >50% move -> likely pump; not a value entry
MIN_PUMP_VOL_RATIO = 3.0             # intraday vol >3x normal is suspicious when spiking

# Region-specific adjustments (UK market is thinner)
_REGION_LIQUIDITY_ADJUSTMENTS = {
    "UK": {"min_dollar_volume": 50_000, "min_avg_volume": 10_000},
}

# Yahoo region codes accepted by the screener's `region` field.
_YF_REGION = {
    "USA": "us",
    "UK": "gb",
    "Canada": "ca",
    "Australia": "au",
}

# Map Yahoo exchange codes we consider acceptable per region (avoids OTC
# pink-sheet shells for US and keeps listings to the primary boards).
_ALLOWED_EXCHANGES = {
    "USA": {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS", "NAS", "NYS"},
    "UK": {"LSE", "IOB", "AQS"},
    "Canada": {"TOR", "VAN", "CNQ", "NEO"},
    "Australia": {"ASX"},
}

# Pre-curated universe of micro-cap-heavy indices / lists.
# Kept as a fallback for when the dynamic screener is unavailable and as a
# guaranteed-minimum pool that is merged into every dynamic scan.
_SEED_POOLS: dict[str, list[str]] = {
    "USA": [
        # Russell Micro-Cap sampling — audited 2026-03-05, expanded for depth
        "BSFC", "INBS", "TTOO", "ARDS", "APRE",
        "WBUY", "SLNH", "TPST", "EDBL", "RCAT",
        "BMEA", "JCSE", "ATHE", "SXTC", "NUVB",
        "HNVR", "COYA", "MNTS", "GWAV", "AEHL",
        "BNGO", "SNDL", "CTRM", "BYND", "DM",
        "RENT", "RIOT", "HUT", "MRIN", "SESN",
        "SPCE", "QNRX", "CVM", "CRBP", "SENS",
        "CRNC", "NKLA", "LAKE", "INVZ", "FDMT",
    ],
    "UK": [
        # LSE micro-caps — audited 2026-03-05, expanded
        "AFC.L", "CML.L", "DUKE.L", "FLO.L", "GAW.L",
        "JET2.L", "KIE.L", "SDI.L", "WINK.L",
        "ROO.L", "ATST.L", "SGRO.L", "ICG.L",
        "MKS.L", "BAB.L", "JLP.L",
    ],
    "Canada": [
        # TSX/TSXV micro-caps — audited 2026-03-05, expanded
        "TVE.TO", "CJ.TO", "HWO.TO", "NPK.TO",
        "BIGG.V", "FPC.V", "ZEN.V", "STUD.V", "GIGA.V",
        "LIO.V", "HPQ.V", "BSK.V",
        "CCW.V", "MNR.V", "GLO.V", "KNT.V", "FVI.V",
        "BTB.UN.TO", "CVE.TO", "BTE.TO", "SU.TO",
    ],
    "Australia": [
        # ASX micro-caps — audited 2026-03-05, expanded
        "VUL.AX", "PEN.AX", "LKE.AX", "NVX.AX", "RNU.AX",
        "GL1.AX", "EMN.AX", "BRK.AX", "ADN.AX",
        "CXO.AX", "SYA.AX", "LTR.AX", "SBR.AX", "BET.AX",
        "IMU.AX", "CTM.AX", "LRS.AX", "PNN.AX",
    ],
}


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _ticker_from_symbol(symbol: str, region: str) -> str:
    """Normalise a Yahoo screener symbol to the project's ticker convention.

    US symbols are returned bare; other regions already carry their Yahoo
    exchange suffix (e.g. ``ABC.L``), matching the seed-pool convention.
    """
    if not symbol:
        return ""
    symbol = symbol.strip().upper()
    if region == "USA":
        # US screener symbols sometimes arrive with an index suffix (BRK-B).
        return symbol.replace("-", ".")
    return symbol


def _candidate_from_quote(quote: dict, region: str) -> dict | None:
    """Convert a raw Yahoo screener quote into the canonical candidate dict.

    Applies the hard liquidity/quality filters.  Returns *None* when the
    quote should not proceed (so callers can simply filter falsy values).
    """
    ticker = _ticker_from_symbol(quote.get("symbol", ""), region)
    if not ticker:
        return None

    price = quote.get("regularMarketPrice") or quote.get("fulldayPrice") or 0
    currency = quote.get("currency", "USD")
    price = normalize_price(price, ticker, currency)

    mkt_cap = quote.get("marketCap", 0) or 0
    mkt_cap_usd = currency_to_usd(mkt_cap, currency, ticker)

    avg_vol = (
        quote.get("averageDailyVolume3Month")
        or quote.get("averageDailyVolume10Day")
        or quote.get("regularMarketVolume")
        or 0
    )
    dollar_vol = price * avg_vol

    # Region-specific adjustments
    adj = _REGION_LIQUIDITY_ADJUSTMENTS.get(region, {})
    min_dollar_vol = adj.get("min_dollar_volume", MIN_AVG_DOLLAR_VOLUME)
    min_avg_vol = adj.get("min_avg_volume", MIN_AVG_VOLUME)

    # --- Hard liquidity / quality gates -----------------------------------
    if not (MIN_PRICE <= price <= MAX_PRICE):
        return None
    if not (MIN_MARKET_CAP < mkt_cap_usd < MAX_MARKET_CAP):
        return None
    if avg_vol < min_avg_vol:
        logger.debug("Liquidity drop %s: avg vol %s < %s", ticker, avg_vol, min_avg_vol)
        return None
    if dollar_vol < min_dollar_vol:
        logger.debug("Liquidity drop %s: $vol %.0f < %s", ticker, dollar_vol, min_dollar_vol)
        return None

    short_pct = quote.get("shortPercentOfFloat")
    if short_pct and short_pct > MAX_SHORT_PCT_FLOAT:
        logger.debug("Quality drop %s: short %% float %.0f%%", ticker, short_pct * 100)
        return None

    # --- Pump / blow-off guard -------------------------------------------
    # A violent intraday spike on abnormal volume is a pump, not a value
    # entry; buying the top of one is how micro-cap hunters get hurt.
    change_pct = quote.get("regularMarketChangePercent")
    intraday_vol = quote.get("regularMarketVolume") or 0
    if change_pct is not None and change_pct >= MAX_INTRADAY_MOVE * 100:
        if avg_vol and intraday_vol > avg_vol * MIN_PUMP_VOL_RATIO:
            logger.debug(
                "Pump guard drop %s: +%.0f%% on %.1fx volume",
                ticker, change_pct, intraday_vol / avg_vol,
            )
            return None

    eps = quote.get("epsTrailingTwelveMonths", 0) or 0
    book_value = quote.get("bookValue", 0) or 0

    return {
        "ticker": ticker,
        "price": price,
        "market_cap": mkt_cap,
        "market_cap_usd": mkt_cap_usd,
        "eps": eps,
        "book_value": book_value,
        "sector": quote.get("sector") or quote.get("sectorDisp") or "Unknown",
        "free_cashflow": 0,
        "total_cash": 0,
        "ebitda": 0,
        "total_debt": 0,
        "current_ratio": 0,
        "price_to_book": quote.get("priceToBook", 0) or 0,
        "short_name": quote.get("shortName") or quote.get("longName") or ticker,
        "currency": currency,
        "avg_volume": avg_vol,
        "dollar_volume": dollar_vol,
        "short_pct_float": short_pct,
        "exchange": quote.get("exchange", ""),
        "quote_source": "dynamic",
        "info": quote,
    }


# ---------------------------------------------------------------------------
# Dynamic whole-market scan
# ---------------------------------------------------------------------------

def _dynamic_screen(region: str, max_results: int, per_page: int = 100) -> list[dict]:
    """Whole-market micro-cap scan via the Yahoo Finance screener.

    Returns canonical candidate dicts (already liquidity-filtered).  Yields an
    empty list on any failure so the caller can fall back to the seed pool.
    """
    yf_region = _YF_REGION.get(region)
    if not yf_region:
        return []
    if not _HAS_SCREENER:
        logger.info("yfinance screener API unavailable — seed pool only")
        return []

    exchanges = _ALLOWED_EXCHANGES.get(region, set())

    filters = [
        EquityQuery("eq", ["region", yf_region]),
        EquityQuery("gt", ["intradaymarketcap", MIN_MARKET_CAP]),
        EquityQuery("lt", ["intradaymarketcap", MAX_MARKET_CAP]),
        EquityQuery("gt", ["intradayprice", MIN_PRICE]),
        EquityQuery("lt", ["intradayprice", MAX_PRICE]),
        EquityQuery("gt", ["dayvolume", MIN_AVG_VOLUME]),
    ]
    query = EquityQuery("and", filters)

    collected: list[dict] = []
    seen: set[str] = set()
    # Page through a few hundred of the most-liquid names in the band.  We
    # sort by volume descending so the most tradeable micro-caps are seen
    # first, which is exactly what we want to spend our analysis budget on.
    for offset in range(0, min(max_results * 4, 400), per_page):
        try:
            response = yf.screen(
                query,
                size=per_page,
                offset=offset,
                sortField="dayvolume",
                sortAsc=False,
            )
        except Exception as exc:
            if not collected:
                logger.warning("Dynamic screener failed for %s: %s", region, exc)
            break

        quotes = (response or {}).get("quotes", [])
        if not quotes:
            break

        for quote in quotes:
            cand = _candidate_from_quote(quote, region)
            if not cand or cand["ticker"] in seen:
                continue
            if exchanges and cand["exchange"] not in exchanges:
                logger.debug("Exchange drop %s: %s", cand["ticker"], cand["exchange"])
                continue
            seen.add(cand["ticker"])
            collected.append(cand)
            if len(collected) >= max_results:
                return collected

        if len(quotes) < per_page:
            break

    logger.info("Dynamic screener found %d liquid candidates for %s", len(collected), region)
    return collected


# ---------------------------------------------------------------------------
# Seed pool fallback
# ---------------------------------------------------------------------------

def _pre_validate(tickers: list[str]) -> list[str]:
    """Drop tickers that don't resolve on yfinance before adding to the pool.

    Prevents dead/delisted symbols (e.g. delisted micro-caps found by Brave)
    entering the screener pool and wasting the region's single candidate slot.
    """
    valid = []
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            if (info.get("marketCap", 0) or 0) > 0:
                valid.append(t)
            else:
                logger.info("Pre-validation dropped dead ticker: %s", t)
        except Exception as exc:
            logger.debug("Pre-validation skip %s: %s", t, exc)
    return valid


def _screen_seed_pool(
    region: str,
    extra_tickers: list[str] | None = None,
    max_results: int = 20,
) -> list[dict]:
    """Screening fallback: walk the curated seed pool with the same gates."""
    pool = list(_SEED_POOLS.get(region, []))
    if extra_tickers:
        pool.extend(t for t in _pre_validate(list(extra_tickers)) if t not in pool)

    random.shuffle(pool)

    passed: list[dict] = []
    for ticker in pool:
        if len(passed) >= max_results:
            break
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            mkt_cap = info.get("marketCap", 0) or 0
            currency = info.get("currency", "USD")
            mkt_cap_usd = currency_to_usd(mkt_cap, currency, ticker)
            if not (MIN_MARKET_CAP < mkt_cap_usd < MAX_MARKET_CAP):
                continue

            price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
            price = normalize_price(price, ticker, currency)
            if not (MIN_PRICE <= price <= MAX_PRICE):
                continue

            avg_vol = (
                info.get("averageVolume")
                or info.get("averageDailyVolume3Month")
                or info.get("averageVolume10days")
                or 0
            )
            if avg_vol and avg_vol < MIN_AVG_VOLUME:
                continue
            if avg_vol and price * avg_vol < MIN_AVG_DOLLAR_VOLUME:
                continue

            passed.append({
                "ticker": ticker,
                "price": price,
                "market_cap": mkt_cap,
                "market_cap_usd": mkt_cap_usd,
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
                "avg_volume": avg_vol,
                "dollar_volume": price * (avg_vol or 0),
                "short_pct_float": info.get("shortPercentOfFloat"),
                "exchange": "",
                "quote_source": "seed",
                "info": info,
            })
            logger.info("Seed screener PASS: %s  cap=$%s  price=%.2f", ticker, f"{mkt_cap_usd:,.0f}", price)

        except Exception as exc:
            logger.debug("Seed screener skip %s: %s", ticker, exc)
            continue

    logger.info("Seed screener returned %d candidates for %s", len(passed), region)
    return passed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def screen_microcaps(
    region: str = "USA",
    extra_tickers: list[str] | None = None,
    max_results: int = 20,
) -> list[dict]:
    """Screen for liquid micro-cap stocks.

    Primary path is the dynamic whole-market Yahoo screener; the curated seed
    pool (plus any web-sourced ``extra_tickers``) is merged in so discovery
    keeps its historical coverage and survives a screener outage.

    Args:
        region: Market region key (USA, UK, Canada, Australia).
        extra_tickers: Additional ticker symbols to include (e.g. from
            Brave trending search).  Merged with the seed pool.
        max_results: Maximum candidates to return.

    Returns:
        List of canonical candidate dicts (see ``_candidate_from_quote``).
    """
    dynamic = _dynamic_screen(region, max_results=max_results)

    # Seed pool always contributes, so a screener outage degrades gracefully
    # and the historical universe is never lost.
    seed = _screen_seed_pool(
        region,
        extra_tickers=extra_tickers,
        max_results=max_results,
    )

    merged: list[dict] = []
    seen: set[str] = set()
    for cand in dynamic + seed:
        if cand["ticker"] in seen:
            continue
        seen.add(cand["ticker"])
        merged.append(cand)

    logger.info(
        "Screener merged %d candidates for %s (dynamic=%d, seed=%d)",
        len(merged), region, len(dynamic), len(seed),
    )
    return merged[:max_results]


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
        f"microcap stocks insider buying {region} recent filings",
    ]

    raw_text = brave_search_raw(random.choice(queries), count=15, freshness="pw")
    if not raw_text:
        return []

    candidates = extract_tickers(raw_text)
    logger.info("Brave trending found %d raw candidates for %s", len(candidates), region)
    return candidates[:20]
