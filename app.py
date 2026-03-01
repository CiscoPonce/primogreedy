import chainlit as cl
import gc
import matplotlib.pyplot as plt
from src.agent import app
from src.social_scout import fetch_tickers_from_social
from src.scanner import get_trending_stocks
from src.core.logger import get_logger

logger = get_logger(__name__)


@cl.on_chat_start
async def start():
    welcome_msg = """
    **PrimoGreedy v7.0: Screener + Brave Edition**
    
    **Commands:**
    1. `AUTO` -> Smart Scan (yFinance screener + Brave trending)
    2. `@Handle` -> Social Scout
    3. `PORTFOLIO` -> View Agent Track Record
    4. `BACKTEST` -> Run backtest on paper portfolio
    5. `NVDA` -> Single Ticker Scout
    6. Ask a question -> Chat with Agent
    """
    await cl.Message(content=welcome_msg).send()


@cl.on_message
async def main(message: cl.Message):
    user_input = message.content.strip()

    # 1. AUTO SCAN
    if user_input.upper() == "AUTO":
        await cl.Message(content="Scanning global markets with screener + Brave trending...").send()
        tickers = await cl.make_async(get_trending_stocks)()
        if not tickers:
            await cl.Message(content="No trending data found.").send()
            return
        await cl.Message(content=f"**Hot List:** {', '.join(tickers)}").send()

    # 2. PORTFOLIO
    elif user_input.upper() == "PORTFOLIO":
        from src.portfolio_tracker import evaluate_portfolio
        await cl.Message(content="Fetching live prices for the Agent's historical calls...").send()
        report = await cl.make_async(evaluate_portfolio)()
        await cl.Message(content=report).send()
        return

    # 3. BACKTEST (Proposal G)
    elif user_input.upper() == "BACKTEST":
        await cl.Message(content="Running backtest on paper portfolio...").send()
        try:
            from src.backtesting.portfolio_bridge import backtest_portfolio
            results = await cl.make_async(backtest_portfolio)()
            if not results:
                await cl.Message(content="No trades in portfolio to backtest.").send()
                return

            report = "## Portfolio Backtest Results\n\n"
            report += "| Symbol | PrimoAgent | Buy & Hold | Alpha |\n"
            report += "|--------|-----------|-----------|-------|\n"
            for symbol, r in results.items():
                primo = r["primo"]["Cumulative Return [%]"]
                bh = r["buyhold"]["Cumulative Return [%]"]
                diff = primo - bh
                emoji = "+" if diff > 0 else ""
                report += f"| **{symbol}** | {primo:.2f}% | {bh:.2f}% | {emoji}{diff:.2f}% |\n"

            total = len(results)
            wins = sum(1 for r in results.values()
                       if r["primo"]["Cumulative Return [%]"] > r["buyhold"]["Cumulative Return [%]"])
            report += f"\n**Win Rate:** {wins}/{total} ({wins / total * 100:.1f}%)\n"

            await cl.Message(content=report).send()
        except Exception as e:
            logger.error("Backtest error: %s", e, exc_info=True)
            await cl.Message(content=f"Backtest error: {e}").send()
        return

    # 4. SOCIAL SCOUT
    elif user_input.startswith("@"):
        handle = user_input.replace("@", "")
        await cl.Message(content=f"Scouting **@{handle}**...").send()
        tickers = await cl.make_async(fetch_tickers_from_social)(handle)
        if not tickers:
            await cl.Message(content="No tickers found.").send()
            return

    # 5. CHAT (has a space -> conversational query)
    elif " " in user_input:
        await cl.Message(content="Consulting Senior Broker...").send()
        try:
            config = {"configurable": {"thread_id": "ui_session"}}
            result = await app.ainvoke(
                {"ticker": user_input, "retry_count": 0, "manual_search": False},
                config=config,
            )
            report = result.get("final_report", "No response generated.")
            await cl.Message(content=f"**Agent:**\n\n{report}").send()
        except Exception as e:
            logger.error("Chat error: %s", e)
            await cl.Message(content=f"Chat error: {e}").send()
        return

    # 6. SINGLE OR COMMA-SEPARATED TICKERS
    else:
        raw_list = user_input.upper().replace(",", " ").split()
        tickers = [t for t in raw_list if len(t) <= 5 and t.isalpha()]
        if not tickers and " " not in user_input:
            tickers = [user_input.upper()]

    for ticker in tickers:
        await cl.Message(content=f"--- **Processing:** {ticker} ---").send()
        try:
            config = {"configurable": {"thread_id": "ui_session"}}
            result = await app.ainvoke(
                {"ticker": ticker, "retry_count": 0, "manual_search": True},
                config=config,
            )

            status = result.get("status")
            report = result.get("final_report", "No report generated.")
            chart_bytes = result.get("chart_data")

            elements = []
            if chart_bytes:
                elements.append(
                    cl.Image(content=chart_bytes, name=f"{ticker}_chart", display="inline")
                )

            if status == "FAIL":
                response = f"{report}\n\n*Chart provided for visual reference.*"
            else:
                response = f"**PASSED FIREWALL**\n\n{report}"

            await cl.Message(content=response, elements=elements).send()

        except Exception as e:
            logger.error("Error processing %s: %s", ticker, e)
            await cl.Message(content=f"Error on {ticker}: {e}").send()
        finally:
            plt.close("all")
            gc.collect()
