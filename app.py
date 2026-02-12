import chainlit as cl
from src.agent import app

@cl.on_chat_start
async def start():
    # Send a Welcome Message that lists your new powers
    await cl.Message(content="👋 **PrimoGreedy v3.0**\n\n- **Brave Search** Active 🦁\n- **Resend Email** Active 📧\n- **Charts** Active 📈\n\n*Type a ticker (e.g., NVDA) to scout, or just say Hello.*").send()

@cl.on_message
async def main(message: cl.Message):
    user_input = message.content.strip()
    
    # Simple check: If it looks like a ticker, say "Scouting", otherwise "Thinking"
    if len(user_input) <= 5 and " " not in user_input:
        msg = cl.Message(content=f"🔍 Scouting **{user_input.upper()}**...")
    else:
        msg = cl.Message(content=f"🤔 Thinking...")
    await msg.send()
    
    try:
        # Run the full Agent (Brain + Eyes + Hands)
        result = await app.ainvoke({"ticker": user_input})
        
        # Extract all the new data we added
        status = result.get('status')
        report = result.get('final_report')
        chart_bytes = result.get('chart_data')
        email_msg = result.get('email_status')

        # Prepare the Image (if we have one)
        elements = []
        if chart_bytes:
            elements.append(cl.Image(content=chart_bytes, name="chart", display="inline"))

        # Format the Text Response
        if status == "FAIL":
            # Rejection (Firewall)
            response = f"""
            ❌ **REJECTED**: {result.get('financial_data', {}).get('reason')}
            
            *No email sent. No chart drawn.*
            """
        elif status == "PASS":
            # Success (Analysis + Chart + Email)
            response = f"""
            ✅ **PASSED FIREWALL**
            
            {report}
            
            ---
            **System Status:**
            {email_msg if email_msg else "📧 Email not sent (Check keys)"}
            """
        else:
            # Just Chatting
            response = report

        # Send everything to the UI
        await cl.Message(content=response, elements=elements).send()
        
    except Exception as e:
        await cl.Message(content=f"⚠️ Error: {str(e)}").send()