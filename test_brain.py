from src.search_tools import get_market_sentiment

print("\n--- 🧠 PRIMOGREEDY INTELLIGENCE TEST ---\n")

# We test with AAPL because there is ALWAYS news about Apple
ticker = "AAPL"
print(f"🔎 Searching for latest risks/news on {ticker}...")

news = get_market_sentiment(ticker)

print(f"\n📰 RAW NEWS DATA RECEIVED:\n")
print(news)

print("\n------------------------------------------------\n")