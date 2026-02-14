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
    """
    TEMPORARY: A curated 'Watchlist' of interesting Small/Mid Caps 
    to test the Graham Logic while we wait for Real-Time API integration.
    """
    
    # These are NOT recommendations, but they are actual Small/Mid caps 
    # often discussed in Value Investing circles.
    
    watchlist = {
        # USA: Looking for Cash-Rich, Low Debt Niche players
        "pennystocks": [
            "PERI",  # Perion (AdTech, historically low P/E)
            "INMD",  # InMode (MedTech, usually high cash, no debt)
            "HZO",   # MarineMax (Consumer Cyclical, often deep value)
            "ZUMZ"   # Zumiez (Retail, often trades near cash value)
        ],
        
        # UK: The AIM Market & Undervalued Mid-Caps
        "UKInvesting": [
            "GAW.L",  # Games Workshop (High ROIC, Niche Moat - "Warhammer")
            "IG.L",   # IG Group (Fintech, cash cow)
            "DOT.L",  # Digital 9 (Digital Infra - often distressed/value play)
            "BOO.L"   # Boohoo (Turnaround/Deep Value play?)
        ],
        
        # CANADA: Tech & Resources (Non-Bank)
        "CanadianInvestor": [
            "CTS.TO", # Converge Tech (Roll-up strategy, mid-cap)
            "LSPD.TO",# Lightspeed (Beaten down tech - potential value?)
            "CG.TO",  # Centerra Gold (Mining - value play?)
        ],
        
        # AUSTRALIA: Niche players
        "ASX_Bets": [
            "DDR.AX", # Dicker Data (High yield, family owned)
            "JIN.AX", # Jumbo Interactive (Lottery resell, moat)
            "CKF.AX"  # Collins Foods (KFC operator, steady cash)
        ]
    }
    
    return watchlist.get(subreddit_name, [])

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