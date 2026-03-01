import os
import yfinance as yf
from src.whale_hunter import analyst_node
import warnings
warnings.filterwarnings('ignore')

# Mock LLM for timeout-free testing
class MockLLM:
    def bind_tools(self, tools): return self
    def invoke(self, prompt):
        class MockResponse:
            content = "### 🧮 THE QUANTITATIVE BASE\n* Apple trades at a steep premium...\n\n### 🟢 THE LYNCH PITCH\n* Insiders are selling.\n* Hardware supercycle incoming.\n\n### 🔴 THE MUNGER INVERT\n* Over-reliance on TSM.\n\n### ⚖️ FINAL VERDICT\nWATCH"
        return MockResponse()

import src.whale_hunter
src.whale_hunter.llm = MockLLM()

# Set up test state
ticker = "AAPL"
stock = yf.Ticker(ticker)
info = stock.info

state = {
    "ticker": ticker,
    "company_name": info.get('shortName', ticker),
    "financial_data": info,
    "region": "USA"
}

print(f"\n--- Testing Analyst Node for {ticker} ---")
os.environ["FINNHUB_API_KEY"] = "d6h1lkhr01qnjncn1030d6h1lkhr01qnjncn103g"
result = src.whale_hunter.analyst_node(state)

print("\n\n--- THE AGENT'S RESULT ---")
print(result['final_verdict'])
