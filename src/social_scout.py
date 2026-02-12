import os
import requests
import re
import yfinance as yf

# Map generic handles to "Searchable Real Names" for better results
HANDLE_MAP = {
    "DeItaone": "Walter Bloomberg",
    "unusual_whales": "Unusual Whales",
    "JimCramer": "Jim Cramer",
    "CathieDWood": "Cathie Wood"
}

def fetch_tickers_from_social(handle: str):
    """
    1. Converts handle to real name (if known).
    2. Searches the WHOLE WEB for their recent stock mentions (not just Twitter).
    3. Extracts and validates tickers.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        print("⚠️ Brave API Key missing.")
        return []

    # 1. Resolve Name
    search_term = HANDLE_MAP.get(handle, handle) # Default to handle if not in map
    
    # 2. Broad Search Query (The Fix)
    # We remove 'site:twitter.com' and search for the *person* + keywords
    query = f'"{search_term}" stock market "buy" OR "sell" OR "picks"'
    
    print(f"🕵️‍♂️ Scouting Web for: {search_term}...")
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": api_key}
    params = {
        "q": query,
        "count": 20,       # Scan more results (20 instead of 10)
        "freshness": "pw"  # Past Week only
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        results = data.get("web", {}).get("results", [])
        
        # Combine titles and descriptions
        full_text = " ".join([r['title'] + " " + r['description'] for r in results])
        print(f"📝 Scanned {len(results)} search results.")
        
    except Exception as e:
        print(f"Brave Search Error: {e}")
        return []

    # 3. Extract Candidates (Refined Regex)
    # Matches $ABCD or just ABCD (2-5 capital letters)
    # We deliberately cast a wide net.
    candidates = re.findall(r'\b[A-Z]{2,5}\b', full_text)
    
    # 4. Filter Noise
    ignore_list = {
        "THE", "FOR", "AND", "WHO", "ARE", "YOU", "WHY", "NOT", "NEW", "CEO", "CFO", 
        "NOW", "BUY", "SELL", "LOW", "HIGH", "ATH", "ETF", "USA", "USD", "YTD", 
        "CNBC", "NEWS", "REAL", "TIME", "TODAY", "LIVE", "DATA"
    }
    unique_candidates = set([c for c in candidates if c not in ignore_list])
    
    valid_tickers = []
    
    # 5. Validate with yFinance
    print(f"🔎 Validating {len(unique_candidates)} candidates...")
    
    for ticker in unique_candidates:
        if len(valid_tickers) >= 5: break # Limit to top 5
        
        try:
            # We check if it has a price. If it errors, it's not a stock.
            stock = yf.Ticker(ticker)
            # Fast check: 'info' is slow, 'fast_info' or history is faster
            price = stock.fast_info.last_price 
            if price and price > 0:
                valid_tickers.append(ticker)
        except:
            continue
            
    return valid_tickers