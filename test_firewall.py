from src.finance_tools import check_financial_health

print("\n--- 🕵️‍♂️ PRIMOGREEDY V2 TEST (Solvency + Valuation) ---\n")

# Test List:
# NVDA = Might fail (Expensive)
# AMC = Should fail (Bad Debt)
# KO (Coca Cola) = Should pass (Safe)
# PLTR (Palantir) = Might fail (Expensive)

tickers = ["NVDA", "AMC", "KO", "PLTR"]

for t in tickers:
    result = check_financial_health(t)
    icon = "✅" if result['status'] == "PASS" else "❌"
    print(f"{icon} {t}: {result['reason']}")