import chainlit as cl
import gc
import matplotlib.pyplot as plt
from src.agent import app
from src.social_scout import fetch_tickers_from_social
from src.scanner import get_trending_stocks

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
    
    if user_input == "AUTO":
        await cl.Message(content="📡 **Scanning Global Markets...**").send()
        tickers = await cl.make_async(get_trending_stocks)()
        if not tickers:
            await cl.Message(content="❌ No trending data.").send()
            return
        await cl.Message(content=f"🔥 **Hot List:** {', '.join(tickers)}").send()

    elif user_input.startswith("@"):
        handle = user_input.replace("@", "")
        await cl.Message(content=f"🕵️‍♂️ Scouting **@{handle}**...").send()
        tickers = await cl.make_async(fetch_tickers_from_social)(handle)
        if not tickers:
            await cl.Message(content="❌ No tickers found.").send()
            return
            
    else:
        raw_list = user_input.replace(",", " ").split()
        tickers = [t for t in raw_list if len(t) <= 5 and t.isalpha()]
        if not tickers: tickers = [user_input]

    # --- EXECUTION LOOP ---
    for ticker in tickers:
        if len(ticker) > 5 and len(tickers) > 1: continue

        await cl.Message(content=f"--- 🔎 **Checking {ticker}** ---").send()
        
        try:
            # We pass the ticker to bypass the random 'scout'
            result = await app.ainvoke({"ticker": ticker, "retry_count": 0})
            
            status = result.get('status')
            report = result.get('final_report')
            chart_bytes = result.get('chart_data')
            
            elements = []
            if chart_bytes:
                elements.append(cl.Image(content=chart_bytes, name=f"{ticker}_chart", display="inline"))

            if status == "FAIL":
                response = f"❌ **REJECTED**: {result.get('financial_data', {}).get('reason')}"
            else:
                response = f"✅ **PASSED FIREWALL**\n{report}"

            await cl.Message(content=response, elements=elements).send()
            
        except Exception as e:
            await cl.Message(content=f"⚠️ Error on {ticker}: {str(e)}").send()
            
        finally:
            # 🚨 MEMORY FIX: Close charts and purge RAM after every loop
            plt.close('all') 
            result = None
            elements = []
            chart_bytes = None
            gc.collect()