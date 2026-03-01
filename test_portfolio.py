import os
import yfinance as yf
from src.portfolio_tracker import record_paper_trade, evaluate_portfolio

print("--- 🧪 INITIATING PORTFOLIO TRACKER TEST ---")

# Ensure clean slate for test
test_file = "paper_portfolio.json"
if os.path.exists(test_file):
    os.remove(test_file)
    print("🧹 Cleared existing portfolio ledger.")

# 1. Simulate the Analyst Node generating a STRONG BUY for AAPL yesterday with a lower entry price
print("\n🤖 [Agent Simulation]: AAPL looks amazing. Entry: $150. Verdict: STRONG BUY.")
record_paper_trade(ticker="AAPL", entry_price=150.00, verdict="STRONG BUY. Target $250.", source="Test Script")

# 2. Simulate the Analyst Node generating a WATCH for a random stock with a higher entry price (to show a loss)
print("🤖 [Agent Simulation]: PLTR is hot but expensive. Entry: $300. Verdict: WATCH.")
record_paper_trade(ticker="PLTR", entry_price=300.00, verdict="WATCH for a pullback.", source="Test Script")

# 3. Simulate the user typing "PORTFOLIO" in the Chainlit UI
print("\n--- 📊 USER TYPED 'PORTFOLIO' ---")
print("Fetching live prices...\n")
report = evaluate_portfolio()

print(report)

# Clean up
if os.path.exists(test_file):
    os.remove(test_file)
    print("\n🧹 Test Complete. Cleaned up test ledger.")
