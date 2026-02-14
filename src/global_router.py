# src/global_router.py
# (No changes needed, use the version from the previous message)
MARKET_CONFIG = {
    "USA": {"suffix": "", "gov_source": "SEC EDGAR", "reddit_sub": "pennystocks"},
    "UK": {"suffix": ".L", "gov_source": "Companies House", "reddit_sub": "UKInvesting"},
    "CANADA": {"suffix": ".TO", "gov_source": "SEDAR+", "reddit_sub": "CanadianInvestor"},
    "AUSTRALIA": {"suffix": ".AX", "gov_source": "ASX", "reddit_sub": "ASX_Bets"}
}

def get_official_filing_link(ticker, region):
    base = ticker.replace(MARKET_CONFIG[region]['suffix'], "")
    if region == "USA": return f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={base}&owner=exclude"
    if region == "UK": return f"https://find-and-update.company-information.service.gov.uk/search?q={base}"
    if region == "AUSTRALIA": return f"https://www2.asx.com.au/markets/company/{base}"
    return "https://google.com/search?q=" + ticker + "+investor+relations"

def normalize_ticker(ticker, region):
    suffix = MARKET_CONFIG[region]['suffix']
    if not ticker.endswith(suffix) and suffix: return f"{ticker}{suffix}"
    return ticker

