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
    # Don't uppercase everything immediately so chat messages look normal
    user_input = message.content.strip() 
    tickers = []
    is_chat = False
    
    if user_input.upper() == "AUTO":
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
        # 🚨 THE NLP FIX: If it's a sentence, treat it as a chat. If it's short, it's a ticker.
        words = user_input.split()
        if len(words) > 2:
            tickers = [user_input]
            is_chat = True
        else:
            raw_list = user_input.upper().replace(",", " ").split()
            tickers = [t for t in raw_list if len(t) <= 5 and t.isalpha()]
            if not tickers: tickers = [user_input.upper()]

    # --- EXECUTION LOOP ---
    for ticker in tickers:
        # Skip weird artifacts unless it's a purposeful chat message
        if len(ticker) > 5 and len(tickers) > 1 and not is_chat: continue

        # Dynamic UI Feedback
        if is_chat:
            await cl.Message(content=f"💬 **Consulting Senior Broker...**").send()
        else:
            await cl.Message(content=f"--- 🔎 **Processing:** {ticker} ---").send()
        
        try:
            config = {"configurable": {"thread_id": "ui_session"}}
            
            # Pass the query to LangGraph
            result = await app.ainvoke(
                {"ticker": ticker, "retry_count": 0, "manual_search": not is_chat},
                config=config
            )
            
            status = result.get('status')
            report = result.get('final_report', "No report generated.")
            chart_bytes = result.get('chart_data')
            
            elements = []
            if chart_bytes and not is_chat: # Don't try to render charts for text conversations
                elements.append(cl.Image(content=chart_bytes, name=f"{ticker}_chart", display="inline"))

            if status == "FAIL":
                response = f"{report}\n\n*Chart provided for visual reference despite rejection.*"
            elif status == "CHAT":
                response = f"🤖 **Agent Response:**\n\n{report}"
            else:
                response = f"✅ **PASSED FIREWALL**\n\n{report}"

            await cl.Message(content=response, elements=elements).send()
            
        except Exception as e:
            await cl.Message(content=f"⚠️ Error processing request: {str(e)}").send()
            
        finally:
            plt.close('all') 
            result = None
            elements = []
            chart_bytes = None
            gc.collect()