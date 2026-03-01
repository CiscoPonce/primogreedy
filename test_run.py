import asyncio
from src.agent import app

async def main():
    print("🚀 Triggering the Advanced Agent Pipeline (USA)...")
    
    # We pass the state to the compiled StateGraph
    result = await app.ainvoke({"region": "USA", "retry_count": 0, "ticker": "NONE"})
    
    print("\n" + "="*50)
    print("📈 PIPELINE RESULTS")
    print("="*50)
    
    ticker = result.get('ticker', 'NONE')
    status = result.get('status', 'FAIL')
    verdict = result.get('final_verdict', 'No Verdict')
    
    print(f"🎯 Target Acquired: {ticker}")
    print(f"⚖️ Gatekeeper Status: {status}")
    
    if status == 'PASS':
        info = result.get('financial_data', {})
        print(f"💰 Price: ${info.get('currentPrice')}")
        print(f"📊 Market Cap: ${info.get('marketCap', 0):,.0f}")
        print(f"🌊 Float Shares: {info.get('floatShares', 0):,.0f}")
        print(f"👔 Insider Ownership: {info.get('heldPercentInsiders', 0) * 100:.1f}%")
        print("\n🧠 SENIOR BROKER ANALYSIS:")
        print(verdict)
    else:
        print(f"🛑 Reason for failure: {result.get('financial_data', {}).get('reason', 'N/A')}")
        
    print("="*50 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
