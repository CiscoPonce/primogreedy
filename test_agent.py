from src.agent import app

print("--- 🚀 STARTING PRIMO AGENT ---")

# Test 1: The Bad Stock (Should stop at Filter)
print("\n\n📉 TEST CASE 1: AMC (High Risk)")
result_amc = app.invoke({"ticker": "AMC"})
print(f"RESULT: {result_amc.get('final_report', 'REJECTED BY FIREWALL')}")

# Test 2: The Good Stock (Should go to LLM)
print("\n\n📈 TEST CASE 2: AAPL (Low Risk)")
result_aapl = app.invoke({"ticker": "AAPL"})
print(f"REPORT: \n{result_aapl['final_report']}")