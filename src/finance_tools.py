import yfinance as yf

def check_financial_health(ticker: str):
    """
    The Logic Firewall (v2).
    Analyzes:
    1. Solvency (Debt/EBITDA, Capital Ratio) -> Safety
    2. Valuation (P/E Ratio) -> Price
    """
    try:
        print(f"🛡️  Firewall Checking: {ticker}...")
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # --- 1. SOLVENCY CHECKS (The Safety Net) ---
        sector = info.get('sector', 'Unknown')
        industry = info.get('industry', 'Unknown')
        
        # Banks
        if "Financial" in sector and "Bank" in industry:
            total_assets = info.get('totalAssets', 0)
            total_equity = info.get('totalStockholderEquity', 0)
            if total_assets > 0:
                capital_ratio = (total_equity / total_assets) * 100
                if capital_ratio < 8.0:
                     return {"status": "FAIL", "reason": f"Bank Capital too low: {capital_ratio:.2f}% (<8%)"}

        # Real Estate
        elif "Real Estate" in sector:
            total_debt = info.get('totalDebt', 0)
            total_assets = info.get('totalAssets', 0)
            if total_assets > 0:
                ltv = (total_debt / total_assets) * 100
                if ltv > 60.0:
                    return {"status": "FAIL", "reason": f"LTV too high: {ltv:.2f}% (>60%)"}

        # Standard Companies (Debt/EBITDA)
        else:
            ebitda = info.get('ebitda', 0)
            total_debt = info.get('totalDebt', 0)
            cash = info.get('totalCash', 0)
            net_debt = total_debt - cash
            
            # Check if they are losing money
            if ebitda <= 0:
                 return {"status": "FAIL", "reason": "Company is losing money (Negative EBITDA)"}
            
            # Only check leverage if they actually have Net Debt
            if net_debt > 0:
                leverage = net_debt / ebitda
                if leverage > 4.0: # Relaxed slightly to 4.0x for modern markets
                    return {"status": "FAIL", "reason": f"Debt/EBITDA Dangerous: {leverage:.2f}x (>4.0x)"}

        # --- 2. VALUATION CHECK (The "Price" Filter) ---
        # This is the NEW section!
        pe_ratio = info.get('trailingPE')
        forward_pe = info.get('forwardPE')
        
        # Use Forward P/E if Trailing is missing (common for growth stocks)
        final_pe = pe_ratio if pe_ratio is not None else forward_pe
        
        if final_pe is None:
            # If NO P/E exists, it usually means no earnings (Risk!)
            return {"status": "FAIL", "reason": "Valuation Unknown (No P/E Ratio found - likely unprofitable)"}
        
        if final_pe > 60.0:
            return {"status": "FAIL", "reason": f"Stock is too expensive! P/E: {final_pe:.2f} (>60x)"}

        if final_pe < 0:
             return {"status": "FAIL", "reason": f"Company is losing money! P/E is negative."}

        # If it passed all checks:
        return {"status": "PASS", "reason": f"Healthy! Leverage Safe & P/E Reasonable ({final_pe:.2f}x)"}

    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}