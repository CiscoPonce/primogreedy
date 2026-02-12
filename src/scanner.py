import os
import requests
import re
import yfinance as yf
import random

# A menu of different search strategies to keep results fresh
SEARCH_QUERIES = [
    "stock market top trending gainers today",
    "most active stocks by volume today",
    "undervalued growth stocks 2026",
    "stocks with highest implied volatility today",
    "best performing tech stocks this week",
    "small cap stocks breaking out today",
    "unusual options activity tickers today"
]

def get_trending_stocks():
    """
    1. Picks a RANDOM search strategy.
    2. Asks Brave for data.
    3. Shuffles and returns a unique list of tickers.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key: return ["NVDA", "TSLA", "AAPL", "AMD", "PLTR"]

    # 1. Randomly select a query
    selected_query = random.choice(SEARCH_QUERIES)
    print(f"🎲 Strategy Selected: '{selected_query}'")
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": api_key}
    # We ask for 20 results to get a wide pool of candidates
    params = {"q": selected_query, "count": 20, "freshness": "pw"} 

    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        results = data.get("web", {}).get("results", [])
        full_text = " ".join([r['title'] + " " + r['description'] for r in results])
    except Exception as e:
        print(f"Scanner Error: {e}")
        return ["NVDA", "TSLA", "AMD"]

    # 2. Extract Tickers
    candidates = re.findall(r'\b[A-Z]{2,5}\b', full_text)
    
    ignore = {
        "THE", "FOR", "AND", "ARE", "YOU", "WHY", "NOT", "NEW", "CEO", "NOW", 
        "BUY", "SELL", "LOW", "HIGH", "ATH", "ETF", "USA", "USD", "YTD", 
        "TOP", "HOT", "BEST", "LIVE", "DATA", "GDP", "CPI", "FED", "FOMC",
        "PCE", "PPI", "CNBC", "NYSE", "NASDAQ"
    }
    
    unique_tickers = []
    seen = set()
    
    # 3. Randomly Shuffle the candidates BEFORE validating
    # This ensures we don't always pick the first ones mentioned in the news
    random.shuffle(candidates)

    print(f"🔎 Scanning Market Text...")
    
    for ticker in candidates:
        if ticker in ignore or ticker in seen: continue
        
        if len(unique_tickers) >= 5: break # Limit to 5 for speed

        try:
            stock = yf.Ticker(ticker)
            # Fast check to see if it's real
            price = stock.fast_info.last_price
            if price and price > 0:
                unique_tickers.append(ticker)
                seen.add(ticker)
        except:
            continue
            
    return unique_tickers if unique_tickers else ["NVDA", "TSLA", "AMD"]