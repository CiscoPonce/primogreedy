import os
import requests
import re
import yfinance as yf
from src.llm import get_llm

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
    search_term = HANDLE_MAP.get(handle, handle)
    
    # 2. Refined Search Query
    # Some search results strip the '$', so we search for stock-specific keywords
    query = f'"{search_term}" (stock OR shares OR bought OR sold OR calls OR options)'
    
    print(f"🕵️‍♂️ AI Scouting Web for: {search_term}...")
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": api_key}
    params = {
        "q": query,
        "count": 15,
        "freshness": "pw"
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        results = data.get("web", {}).get("results", [])
        
        full_text = " ".join([r['title'] + " " + r['description'] for r in results])
        print(f"📝 Scanned {len(results)} search results.")
        
    except Exception as e:
        print(f"Brave Search Error: {e}")
        return []

    # 3. Fast Extraction 
    print(f"🧠 Scanning for Tickers in {len(results)} articles...")
    
    # Extract 2-5 letter fully capitalized words
    raw_candidates = re.findall(r'\b[A-Z]{2,5}\b', full_text)
    
    # Filter out common noise
    ignore_list = {
        "THE", "FOR", "AND", "WHO", "ARE", "YOU", "WHY", "NOT", "NEW", "CEO", "CFO", 
        "NOW", "BUY", "SELL", "LOW", "HIGH", "ATH", "ETF", "USA", "USD", "YTD", 
        "CNBC", "NEWS", "REAL", "TIME", "TODAY", "LIVE", "DATA", "KRUZ", "WSJ", "NYSE",
        "SEC", "FED", "CPI", "FOMC"
    }
    
    # Remove duplicates and noise
    unique_tickers = list(dict.fromkeys([c for c in raw_candidates if c not in ignore_list]))
    
    # 4. Validate with yFinance
    valid_tickers = []
    print(f"🔎 Validating {len(unique_tickers)} candidates...")
    
    for ticker in unique_tickers:
        if len(valid_tickers) >= 5: 
            break # Limit to top 5
        
        try:
            stock = yf.Ticker(ticker)
            price = stock.fast_info.last_price 
            if price and price > 0:
                valid_tickers.append(ticker)
        except:
            continue
            
    return valid_tickers