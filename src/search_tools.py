import os
import requests
from dotenv import load_dotenv

load_dotenv()

def get_market_sentiment(ticker: str):
    """
    The Eyes.
    Uses Brave Search API directly to find news.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        return "Error: BRAVE_API_KEY not found in .env"

    try:
        print(f"🧠 AI Searching news for: {ticker}...")
        
        # 1. Define the Endpoint
        url = "https://api.search.brave.com/res/v1/web/search"
        
        # 2. Define the Headers (Authentication)
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key
        }
        
        # 3. Define the Query
        params = {
            "q": f"{ticker} stock news risks analysis",
            "count": 3
        }
        
        # 4. Make the Request
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            # Extract just the descriptions/snippets
            results = [item.get('description', '') for item in data.get('web', {}).get('results', [])]
            return "\n".join(results)
        elif response.status_code == 429:
            return "Error: Rate Limit Hit (Wait 1 second)"
        else:
            return f"Error: API Status {response.status_code}"

    except Exception as e:
        return f"Search Error: {str(e)}"