import os
import finnhub
import requests
from datetime import datetime, timedelta
from langchain_core.tools import tool
from src.core.logger import get_logger

logger = get_logger(__name__)

# --- SECTOR-SPECIFIC RULES ---
SECTOR_CONFIG = {
    "Financial Services": {
        "type": "bank",
        "require_pb_under_one": True,
        "check_debt": False,
        "zombie_filter": False,
    },
    "Technology": {
        "type": "growth",
        "require_pb_under_one": False,
        "check_debt": True,
        "debt_max_ebitda": 3.5,
        "zombie_filter": True,
    },
    "Healthcare": {
        "type": "growth",
        "require_pb_under_one": False,
        "check_debt": True,
        "debt_max_ebitda": 3.5,
        "zombie_filter": True,
    },
    "Default": {
        "type": "standard",
        "require_pb_under_one": False,
        "check_debt": True,
        "debt_max_ebitda": 3.5,
        "zombie_filter": False,
    },
}


def calculate_graham_number(info: dict) -> float:
    """Classic Value Investing: sqrt(22.5 * EPS * BookValue)."""
    try:
        eps = info.get("trailingEps", 0) or 0
        bvps = info.get("bookValue", 0) or 0

        if eps <= 0 or bvps <= 0:
            return 0

        return (22.5 * eps * bvps) ** 0.5
    except (TypeError, ValueError):
        return 0


def check_financial_health(ticker: str, info: dict) -> dict:
    """Evaluate a company's financial health based on its sector.

    Returns:
        {"status": "PASS"/"FAIL", "reason": "...", "metrics": {...}}
    """
    try:
        sector = info.get("sector", "Default")
        config = SECTOR_CONFIG.get(sector, SECTOR_CONFIG["Default"])

        current_price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
        currency = info.get("currency", "USD")

        if ticker.endswith(".L") or currency in ("GBp", "GBX"):
            current_price = current_price / 100

        # 1. Financial Services (Banks)
        if config["type"] == "bank":
            pb_ratio = info.get("priceToBook", 0)
            if config["require_pb_under_one"] and (pb_ratio is None or pb_ratio > 1.2):
                return {
                    "status": "FAIL",
                    "reason": f"Financials Reject: P/B is {pb_ratio} (needs near or under 1.0)",
                    "metrics": {"sector": sector},
                }
            current_ratio = info.get("currentRatio")
            if current_ratio and current_ratio < 0.8:
                return {
                    "status": "FAIL",
                    "reason": f"Bank Reject: low liquidity (Current Ratio {current_ratio} < 0.8)",
                    "metrics": {"sector": sector},
                }

        # 2. Zombie Filter (Tech/Healthcare cash runway)
        if config["zombie_filter"]:
            fcf = info.get("freeCashflow", 0)
            cash = info.get("totalCash", 0)
            if fcf is not None and cash is not None and fcf < 0:
                yearly_burn = abs(fcf)
                if yearly_burn > 0:
                    runway_years = cash / yearly_burn
                    if runway_years < 0.5:
                        return {
                            "status": "FAIL",
                            "reason": "Zombie Reject: burning cash with < 6 months runway",
                            "metrics": {"sector": sector, "runway_years": round(runway_years, 2)},
                        }

        # 3. Classic Debt Filter (Industrials/Default)
        if config["check_debt"] and config["type"] == "standard":
            ebitda = info.get("ebitda")
            debt = info.get("totalDebt")
            cash = info.get("totalCash")
            if ebitda and debt and ebitda > 0:
                net_debt_ebitda = (debt - (cash or 0)) / ebitda
                if net_debt_ebitda > config["debt_max_ebitda"]:
                    return {
                        "status": "FAIL",
                        "reason": f"Debt Reject: Net Debt/EBITDA is {net_debt_ebitda:.2f}x > {config['debt_max_ebitda']}x",
                        "metrics": {"sector": sector},
                    }

        # 4. Intrinsic Value & Safety Margin
        intrinsic_val = calculate_graham_number(info)
        margin_of_safety = "N/A"

        if intrinsic_val > 0 and current_price > 0:
            raw_margin = (intrinsic_val - current_price) / intrinsic_val * 100
            margin_of_safety = f"{round(raw_margin, 1)}%"
        elif intrinsic_val == 0:
            margin_of_safety = "No Value (Unprofitable)"

        metrics = {
            "sector": sector,
            "current_price": current_price,
            "intrinsic_value": round(intrinsic_val, 2),
            "margin_of_safety": margin_of_safety,
        }

        return {"status": "PASS", "reason": f"Passed {sector} Gatekeeper.", "metrics": metrics}

    except Exception as exc:
        logger.error("Health check error for %s: %s", ticker, exc)
        return {"status": "FAIL", "reason": f"Data Extraction Error: {exc}", "metrics": {}}


