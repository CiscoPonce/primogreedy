import yfinance as yf
import os
import finnhub
from langchain_core.tools import tool

# --- CONFIGURATION: SECTOR SPECIFIC RULES ---
SECTOR_CONFIG = {
    "Financial Services": {
        "type": "bank",
        "require_pb_under_one": True,
        "check_debt": False,
        "zombie_filter": False
    },
    "Technology": {
        "type": "growth",
        "require_pb_under_one": False,
        "check_debt": True,
        "debt_max_ebitda": 3.5,
        "zombie_filter": True
    },
    "Healthcare": {
        "type": "growth",
        "require_pb_under_one": False,
        "check_debt": True,
        "debt_max_ebitda": 3.5,
        "zombie_filter": True
    },
    "Default": {
        "type": "standard",
        "require_pb_under_one": False,
        "check_debt": True,
        "debt_max_ebitda": 3.5,
        "zombie_filter": False
    }
}

def calculate_graham_number(info):
    """
    Classic Value Investing Formula: Sqrt(22.5 * EPS * BookValue)
    """
    try:
        eps = info.get('trailingEps', 0)
        bvps = info.get('bookValue', 0)
        
        # If the company is losing money (Negative EPS), Graham Number is 0
        if eps is None or bvps is None or eps <= 0 or bvps <= 0:
            return 0
            
        return (22.5 * eps * bvps) ** 0.5
    except:
        return 0

def check_financial_health(ticker, info):
    """
    Evaluates a company's financial health based on its sector.
    Returns: {"status": "PASS"/"FAIL", "reason": "...", "metrics": {...}}
    """
    try:
        sector = info.get('sector', 'Default')
        config = SECTOR_CONFIG.get(sector, SECTOR_CONFIG['Default'])
        
        current_price = info.get('currentPrice', 0) or info.get('regularMarketPrice', 0)
        currency = info.get('currency', 'USD')
        
        if ticker.endswith(".L") or currency == "GBp" or currency == "GBX":
            current_price = current_price / 100
            
        # --- 1. SECTOR LOGIC: FINANCIAL SERVICES (BANKS) ---
        if config["type"] == "bank":
            pb_ratio = info.get('priceToBook', 0)
            if config["require_pb_under_one"] and (pb_ratio is None or pb_ratio > 1.2): # 1.2 giving slight premium margin
                return {"status": "FAIL", "reason": f"Financials Reject: Price/Book is {pb_ratio} (Needs to be near or under 1.0 for Graham safety)."}
            current_ratio = info.get('currentRatio')
            if current_ratio and current_ratio < 0.8:
                return {"status": "FAIL", "reason": f"Bank Reject: Dangerously low liquidity (Current Ratio {current_ratio} < 0.8)"}
        
        # --- 2. THE ZOMBIE FILTER (CASH RUNWAY FOR TECH/HEALTHCARE) ---
        if config["zombie_filter"]:
            fcf = info.get('freeCashflow', 0)
            cash = info.get('totalCash', 0)
            if fcf is not None and cash is not None and fcf < 0:
                yearly_burn = abs(fcf)
                if yearly_burn > 0:
                    runway_years = cash / yearly_burn
                    if runway_years < 0.5:
                        return {"status": "FAIL", "reason": f"Zombie Reject: Burning cash with less than 6 months runway."}
        
        # --- 3. CLASSIC DEBT FILTER (INDUSTRIALS/DEFAULT) ---
        if config["check_debt"] and config["type"] == "standard":
            ebitda = info.get('ebitda')
            debt = info.get('totalDebt')
            cash = info.get('totalCash')
            if ebitda and debt and ebitda > 0:
                net_debt_ebitda = (debt - (cash or 0)) / ebitda
                if net_debt_ebitda > config["debt_max_ebitda"]:
                    return {"status": "FAIL", "reason": f"Debt Reject: Net Debt/EBITDA is {round(net_debt_ebitda, 2)}x > {config['debt_max_ebitda']}x"}
        
        # --- 4. INTRINSIC VALUE & SAFETY MARGIN ---
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
            "margin_of_safety": margin_of_safety
        }
        
        return {"status": "PASS", "reason": f"Passed {sector} Gatekeeper.", "metrics": metrics}
        
    except Exception as e:
         return {"status": "FAIL", "reason": f"Data Extraction Error: {str(e)}", "metrics": {}}

