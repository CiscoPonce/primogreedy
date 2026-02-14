# src/global_router.py
import yfinance as yf

# Configuración de Mercados Angloparlantes
MARKET_CONFIG = {
    "USA": {"suffix": "", "currency": "USD", "gov_source": "SEC EDGAR", "reddit_sub": "pennystocks"},
    "UK": {"suffix": ".L", "currency": "GBP", "gov_source": "Companies House", "reddit_sub": "UKInvesting"},
    "CANADA": {"suffix": ".TO", "currency": "CAD", "gov_source": "SEDAR+ (via CEO.ca)", "reddit_sub": "CanadianInvestor"},
    "AUSTRALIA": {"suffix": ".AX", "currency": "AUD", "gov_source": "ASX", "reddit_sub": "ASX_Bets"}
}

def get_official_filing_link(ticker, region):
    """Generates the direct link to the Source of Truth."""
    # Remove the suffix to get the raw symbol (e.g., "SU.TO" -> "SU")
    base = ticker.replace(MARKET_CONFIG[region]['suffix'], "")
    
    if region == "USA":
        # SEC EDGAR Official Search
        return f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={base}&owner=exclude"
    
    elif region == "UK":
        # Companies House Beta
        return f"https://find-and-update.company-information.service.gov.uk/search?q={base}"
    
    elif region == "CANADA":
        # 🟢 SMART FIX: SEDAR+ doesn't allow deep links. 
        # CEO.ca mirrors SEDAR filings legally and allows direct linking.
        return f"https://ceo.ca/{base}?tab=filings"
    
    elif region == "AUSTRALIA":
        # ASX Official Page
        return f"https://www2.asx.com.au/markets/company/{base}"
    
    # Fallback if region is unknown
    return f"https://google.com/search?q={ticker}+investor+relations"

def normalize_ticker(ticker, region):
    """Ensures the ticker matches Yahoo Finance format."""
    suffix = MARKET_CONFIG[region]['suffix']
    ticker = ticker.upper().strip()
    
    # If it doesn't have the suffix, add it
    if not ticker.endswith(suffix) and suffix != "":
        return f"{ticker}{suffix}"
        
    return ticker