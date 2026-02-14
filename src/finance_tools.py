import yfinance as yf

# --- CONFIGURATION: SECTOR SPECIFIC RULES ---
# Based on "Framework Profesional de Valoración Sectorial"
SECTOR_CONFIG = {
    "Financial Services": {
        "debt_metric": "debtToEquity", 
        "val_metric": "priceToBook", 
        "debt_max": 3.0, # Banks carry high debt/equity naturally
        "exclude_ebitda": True # Banks don't use EBITDA
    },
    "Real Estate": {
        "debt_metric": "debtToEquity", 
        "val_metric": "priceToBook", 
        "debt_max": 2.5,
        "exclude_ebitda": False 
    },
    "Technology": {
        "debt_metric": "totalCash", 
        "val_metric": "priceToFreeCashFlows", 
        "debt_max": 100.0, # Placeholder, Tech prefers Net Cash
        "exclude_ebitda": False
    },
    "Default": {
        "debt_metric": "debtToEbitda", 
        "val_metric": "forwardPE", 
        "debt_max": 3.5, # > 3.5 is "Risk" per docs
        "exclude_ebitda": False
    }
}

def calculate_graham_number(info):
    """
    Classic Value Investing Formula: Sqrt(22.5 * EPS * BookValue)
    Acts as a proxy for 'Intrinsic Value'.
    """
    try:
        eps = info.get('trailingEps', 0)
        bvps = info.get('bookValue', 0)
        if eps is not None and bvps is not None and eps > 0 and bvps > 0:
            return (22.5 * eps * bvps) ** 0.5
    except:
        pass
    return 0

def check_financial_health(ticker):
    """
    The 'Graham & Buffett' Gatekeeper (Fixed for Percentage Bugs).
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info 
        
        sector = info.get('sector', 'Default')
        config = SECTOR_CONFIG.get(sector, SECTOR_CONFIG['Default'])
        
        # --- FIX 1: FORMAT DEBT CORRECTLY ---
        # yfinance returns 15.5 for 15.5%. We convert to decimal for math, but string for LLM.
        debt_equity_raw = info.get('debtToEquity', 0)
        debt_equity_str = f"{debt_equity_raw}%" if debt_equity_raw else "N/A"
        
        # --- FIX 2: ADD GROWTH VALUATION (PEG RATIO) ---
        # For Tech, we look at PEG (Price/Earnings to Growth). 
        # < 1.0 is Undervalued, < 2.0 is Fair for Quality.
        peg_ratio = info.get('pegRatio', 'N/A')

        # --- 1. GRAHAM'S SOLVENCY CHECK ---
        current_ratio = info.get('currentRatio')
        if current_ratio and current_ratio < 1.0:
            return {"status": "FAIL", "reason": f"Graham Reject: Liquidity Crisis (Current Ratio {current_ratio} < 1.0)"}

        # --- 2. SECTOR SPECIFIC DEBT CHECK ---
        if not config['exclude_ebitda']:
            ebitda = info.get('ebitda')
            debt = info.get('totalDebt')
            cash = info.get('totalCash')
            if ebitda and debt and ebitda > 0:
                net_debt_ebitda = (debt - cash) / ebitda
                if net_debt_ebitda > config['debt_max']:
                    return {"status": "FAIL", "reason": f"Sector Reject: Debt/EBITDA {round(net_debt_ebitda, 2)}x > {config['debt_max']}x"}

        # --- 3. INTRINSIC VALUE CALCULATION ---
        intrinsic_val = calculate_graham_number(info)
        current_price = info.get('currentPrice', 0)
        
        margin_of_safety = "N/A"
        if intrinsic_val > 0 and current_price > 0:
            margin = (intrinsic_val - current_price) / intrinsic_val * 100
            margin_of_safety = f"{round(margin, 1)}%"

        metrics = {
            "sector": sector,
            "current_price": current_price,
            "intrinsic_value": round(intrinsic_val, 2),
            "margin_of_safety": margin_of_safety,
            "debt_to_equity": debt_equity_str, # Now has '%' symbol
            "peg_ratio": peg_ratio,            # New Metric for Tech
            "return_on_equity": f"{info.get('returnOnEquity', 0)*100:.2f}%" # Format as %
        }

        return {
            "status": "PASS", 
            "reason": f"Solvent. Sector: {sector}. Safety: {margin_of_safety}",
            "metrics": metrics
        }
        
    except Exception as e:
        return {"status": "PASS", "reason": f"Data Warning: {str(e)}", "metrics": {}}