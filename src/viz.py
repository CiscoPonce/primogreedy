import matplotlib.pyplot as plt
import yfinance as yf
import io

def get_stock_chart(ticker: str):
    """
    Generates a 6-month price chart and returns the image as bytes.
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo")
        
        if hist.empty: return None

        plt.figure(figsize=(10, 5))
        plt.plot(hist.index, hist['Close'], label='Price', color='#00ff00', linewidth=2)
        plt.title(f"{ticker} - 6 Month Trend", color='white')
        plt.grid(True, alpha=0.3)
        
        # Dark Mode Style
        ax = plt.gca()
        ax.set_facecolor('#0e1117')
        plt.gcf().set_facecolor('#0e1117')
        ax.tick_params(colors='white')
        
        # Save to RAM
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf.getvalue()
        
    except:
        return None