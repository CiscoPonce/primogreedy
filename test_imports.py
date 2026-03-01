import sys
import time

print("1. Starting import test...", flush=True)

try:
    import yfinance as yf
    print("2. yfinance loaded ok.", flush=True)
except Exception as e:
    print(f"Error loading yfinance: {e}")

try:
    from src.llm import get_llm
    print("3. src.llm loaded ok.", flush=True)
except Exception as e:
    print(f"Error loading src.llm: {e}")

try:
    from src.agent import brave_market_search
    print("4. src.agent loaded ok.", flush=True)
except Exception as e:
    print(f"Error loading src.agent: {e}")

print("5. Test complete.", flush=True)
