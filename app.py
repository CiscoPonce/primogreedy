import chainlit as cl
from src.agent import app
from src.social_scout import fetch_tickers_from_social

@cl.on_chat_start
async def start():
    await cl.Message(content="👋 **PrimoGreedy v5.0: Social Scout**\n\n- **Type a Ticker:** `NVDA`\n- **Type a List:** `AAPL, TSLA, MSFT`\n- **Scout an Account:** `@DeItaone` or `@unusual_whales`").send()

@cl.on_message
async def main(message: cl.Message):
    user_input = message.content.strip()
    
    # --- MODE 1: SOCIAL SCOUT (@handle) ---
    if user_input.startswith("@"):
        handle = user_input.replace("@", "")
        msg = cl.Message(content=f"🕵️‍♂️ Scouting X (Twitter) for **@{handle}**...")
        await msg.send()
        
        # Run the Scout Tool
        tickers = await cl.make_async(fetch_tickers_from_social)(handle)
        
        if not tickers:
            await cl.Message(content=f"❌ No valid stock tickers found in the last week for @{handle}.").send()
            return
            
        await cl.Message(content=f"🎯 Targets Acquired: **{', '.join(tickers)}**\n*Starting Analysis Loop...*").send()
    
    # --- MODE 2: DIRECT INPUT ---
    else:
        # Split by comma or space
        raw_list = user_input.replace(",", " ").split()
        tickers = [t.upper() for t in raw_list if len(t) <= 5 and t.isalpha()]
        
        if not tickers:
            # It's just chat
            tickers = [user_input]

    # --- EXECUTION LOOP ---
    for ticker in tickers:
        # Skip long conversational words if we are in batch mode
        if len(ticker) > 5 and len(tickers) > 1: continue

        msg = cl.Message(content=f"🔍 Analyzing **{ticker}**...")
        await msg.send()
        
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
                response = f"❌ **{ticker}: REJECTED**\n\nReason: {result.get('financial_data', {}).get('reason')}"
            elif status == "PASS":
                response = f"""
                ✅ **{ticker}: PASSED**
                
                {report}
                
                ---
                {email_msg if email_msg else "📧 Email check failed"}
                """
            else:
                response = report

            await cl.Message(content=response, elements=elements).send()
            
        except Exception as e:
            await cl.Message(content=f"⚠️ Error on {ticker}: {str(e)}").send()