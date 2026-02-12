import os
import requests
import re
import yfinance as yf

def get_trending_stocks():
    """
    1. Asks Brave for 'Top Trending Stocks Today'.
    2. Extracts tickers from the search results.
    3. Returns a unique list of valid tickers.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key: return ["NVDA", "TSLA", "AAPL", "AMD", "PLTR"] # Fallback if no key

    # We search for a mix of "Trending", "Gainers", and "Most Active"
    query = "stock market top trending gainers active tickers today"
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": api_key}
    params = {"q": query, "count": 10} # Get top 10 results

    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        results = data.get("web", {}).get("results", [])
        
        # Combine all text
        full_text = " ".join([r['title'] + " " + r['description'] for r in results])
        
    except Exception as e:
        print(f"Scanner Error: {e}")
        return ["NVDA", "TSLA", "AAPL"] # Fallback

    # Regex to find Tickers (2-5 Uppercase Letters)
    candidates = re.findall(r'\b[A-Z]{2,5}\b', full_text)
    
    # Filter out common English words that look like tickers
    ignore = {
        "THE", "FOR", "AND", "ARE", "YOU", "WHY", "NOT", "NEW", "CEO", "NOW", 
        "BUY", "SELL", "LOW", "HIGH", "ATH", "ETF", "USA", "USD", "YTD", 
        "TOP", "HOT", "BEST", "LIVE", "DATA", "GDP", "CPI", "FED", "FOMC"
    }
    
    unique_tickers = []
    seen = set()

    print(f"🔎 Scanning Market Text...")
    
    for ticker in candidates:
        if ticker in ignore or ticker in seen: continue
        
        # We only want 5 to 7 tickers max for the 'Dummy Mode'
        if len(unique_tickers) >= 7: break

        try:
            # Quick Validation: Does it have a price?
            stock = yf.Ticker(ticker)
            price = stock.fast_info.last_price
            if price and price > 0:
                unique_tickers.append(ticker)
                seen.add(ticker)
        except:
            continue
            
    return unique_tickers if unique_tickers else ["NVDA", "TSLA", "AMD"]