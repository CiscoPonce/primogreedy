import chainlit as cl
from src.agent import app  # Import the robot you built

@cl.on_chat_start
async def start():
    """
    Sends a welcome message when the user opens the website.
    """
    await cl.Message(content="👋 **Hello! I am PrimoGreedy.**\n\nI am your skeptical financial analyst.\nGive me a ticker (e.g., AAPL, AMC, PLTR) and I will run it through the Firewall.").send()

@cl.on_message
async def main(message: cl.Message):
    """
    Runs every time the user types a message.
    """
    ticker = message.content.upper().strip()
    
    # 1. Notify user we are starting
    msg = cl.Message(content=f"🔍 Analyzing **{ticker}**...")
    await msg.send()
    
    # 2. Run the Agent Logic
    # We use 'invoke' to run the graph we built in agent.py
    try:
        result = await app.ainvoke({"ticker": ticker})
        
        # 3. Check the Result
        final_report = result.get('final_report')
        status = result.get('status')
        financial_data = result.get('financial_data', {})
        
        # 4. Display the Output
        if status == "FAIL":
            # If the Firewall rejected it
            reason = financial_data.get('reason', 'Unknown Reason')
            
            response = f"""
            ❌ **REJECTED BY FIREWALL**
            
            **Ticker:** {ticker}
            **Reason:** {reason}
            
            *I did not waste time searching for news because this stock is too risky.*
            """
        else:
            # If it passed and the LLM wrote a report
            response = f"""
            ✅ **PASSED FIREWALL**
            
            {final_report}
            """
            
        # Send the final answer to the chat UI
        await cl.Message(content=response).send()
        
    except Exception as e:
        await cl.Message(content=f"⚠️ Error: {str(e)}").send()