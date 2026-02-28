import json
import os
from datetime import datetime
import yfinance as yf

PORTFOLIO_FILE = "paper_portfolio.json"

def record_paper_trade(ticker, entry_price, verdict, source):
    """
    Saves a BUY or STRONG BUY recommendation to the local paper tracking ledger.
    """
    v_upper = verdict.strip().upper()
    
    # Check if the verdict actually recommends a buy or watch
    is_tracked = False
    if "STRONG BUY" in v_upper:
        is_tracked = True
        trade_type = "STRONG BUY"
    elif " BUY" in v_upper or v_upper.startswith("BUY"):
        is_tracked = True
        trade_type = "BUY"
    elif "WATCH" in v_upper:
        is_tracked = True
        trade_type = "WATCH"
        
    if not is_tracked:
        return
        
    try:
        portfolio = []
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, "r") as f:
                portfolio = json.load(f)
                
        # Check if already in portfolio to avoid duplicate entries on the same day
        for trade in portfolio:
            if trade['ticker'] == ticker and trade['date'] == datetime.now().strftime("%Y-%m-%d"):
                return
                
        trade = {
            "ticker": ticker,
            "entry_price": entry_price,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "verdict": trade_type,
            "source": source
        }
        
        portfolio.append(trade)
        
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, indent=4)
            
    except Exception as e:
        print(f"Error recording paper trade: {e}")

def evaluate_portfolio():
    """
    Reads the paper portfolio, fetches live prices, and calculates performance.
    Returns a formatted markdown string of the results.
    """
    if not os.path.exists(PORTFOLIO_FILE):
        return "📉 **Paper Portfolio is empty.** The Agent hasn't tracked any stocks (Buy/Watch) yet!"
        
    try:
        with open(PORTFOLIO_FILE, "r") as f:
            portfolio = json.load(f)
            
        if not portfolio:
            return "📉 **Paper Portfolio is empty.** The Agent hasn't tracked any stocks (Buy/Watch) yet!"
            
        total_roi = 0
        winning_trades = 0
        report = "## 📊 PrimoGreedy Agent Track Record\n\n"
        report += "| Ticker | Date Called | Entry Price | Current Price | Return | Verdict |\n"
        report += "|--------|-------------|-------------|---------------|--------|---------|\n"
        
        for trade in portfolio:
            ticker = trade['ticker']
            entry = trade['entry_price']
            
            try:
                stock = yf.Ticker(ticker)
                price = stock.info.get('currentPrice', 0) or stock.info.get('regularMarketPrice', 0)
                currency = stock.info.get('currency', 'USD')
                
                if ticker.endswith(".L") or currency == "GBp" or currency == "GBX":
                    price = price / 100
                    
                if price > 0 and entry > 0:
                    gain_pct = ((price - entry) / entry) * 100
                    emoji = "🟢" if gain_pct > 0 else "🔴"
                    if gain_pct > 0: winning_trades += 1
                    total_roi += gain_pct
                    
                    report += f"| **{ticker}** | {trade['date']} | ${entry:.2f} | ${price:.2f} | {emoji} {gain_pct:.2f}% | {trade['verdict']} |\n"
                else:
                    report += f"| **{ticker}** | {trade['date']} | ${entry:.2f} | Error | N/A | {trade['verdict']} |\n"
            except:
                report += f"| **{ticker}** | {trade['date']} | ${entry:.2f} | Error | N/A | {trade['verdict']} |\n"
                
        avg_roi = total_roi / len(portfolio)
        win_rate = (winning_trades / len(portfolio)) * 100
        
        report += f"\n### 🏆 Agent Performance Summary\n"
        report += f"- **Total Calls:** {len(portfolio)}\n"
        report += f"- **Win Rate:** {win_rate:.1f}%\n"
        report += f"- **Average Return per Trade:** {avg_roi:.2f}%\n"
        
        return report
        
    except Exception as e:
        return f"❌ Error reading portfolio: {e}"
