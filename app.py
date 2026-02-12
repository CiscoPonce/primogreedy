import chainlit as cl
from src.agent import app
from src.social_scout import fetch_tickers_from_social
from src.scanner import get_trending_stocks # <--- Import new tool

@cl.on_chat_start
async def start():
    welcome_msg = """
    👋 **PrimoGreedy v6.0: Auto-Pilot**
    
    **Try these commands:**
    1. `AUTO` -> 🧠 **Smart Scan** (Finds trending stocks & filters them)
    2. `@Handle` -> 🕵️‍♂️ **Social Scout** (e.g., `@DeItaone`)
    3. `NVDA` -> 🔎 **Single Scout**
    """
    await cl.Message(content=welcome_msg).send()

@cl.on_message
async def main(message: cl.Message):
    user_input = message.content.strip().upper()
    tickers = []
    
    # --- MODE 1: AUTO-PILOT (The "Dummy Mode") ---
    if user_input == "AUTO":
        await cl.Message(content="📡 **Scanning Global Markets for Trends...**").send()
        tickers = await cl.make_async(get_trending_stocks)()
        
        if not tickers:
            await cl.Message(content="❌ Could not find trending data. Try again.").send()
            return
            
        await cl.Message(content=f"🔥 **Hot List Detected:** {', '.join(tickers)}\n*Running Logic Firewall on all of them...*").send()

    # --- MODE 2: SOCIAL SCOUT ---
    elif user_input.startswith("@"):
        handle = user_input.replace("@", "")
        await cl.Message(content=f"🕵️‍♂️ Scouting **@{handle}**...").send()
        tickers = await cl.make_async(fetch_tickers_from_social)(handle)
        
        if not tickers:
            await cl.Message(content=f"❌ No tickers found for @{handle}.").send()
            return
        await cl.Message(content=f"🎯 Targets: **{', '.join(tickers)}**").send()
    
    # --- MODE 3: DIRECT INPUT ---
    else:
        raw_list = user_input.replace(",", " ").split()
        tickers = [t for t in raw_list if len(t) <= 5 and t.isalpha()]
        if not tickers: tickers = [user_input] # Fallback for chat

    # --- EXECUTION LOOP ---
    for ticker in tickers:
        # Skip chatty words
        if len(ticker) > 5 and len(tickers) > 1: continue

        # Visual Separation
        await cl.Message(content=f"--- 🔎 **Checking {ticker}** ---").send()
        
        try:
            result = await app.ainvoke({"ticker": ticker})
            
            status = result.get('status')
            report = result.get('final_report')
            chart_bytes = result.get('chart_data')
            email_msg = result.get('email_status')

            elements = []
            if chart_bytes:
                elements.append(cl.Image(content=chart_bytes, name=f"{ticker}_chart", display="inline"))

            if status == "FAIL":
                # Concise Rejection
                response = f"❌ **REJECTED**: {result.get('financial_data', {}).get('reason')}"
            elif status == "PASS":
                # Full Report
                response = f"""
                ✅ **PASSED FIREWALL**
                {report}
                \n*{email_msg if email_msg else '📧 Email failed'}*
                """
            else:
                response = report

            await cl.Message(content=response, elements=elements).send()
            
        except Exception as e:
            await cl.Message(content=f"⚠️ Error on {ticker}: {str(e)}").send()