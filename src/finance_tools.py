import yfinance as yf

# --- CONFIGURATION: SECTOR SPECIFIC RULES ---
SECTOR_CONFIG = {
    "Financial Services": {
        "debt_metric": "debtToEquity", 
        "val_metric": "priceToBook", 
        "debt_max": 3.0, 
        "exclude_ebitda": True 
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
        "debt_max": 100.0, 
        "exclude_ebitda": False
    },
    "Default": {
        "debt_metric": "debtToEbitda", 
        "val_metric": "forwardPE", 
        "debt_max": 3.5, 
        "exclude_ebitda": False
    }
}

def calculate_graham_number(info):
    """
    Classic Value Investing Formula: Sqrt(22.5 * EPS * BookValue)
    """
    try:
        eps = info.get('trailingEps', 0)
        bvps = info.get('bookValue', 0)
        
        # If the company is losing money (Negative EPS), Graham Number is 0
        if eps is None or bvps is None or eps <= 0 or bvps <= 0:
            return 0
            
        return (22.5 * eps * bvps) ** 0.5
    except:
        return 0

def check_financial_health(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info 
        
        sector = info.get('sector', 'Default')
        config = SECTOR_CONFIG.get(sector, SECTOR_CONFIG['Default'])
        
        # --- 🟢 FIX: FORCE UK CURRENCY NORMALIZATION ---
        current_price = info.get('currentPrice', 0)
        currency = info.get('currency', 'USD')
        
        # Yahoo Finance often reports UK stocks (LSE) in Pence (GBX)
        # But we need Pounds (GBP) to match the Intrinsic Value.
        # Logic: If it ends in .L OR currency is GBX/GBp, divide by 100.
        if ticker.endswith(".L") or currency == "GBp" or currency == "GBX":
            current_price = current_price / 100
        
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
        
        margin_of_safety = "N/A"
        
        if intrinsic_val > 0 and current_price > 0:
            # Calculate Margin
            raw_margin = (intrinsic_val - current_price) / intrinsic_val * 100
            margin_of_safety = f"{round(raw_margin, 1)}%"
        elif intrinsic_val == 0:
             margin_of_safety = "No Value (Unprofitable)"

        # Format Debt for display
        debt_equity_raw = info.get('debtToEquity', 0)
        debt_equity_str = f"{debt_equity_raw}%" if debt_equity_raw else "N/A"
        
        metrics = {
            "sector": sector,
            "current_price": current_price,
            "currency": currency,
            "intrinsic_value": round(intrinsic_val, 2),
            "margin_of_safety": margin_of_safety,
            "debt_to_equity": debt_equity_str,
            "peg_ratio": info.get('pegRatio', 'N/A')
        }

        return {
            "status": "PASS", 
            "reason": f"Solvent. Sector: {sector}.",
            "metrics": metrics
        }
        
    except Exception as e:
        return {"status": "PASS", "reason": f"Data Warning: {str(e)}", "metrics": {}}