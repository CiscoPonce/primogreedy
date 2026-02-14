import os
import yfinance as yf
from dotenv import load_dotenv
# Import your new Value Investing Logic
from src.finance_tools import check_financial_health 
from src.email_utils import send_email_report
from src.global_router import MARKET_CONFIG, normalize_ticker, get_official_filing_link

load_dotenv()

# --- MOCK DATA FOR TESTING (Replace with Reddit API later) ---
def scan_community_for_tickers(subreddit_name):
    mock_trending = {
        "pennystocks": ["PLTR", "SOFI", "RKLB"], # USA
        "UKInvesting": ["RR", "BARC", "LLOY"],   # UK 
        "CanadianInvestor": ["SU", "SHOP", "BB"], # Canada
        "ASX_Bets": ["BHP", "FMG", "PLS"]        # Australia
    }
    return mock_trending.get(subreddit_name, [])

def run_global_hunt():
    print("🌍 Starting Global Hunter...")
    report_html = "<h1>🌍 Daily Global Small-Cap Hunter</h1>"
    gems_found = False
    
    for region, config in MARKET_CONFIG.items():
        print(f"🕵️ Scanning {region}...")
        candidates = scan_community_for_tickers(config['reddit_sub'])
        
        region_gems = []
        for raw_ticker in candidates:
            ticker = normalize_ticker(raw_ticker, region)
            
            try:
                # 1. THE FILTER (Small Cap + Value Investing)
                health = check_financial_health(ticker)
                
                # If it passes the Graham/Buffett firewall OR has a Margin of Safety
                if health['status'] == "PASS" or health.get('metrics', {}).get('margin_of_safety', 'N/A') != "N/A":
                    link = get_official_filing_link(ticker, region)
                    gems_found = True
                    region_gems.append(f"""
                        <li>
                            <b>{ticker} ({region})</b><br>
                            Verdict: {health['reason']}<br>
                            Safety Margin: {health['metrics'].get('margin_of_safety')}<br>
                            <a href="{link}">Verify at {config['gov_source']}</a>
                        </li>
                    """)
            except Exception as e:
                print(f"⚠️ Error checking {ticker}: {e}")
                continue
        
        if region_gems:
            report_html += f"<h2>📍 {region} Gems</h2><ul>" + "".join(region_gems) + "</ul>"

    if not gems_found:
        report_html += "<p>No deep value opportunities found today.</p>"

    # --- UPDATED EMAIL LOGIC ---
    # We explicitly target the two colleagues
    recipients = [os.getenv("EMAIL_CISCO"), os.getenv("EMAIL_RAUL")]
    # Remove any None values )
    recipients = [r for r in recipients if r]
    
    print(f"📧 Sending Report to: {recipients}")
    send_email_report("Global Hunter Report", report_html, recipients)

if __name__ == "__main__":
    run_global_hunt()