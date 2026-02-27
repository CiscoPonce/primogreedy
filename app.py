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
    
    **Commands:**
    1. `AUTO` -> 🧠 Smart Scan
    2. `@Handle` -> 🕵️‍♂️ Social Scout
    3. `NVDA` -> 🔎 Single Scout
    4. Ask a question -> 💬 Chat with Agent
    """
    await cl.Message(content=welcome_msg).send()

@cl.on_message
async def main(message: cl.Message):
    user_input = message.content.strip()
    
    # 1. HANDLE AUTO
    if user_input.upper() == "AUTO":
        await cl.Message(content="📡 **Scanning Global Markets...**").send()
        tickers = await cl.make_async(get_trending_stocks)()
        if not tickers:
            await cl.Message(content="❌ No trending data.").send()
            return
        await cl.Message(content=f"🔥 **Hot List:** {', '.join(tickers)}").send()

    # 2. HANDLE SOCIAL SCOUT
    elif user_input.startswith("@"):
        handle = user_input.replace("@", "")
        await cl.Message(content=f"🕵️‍♂️ Scouting **@{handle}**...").send()
        tickers = await cl.make_async(fetch_tickers_from_social)(handle)
        if not tickers:
            await cl.Message(content="❌ No tickers found.").send()
            return

    # 🚨 3. BULLETPROOF CHAT ROUTING (If it has a space, it is a chat)
    elif " " in user_input:
        await cl.Message(content="💬 **Consulting Senior Broker...**").send()
        try:
            config = {"configurable": {"thread_id": "ui_session"}}
            # Route to LangGraph chat_node
            result = await app.ainvoke({"ticker": user_input, "retry_count": 0, "manual_search": False}, config=config)
            report = result.get('final_report', "No response generated.")
            await cl.Message(content=f"🤖 **Agent:**\n\n{report}").send()
        except Exception as e:
            await cl.Message(content=f"⚠️ Chat Error: {str(e)}").send()
        return

    # 4. HANDLE SINGLE OR COMMA-SEPARATED TICKERS
    else:
        # Don't replace spaces natively, only commas if multiple tickers are passed
        raw_list = user_input.upper().replace(",", " ").split()
        tickers = [t for t in raw_list if len(t) <= 5 and t.isalpha()]
        
        # If no valid tickers were found but it wasn't a chat message (no spaces), try the raw input
        if not tickers and " " not in user_input: 
            tickers = [user_input.upper()]

    for ticker in tickers:
        await cl.Message(content=f"--- 🔎 **Processing:** {ticker} ---").send()
        try:
            config = {"configurable": {"thread_id": "ui_session"}}
            result = await app.ainvoke({"ticker": ticker, "retry_count": 0, "manual_search": True}, config=config)
            
            status = result.get('status')
            report = result.get('final_report', "No report generated.")
            chart_bytes = result.get('chart_data')
            
            elements = []
            if chart_bytes:
                elements.append(cl.Image(content=chart_bytes, name=f"{ticker}_chart", display="inline"))

            if status == "FAIL":
                response = f"{report}\n\n*Chart provided for visual reference.*"
            else:
                response = f"✅ **PASSED FIREWALL**\n\n{report}"

            await cl.Message(content=response, elements=elements).send()
            
        except Exception as e:
            await cl.Message(content=f"⚠️ Error on {ticker}: {str(e)}").send()
        finally:
            plt.close('all')
            gc.collect()