# --- FINNHUB TOOLS ---

def get_finnhub_client():
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        raise ValueError("FINNHUB_API_KEY not set")
    return finnhub.Client(api_key=api_key)


@tool
def get_insider_sentiment(ticker: str) -> str:
    """Fetch recent insider sentiment and trading behavior for a US stock."""
    try:
        if "." in ticker:
            return f"Insider data not supported for non-US ticker {ticker}."

        api_key = os.getenv("FINNHUB_API_KEY")
        if not api_key:
            return "FINNHUB_API_KEY missing."

        url = f"https://finnhub.io/api/v1/stock/insider-sentiment?symbol={ticker}&from=2024-01-01&to=2026-12-31&token={api_key}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "data" not in data or not data["data"]:
            return f"No recent insider sentiment data for {ticker}."

        recent = data["data"][0]
        msp = recent.get("mspr", 0)

        if msp > 0:
            sentiment = "Positive (Insiders Buying)"
        elif msp < 0:
            sentiment = "Negative (Insiders Selling)"
        else:
            sentiment = "Neutral"

        return f"Insider Sentiment for {ticker}: {sentiment}. MSPR Score: {msp}."

    except requests.exceptions.RequestException as exc:
        logger.warning("Insider sentiment request failed for %s: %s", ticker, exc)
        return f"Error fetching insider data: {exc}"
    except Exception as exc:
        logger.error("Unexpected insider sentiment error for %s: %s", ticker, exc)
        return f"Error: {exc}"


@tool
def get_company_news(ticker: str) -> str:
    """Fetch the top 3 most recent financial news headlines for a US stock."""
    try:
        if "." in ticker:
            return f"Finnhub news not supported for non-US ticker {ticker}."

        client = get_finnhub_client()
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        news = client.company_news(ticker, _from=start_date, to=end_date)
        if not news:
            return f"No recent news for {ticker}."

        headlines = []
        for i, article in enumerate(news[:3]):
            headlines.append(
                f"{i + 1}. {article.get('headline', 'No Headline')} - {article.get('summary', '')}"
            )

        return f"Recent News for {ticker}:\n" + "\n".join(headlines)

    except Exception as exc:
        logger.warning("Company news error for %s: %s", ticker, exc)
        return f"Error fetching news: {exc}"


@tool
def get_basic_financials(ticker: str) -> str:
    """Fetch deep fundamental metrics for a US stock."""
    try:
        if "." in ticker:
            return f"Finnhub fundamentals not supported for non-US ticker {ticker}."

        client = get_finnhub_client()
        data = client.company_basic_financials(ticker, "all")
        if not data or "metric" not in data:
            return f"No fundamental data for {ticker}."

        metrics = data["metric"]
        report = f"Fundamentals for {ticker}:\n"
        report += f"- 52 Week High: ${metrics.get('52WeekHigh', 'N/A')}\n"
        report += f"- 52 Week Low: ${metrics.get('52WeekLow', 'N/A')}\n"
        report += f"- Beta: {metrics.get('beta', 'N/A')}\n"
        report += f"- Gross Margin TTM: {metrics.get('grossMarginTTM', 'N/A')}%\n"
        report += f"- ROE TTM: {metrics.get('roeTTM', 'N/A')}%\n"

        return report

    except Exception as exc:
        logger.warning("Basic financials error for %s: %s", ticker, exc)
        return f"Error fetching financials: {exc}"
