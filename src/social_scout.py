import os
import requests
import re
import yfinance as yf

def fetch_tickers_from_social(handle: str):
    """
    1. Searches Brave for recent tweets from a specific handle.
    2. Extracts potential tickers (e.g., $NVDA, AAPL).
    3. Validates them with yFinance to remove noise (like "THE", "FOR").
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        return []

    # 1. Search Brave for "site:twitter.com/handle"
    # We look for financial keywords to ensure we get relevant tweets
    query = f'site:twitter.com/{handle} "stock" OR "shares" OR "buy" OR "sell" OR "long" OR "short"'
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": api_key}
    params = {
        "q": query,
        "count": 10,       # Scan top 10 results
        "freshness": "pw"  # "Past Week" (to get recent ideas)
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        results = data.get("web", {}).get("results", [])
        
        # Combine all titles and descriptions into one big text blob
        full_text = " ".join([r['title'] + " " + r['description'] for r in results])
        
    except Exception as e:
        print(f"Brave Search Error: {e}")
        return []

    # 2. Extract Candidates using Regex
    # Matches $ABCD or just ABCD (2-5 capital letters)
    # We deliberately cast a wide net, then filter later.
    candidates = re.findall(r'\b[A-Z]{2,5}\b', full_text)
    
    # Common false positives to ignore immediately
    ignore_list = {"THE", "FOR", "AND", "WHO", "ARE", "YOU", "WHY", "NOT", "NEW", "CEO", "CFO", "NOW", "BUY", "SELL", "LOW", "HIGH", "ATH"}
    unique_candidates = set([c for c in candidates if c not in ignore_list])

    valid_tickers = []
    
    # 3. Validate with yFinance (The "Grounding" Step)
    print(f"🔎 Found candidates for {handle}: {unique_candidates}")
    
    for ticker in unique_candidates:
        try:
            # Quick check: Does it have a price?
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                valid_tickers.append(ticker)
        except:
            continue
            
    return valid_tickers[:5] # Return top 5 to avoid overloading the agent