# --- FINNHUB API TOOLS ---
def get_finnhub_client():
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        raise ValueError("FINNHUB_API_KEY environment variable not set.")
    return finnhub.Client(api_key=api_key)

@tool
def get_insider_sentiment(ticker: str) -> str:
    """
    Fetch recent insider sentiment and trading behavior for a US stock.
    Returns information on whether company executives are buying or selling shares.
    """
    import requests
    try:
        if "." in ticker: return f"Insider extraction not supported for non-US ticker {ticker}."
        api_key = os.getenv("FINNHUB_API_KEY")
        if not api_key: return "API Key missing."
        
        url = f"https://finnhub.io/api/v1/stock/insider-sentiment?symbol={ticker}&from=2024-01-01&to=2026-12-31&token={api_key}"
        response = requests.get(url)
        data = response.json()
        
        if 'data' not in data or len(data['data']) == 0:
            return f"No recent insider sentiment data found for {ticker}."
            
        recent = data['data'][0] # Most recent month available
        msp = recent.get('mspr', 0)
        
        sentiment = "Neutral"
        if msp > 0: sentiment = "Positive (Insiders are Buying)"
        elif msp < 0: sentiment = "Negative (Insiders are Selling)"
            
        return f"Insider Sentiment for {ticker}: {sentiment}. MSPR Score: {msp}."
    except Exception as e:
        return f"Error fetching insider data: {str(e)}"

@tool
def get_company_news(ticker: str) -> str:
    """
    Fetch the top 3 most recent financial news headlines for a US stock.
    Useful for finding catalysts or understanding recent price movements.
    """
    from datetime import datetime, timedelta
    try:
        if "." in ticker: return f"News extraction not supported via Finnhub for non-US ticker {ticker}."
        client = get_finnhub_client()
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        
        news = client.company_news(ticker, _from=start_date, to=end_date)
        if not news:
            return f"No recent news found for {ticker}."
            
        headlines = []
        for i, article in enumerate(news[:3]):
            headlines.append(f"{i+1}. {article.get('headline', 'No Headline')} - {article.get('summary', '')}")
            
        return f"Recent News for {ticker}:\n" + "\n".join(headlines)
    except Exception as e:
        return f"Error fetching news: {str(e)}"

@tool
def get_basic_financials(ticker: str) -> str:
    """
    Fetch deep fundamental metrics for a US stock, including 52-week highs/lows, 
    beta, gross margin, and return on equity (ROE).
    """
    try:
        if "." in ticker: return f"Deep fundamentals not supported via Finnhub for non-US ticker {ticker}."
        client = get_finnhub_client()
        data = client.company_basic_financials(ticker, 'all')
        if not data or 'metric' not in data:
            return f"No fundamental data found for {ticker}."
            
        metrics = data['metric']
        report = f"Fundamentals for {ticker}:\n"
        report += f"- 52 Week High: ${metrics.get('52WeekHigh', 'N/A')}\n"
        report += f"- 52 Week Low: ${metrics.get('52WeekLow', 'N/A')}\n"
        report += f"- Beta (Volatility vs Market): {metrics.get('beta', 'N/A')}\n"
        report += f"- Gross Margin TTM: {metrics.get('grossMarginTTM', 'N/A')}%\n"
        report += f"- Return On Equity (ROE) TTM: {metrics.get('roeTTM', 'N/A')}%\n"
        
        return report
    except Exception as e:
        return f"Error fetching financials: {str(e)}"
