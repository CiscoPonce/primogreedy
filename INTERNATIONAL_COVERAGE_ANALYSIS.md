# International Coverage & Discovery Quality Analysis

**Scope**: whale_hunter.py pipeline — scout → gatekeeper → analyst → email, fan-out across USA/UK/Canada/Australia  
**Date**: 2026-09-09  
**Evidence basis**: Code trace of all files in src/ with file:line citations, Finnhub API documentation, yfinance behavior

---

## A. Full Code Path Trace: US vs Non-US Region

### Pipeline flow for any region

1. **Scout** (`whale_hunter.py:66-121`): Gets trending tickers from Brave → merges into screener pool → yfinance validates → scores → picks top candidate
2. **Gatekeeper** (`whale_hunter.py:124-186`): Fetches `yf.Ticker(ticker).info` → checks price, market cap, financial health → stores full `info` dict as `state["financial_data"]` (line 179)
3. **Analyst** (`whale_hunter.py:189-364`): Reads `state["financial_data"]` → builds prompt with fundamentals, SEC filings, insider data → LLM generates verdict

### Side-by-side: What the analyst actually receives

| Data Source | USA Candidate | UK/Canada/Australia Candidate |
|---|---|---|
| **yfinance `stock.info`** (price, EPS, book_value, market_cap, sector, ebitda, etc.) | ✅ Fetched by gatekeeper (line 143-144), stored as `financial_data` (line 179), read by analyst (line 192). Used for Graham number calc (lines 197-209) and prompt (line 287) | ✅ **Identical path** — gatekeeper fetches same `stock.info` for ALL regions (line 143-144), stores identically (line 179), analyst reads identically (line 192). Graham number works the same (lines 197-209) |
| **SEC EDGAR filings** (10-K/10-Q MD&A + Risk Factors) | ✅ Lines 215-221: `if region == "USA" and "." not in ticker` → fetches via `get_sec_filings` (sec_edgar.py:209-264) → returns parsed MD&A and Risk Factors sections | ❌ **Never fetched.** Guard at line 216 blocks all non-US. No equivalent implemented. |
| **Finnhub insider sentiment** (MSPR, net buy/sell) | ✅ Line 229: `get_insider_sentiment.invoke({"ticker": ticker})` — returns insider sentiment from Finnhub `/stock/insider-sentiment` endpoint | ❌ **Guarded at finance_tools.py:153**: `if "." in ticker: return "Insider data not supported for non-US ticker"` — hardcoded rejection |
| **Finnhub company news** (top 3 headlines) | ✅ Line 230: `get_company_news.invoke({"ticker": ticker})` — returns recent news from Finnhub `/company-news` endpoint | ❌ **Guarded at finance_tools.py:193**: `if "." in ticker: return "Finnhub news not supported for non-US ticker"` — hardcoded rejection |
| **Finnhub basic financials** (52-week range, beta, margins, ROE) | ✅ Line 231: `get_basic_financials.invoke({"ticker": ticker})` — returns key metrics from Finnhub `/stock/metric` endpoint | ❌ **Guarded at finance_tools.py:221**: `if "." in ticker: return "Finnhub fundamentals not supported for non-US ticker"` — hardcoded rejection |
| **Finnhub insider buys** (net shares, sentiment, MSPR) | ✅ Line 235: `get_insider_buys(ticker)` → insider_feed.py:28-29 guard returns `{"sentiment": "N/A (non-US)"}` if "." in ticker | ❌ **Guarded at insider_feed.py:28-29**: returns empty `N/A` dict immediately |
| **SEC Form 4 feed** (real-time insider filings) | ✅ Available via `get_sec_form4_feed` (insider_feed.py:70-127) — US-only by design (SEC EDGAR) | ❌ N/A — US-only regulatory source |
| **Brave news search** (general web search) | ✅ Line 212: `brave_search(f"{ticker} stock analysis catalysts")` — but NOT included in `deep_fundamentals` for US (overridden by Finnhub block at line 225-237) | ✅ Line 239: `deep_fundamentals = f"NEWS: {str(news)[:1500]}"` — **this is ALL the non-US analyst gets** as contextual fundamentals |

### The critical divergence — `deep_fundamentals` variable

```python
# whale_hunter.py:224-239
deep_fundamentals = ""
if region == "USA" and "." not in ticker:       # ← line 225: BOTH conditions must be true
    logger.info("Researching Finnhub databases for %s...", ticker)
    context = ""
    try:
        context += get_insider_sentiment.invoke({"ticker": ticker}) + "\n"  # line 229
        context += get_company_news.invoke({"ticker": ticker}) + "\n"       # line 230
        context += get_basic_financials.invoke({"ticker": ticker}) + "\n"   # line 231
    except Exception as exc:
        logger.warning("Finnhub tool error for %s: %s", ticker, exc)

    insider = get_insider_buys(ticker)                                      # line 235
    context += f"\nInsider Sentiment (6mo): {insider['sentiment']}..."      # line 236
    deep_fundamentals = f"DEEP FUNDAMENTALS (FINNHUB + INSIDER FEED):\n{context}"  # line 237
else:
    deep_fundamentals = f"NEWS: {str(news)[:1500]}"                         # line 239 ← ALL non-US lands here
```

**Summary**: The gatekeeper fetches `stock.info` for all regions equally — that data IS available to the analyst via `state["financial_data"]`. The analyst uses it for the Graham number calculation and the hard-data prompt line (line 287). But the **qualitative deep-fundamentals context** (insider sentiment, company news, basic financials) is completely missing for non-US. The analyst for a UK stock gets a 1500-char Brave snippet as its only "deep" context, while a US stock gets 4+ data sources.

---

## B. Finnhub International Support Assessment

### B.1 Does Finnhub actually support non-US symbols?

**Finnhub API documentation findings** (from finnhub.io/pricing, finnhub.io/docs/api):

| Endpoint | Free Plan | Paid Plan (All-In-One) | Symbol Format |
|---|---|---|---|
| `company_basic_financials` (metric=all) | US only | **Global** | `symbol` param — likely works with Yahoo-style suffixes (.L, .TO, .AX) |
| `company_news` | US only (1yr) | Global (20yr) | `symbol` param |
| `insider-sentiment` | US only | US only | `symbol` param |
| `insider-transactions` | **Global** (US, UK, CA, AU, India, EU) | Global | `symbol` param |
| `news-sentiment` | US only | US only | `symbol` param |

**Key evidence** from Finnhub docs search:
- `insider-transactions`: *"Company insider transactions data sourced from Form 3,4,5, SEDI and relevant companies' filings. This endpoint covers US, UK, Canada, Australia, India, and all major EU markets."*
- `company_basic-financials`: Page title reads "Global Company Basic Financials" — but free plan coverage table shows "US" only under Fundamental Data Coverage.
- Finnhub symbology: *"We use the following symbology to identify stocks on Finnhub `Exchange_Ticker.Exchange_Code`."* — so `.L`, `.TO`, `.AX` suffixes are native Finnhub format, not just Yahoo Finance quirks.

### B.2 Are the `if "." in ticker` guards needed?

| Tool | Current Guard | Actually Needed? | Rationale |
|---|---|---|---|
| `get_insider_sentiment` (finance_tools.py:153) | Rejects all "." tickers | **Partially** — insider-sentiment is US-only even on paid plans, but insider-transactions IS global | Guard is correct for this specific endpoint. However, the code uses `insider-sentiment` not `insider-transactions` — switching to the latter would unlock global coverage. |
| `get_company_news` (finance_tools.py:193) | Rejects all "." tickers | **No, if on paid plan** — company_news supports global on All-In-On plan. On free plan, US only. | Guard should be conditional on plan tier or simply try-and-fail gracefully instead of hard-rejecting. |
| `get_basic_financials` (finance_tools.py:221) | Rejects all "." tickers | **No, if on paid plan** — company_basic_financials is global on paid plans. On free plan, US only. | Same as above. |
| `get_insider_buys` (insider_feed.py:28) | Rejects all "." tickers | **Definitely no** — calls `insider-sentiment` endpoint which is US-only, but could switch to `insider-transactions` which IS global. | Guard is correct for current endpoint choice, but wrong if we switch to `insider-transactions`. |

**Verdict**: The guards are overly conservative. They were written assuming Finnhub = US only, but Finnhub's paid plan provides global fundamentals and insider transactions. Even on the free plan, `insider-transactions` works globally. The correct approach: remove the `.` guard, pass the bare ticker (strip the suffix) to Finnhub, and handle API-level "not found" errors gracefully rather than pre-emptively blocking.

### B.3 International filing/regulator sources

`global_router.py:5-10` already defines the sources:

| Region | Gov Source | API Available? | Current Use |
|---|---|---|---|
| USA | SEC EDGAR | ✅ Full API (`sec_edgar.py`) | ✅ Used in analyst (whale_hunter.py:217-221) |
| UK | Companies House | ✅ REST API (`find-and-update.company-information.service.gov.uk`) | ❌ URL-only in `global_router.py:22-23` — not integrated |
| Canada | SEDAR+ (via CEO.ca) | ⚠️ CEO.ca mirrors filings but no official API | ❌ URL-only in `global_router.py:27-28` — not integrated |
| Australia | ASX | ⚠️ Company pages available, limited structured API | ❌ URL-only in `global_router.py:30-31` — not integrated |

`global_router.py:12-35` (`get_official_filing_link`) generates URLs but they are never used in the pipeline. The function exists as dead code — no node calls it.

---

## C. Recommendations: Giving Non-US Regions Real Fundamentals

### Option (i): Remove `.`-guard and pass suffix-stripped symbol to Finnhub

**Where to change**: `finance_tools.py:153`, `finance_tools.py:193`, `finance_tools.py:221`, `insider_feed.py:28-29`

**Mechanism**: Before calling Finnhub, strip the Yahoo suffix to get the bare ticker that Finnhub expects:
- `AFC.L` → `AFC` (Finnhub native format is also `AFC.L`, so the suffix can actually be kept)
- `NUMI.TO` → `NUMI` (Finnhub uses `NUMI` or `NUMI.TO`)
- `VUL.AX` → `VUL`

**Trade-off**: Finnhub free plan limits fundamental data to US. Even with the guard removed, `company_basic_financials` and `company_news` will return empty on free plan. Only `insider-transactions` would work globally. **This change alone solves ~20% of the gap.**

### Option (ii): Pass yfinance `info` dict (already fetched) into analyst prompt for ALL regions

**Where to change**: `whale_hunter.py:224-239` — currently the `else` branch at line 238-239 discards the rich `info` dict and replaces it with a Brave snippet.

**Mechanism**: The gatekeeper already fetches the full `stock.info` dict at line 143-144 and stores it as `state["financial_data"]` at line 179. The analyst reads it at line 192 as `info = state.get("financial_data", {})`. This dict contains: `trailingEps`, `bookValue`, `ebitda`, `freeCashflow`, `totalCash`, `totalDebt`, `currentRatio`, `priceToBook`, `sector`, `shortName`, `currency`, `marketCap`, `52WeekHigh`, `52WeekLow`, `beta`, `returnOnEquity`, `profitMargins`, and many more fields — **for ALL regions** (yfinance returns these regardless of region).

**Concrete change at line 238-239**:
```python
# BEFORE:
else:
    deep_fundamentals = f"NEWS: {str(news)[:1500]}"

# AFTER:
else:
    # Enrich with whatever yfinance returned for this region
    enrichment = (
        f"FINANCIAL SNAPSHOT ({region}):\n"
        f"Sector: {info.get('sector', 'N/A')}\n"
        f"Industry: {info.get('industry', 'N/A')}\n"
        f"52W High: {info.get('52WeekHigh', 'N/A')} | 52W Low: {info.get('52WeekLow', 'N/A')}\n"
        f"ROE: {info.get('returnOnEquity', 'N/A')} | Profit Margin: {info.get('profitMargins', 'N/A')}\n"
        f"Free Cash Flow: {info.get('freeCashflow', 'N/A')} | Total Cash: {info.get('totalCash', 'N/A')}\n"
        f"Total Debt: {info.get('totalDebt', 'N/A')} | Debt/Equity: {info.get('debtToEquity', 'N/A')}\n"
        f"Current Ratio: {info.get('currentRatio', 'N/A')}\n"
        f"Beta: {info.get('beta', 'N/A')}\n"
        f"\nRECENT NEWS: {str(news)[:1000]}"
    )
    deep_fundamentals = enrichment
```

**Trade-off**: This is a zero-cost change (data already fetched) that gives the non-US analyst the same quantitative depth the US analyst gets from `get_basic_financials`. It doesn't solve insider data or news, but it's the single highest-ROI fix.

### Option (iii): Both (i) + (ii)

**Recommended approach**. Implement (ii) immediately (free, high-impact). Implement (i) as a follow-up, guarded by a `try/except` so that Finnhub failures on free plan are logged but don't break the pipeline. This way, if/when the team upgrades to paid Finnhub plan, international Finnhub data automatically starts flowing.

### Specific change locations

| Change | File:Line | Effort |
|---|---|---|
| Pass yfinance `info` to analyst for non-US | `whale_hunter.py:238-239` | Low (10 lines) |
| Remove `.`-guard on `get_insider_sentiment` | `finance_tools.py:153-154` | Low (1 line) |
| Remove `.`-guard on `get_company_news` | `finance_tools.py:192-193` | Low (1 line) |
| Remove `.`-guard on `get_basic_financials` | `finance_tools.py:220-221` | Low (1 line) |
| Switch `get_insider_buys` to use `insider-transactions` endpoint | `insider_feed.py:28-29` + `31-37` | Medium (rewrite endpoint) |
| Remove `.`-guard on `get_insider_buys` | `insider_feed.py:28-29` | Low (1 line) |
| Add Companies House integration for UK filings | New module or extend `sec_edgar.py` | High |
| Add CEO.ca / SEDAR+ integration for Canada | New module | High |

---

## D. Discovery Quality: Dead Symbols & Pool Exhaustion

### D.1 The dead-ticker problem — evidence

**Root cause 1: Brave `extract_tickers` produces noise**

`ticker_utils.py:57-109` extracts uppercase tokens from Brave search results. The `NOISE_WORDS` filter at lines 14-52 catches many common words but misses:
- `SOPA` (was in seed pool at screener.py:26 — now dead/delisted)
- `OTCQB` (exchange name, not a ticker — passes filter because it's 6 chars, under `_MAX_TICKER_LEN = 8`)
- `SMALL`, `TELLS`, `AXSO` — appear in Brave search text as regular English words in ALL CAPS context

**Root cause 2: Merged into pool without pre-validation**

`screener.py:66-68`:
```python
pool = list(_SEED_POOLS.get(region, []))
if extra_tickers:
    pool.extend(t for t in extra_tickers if t not in pool)  # ← no validation here
```

The `screen_microcaps` function at lines 74-113 does validate via yfinance (`stock.info`), but catches exceptions silently (line 111-112: `logger.debug("Screener skip %s: %s", ticker, exc)`). Dead tickers that return empty info get skipped — **but the issue is different**: a dead ticker that returns `marketCap=0` gets skipped, but the Brave-extracted ticker may never make it to the screener at all because it fails the yfinance check, leaving fewer candidates in the pool.

**Root cause 3: Pool exhaustion — tiny seed pools**

`_SEED_POOLS` sizes (screener.py:22-46):
- USA: 23 tickers
- UK: 10 tickers
- Canada: 13 tickers
- Australia: 9 tickers

After the scout picks one and marks it seen (`whale_hunter.py:79, 115`), subsequent runs deplete the pool quickly. With `MAX_RETRIES = 3` (whale_hunter.py:56), the pipeline can retry 3 times, but if the pool has only 9-10 valid names and some are dead, exhaustion happens fast.

**Root cause 4: "No ticker to evaluate" cascading failure**

`whale_hunter.py:97-105`:
```python
if not screened:
    logger.warning("No candidates passed screener for %s", region)
    return {"ticker": "NONE", "candidates": []}    # ← leads to gatekeeper line 136-139 → retry

if not fresh:
    logger.warning("All screened candidates already seen for %s", region)
    return {"ticker": "NONE", "candidates": []}    # ← same path
```

The gatekeeper at line 136-139 receives `"NONE"` and increments retry count. After `MAX_RETRIES=3` exhausted retries, it routes to `email` (line 133: `return "email"`), sending a "Hunt Failed" email. **A single bad Brave extraction run can waste the entire region's budget.**

### D.2 Options and trade-offs

| Option | Change Location | Pros | Cons | Recommendation |
|---|---|---|---|---|
| **(i) Pre-validate Brave tickers against yfinance before merging** | `screener.py:67-68` or `whale_hunter.py:91` | Prevents dead tickers from entering pool; no wasted API calls in screener loop | Adds N yfinance API calls per Brave batch; Brave tickers that are valid but outside microcap range get filtered at same step anyway | ✅ **Do this.** Validate immediately after `get_trending_tickers_from_brave` returns. Filter to only tickers where `yf.Ticker(t).info.get("marketCap", 0) > 0`. Cost: ~100ms per valid ticker. |
| **(ii) Pop more than one candidate per region** | `whale_hunter.py:108-112` (currently `top_n=5` but only pops `ranked[0]`) | More resilience — if first pick fails gatekeeper, next one is ready | Doesn't solve pool exhaustion; just delays it. Also, gatekeeper already retries via scout (line 134) | ⚠️ **Partial fix.** Currently the pipeline already retries via the scout→gatekeeper loop. The real issue is pool size. |
| **(iii) Improve `extract_tickers` noise filtering** | `ticker_utils.py:14-52` (NOISE_WORDS) | Reduces noise at source | Can never catch all false positives; real fix is validation not filtering | ⚠️ **Low priority.** Add known false positives (`OTCQB`, `SMALL`, exchange names) but don't rely on this. |
| **(iv) Larger/fresher seed pool** | `screener.py:22-46` (_SEED_POOLS) | Most impactful single change for pool exhaustion | Requires manual curation or automated refresh. Stale pools lose valid tickers over time. | ✅ **Critical.** See below. |

### D.3 The real fix: larger universe + automated refresh

The curated seed pools are tiny (9-23 names per region) and were "audited 2026-03-05" (screener.py:24). In 6 months, some will have been acquired, delisted, or grown out of microcap range. The Brave trending feed is supposed to inject fresh names, but it's unreliable (noise) and the single-candidate-per-run design means exhaustion is inevitable.

**Recommended approach**:
1. **Expand seed pools**: Use yfinance's screener API or index constituents to pull 50-100 micro-cap names per region instead of 10-23. For example, LSE AIM market has 50+ micro-caps; TSX Venture has hundreds.
2. **Refresh seed pools periodically**: Add a weekly job that pulls fresh constituents from index listings (Russell Microcap, AIM, TSXV, ASX Small Ords) and updates `_SEED_POOLS`.
3. **Pre-validate Brave tickers**: At `whale_hunter.py:91`, after `get_trending_tickers_from_brave`, validate each against yfinance and drop any that don't resolve (marketCap == 0 or exception).
4. **Don't stop at one candidate when pool is fresh**: If the pool has >10 unseen candidates, pop 2-3 per run to increase coverage rate.

---

## E. Currency Correctness Assessment

### E.1 Current currency handling

**`normalize_price` (ticker_utils.py:136-140)**:
```python
def normalize_price(price: float, ticker: str, currency: str = "USD") -> float:
    """Convert UK pence to pounds when needed."""
    if ticker.endswith(".L") or currency in ("GBp", "GBX"):
        return price / 100
    return price
```

This converts GBp → GBP for UK prices only. No conversion for CAD, AUD, or any other currency.

**Where it's called**:
- `gatekeeper_node` line 151: `price = normalize_price(price, ticker, currency)` — only price is converted
- `screen_microcaps` line 87: `price = normalize_price(price, ticker, currency)` — only price is converted

### E.2 Currency mixing in Graham Number calculation

**`whale_hunter.py:197-209`**:
```python
price = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
eps = info.get("trailingEps", 0) or 0
book_value = info.get("bookValue", 0) or 0

if eps > 0 and book_value > 0:
    strategy = "GRAHAM CLASSIC"
    valuation = (22.5 * eps * book_value) ** 0.5
    thesis = f"Profitable. Graham Value ${valuation:.2f} vs Price ${price:.2f}."
```

**Problem**: yfinance returns EPS, book_value, price, ebitda, freeCashflow, totalCash, totalDebt all in the **reporting currency** of that exchange:
- UK (.L): GBP (after pence→pound conversion for price only)
- Canada (.TO/.V): CAD
- Australia (.AX): AUD

The Graham number `sqrt(22.5 * EPS * BookValue)` is a currency-neutral ratio internally (it produces a per-share value in the same currency as EPS and book_value), **BUT** it's compared to `price` which may be in a different currency after `normalize_price`.

**Concrete example for UK**:
- yfinance returns: `currentPrice = 150` (GBp), `trailingEps = 12.5` (GBP), `bookValue = 85.0` (GBP)
- After `normalize_price`: price = 1.50 (GBP)
- Graham number = `sqrt(22.5 * 12.5 * 85.0) = sqrt(23906.25) = 154.61` (GBP)
- Thesis: "Graham Value $154.61 vs Price $1.50" — **WRONG comparison**. The `$` prefix in the prompt is misleading (it's GBP not USD), and the Graham number is in GBP but labeled as `$`.

**For Canada**:
- yfinance returns: `currentPrice = 2.50` (CAD), `trailingEps = 0.30` (CAD), `bookValue = 1.80` (CAD)
- Price is NOT normalized (no CAD→USD conversion)
- Graham number = `sqrt(22.5 * 0.30 * 1.80) = sqrt(12.15) = 3.49` (CAD)
- Thesis: "Graham Value $3.49 vs Price $2.50" — math is correct (both CAD), but the `$` prefix implies USD to the reader, and the LLM doesn't know it's CAD

### E.3 Where currency mixing causes wrong conclusions

| Component | File:Line | Currency Behavior | Risk |
|---|---|---|---|
| **Graham number calculation** | `whale_hunter.py:201-203` | EPS and book_value are same currency → math is correct internally | Low — ratio works within same currency |
| **Graham vs Price comparison** | `whale_hunter.py:204` | Price may be converted (GBP→GBP), EPS/BV native. All same currency → comparison is correct | Low — if price was properly normalized |
| **Margin of safety in gatekeeper** | `finance_tools.py:117-122` | `intrinsic_val = calculate_graham_number(info)` using EPS and BV in native currency; `current_price` is normalized. Both same currency → correct | Low |
| **Scoring: Graham margin** | `scoring.py:40-48` | Uses `candidate["price"]` (normalized) vs `graham(eps, bv)` (native currency). Same currency → correct | Low |
| **Scoring: P/B ratio** | `scoring.py:51-56` | Uses `info.get("priceToBook")` — yfinance pre-computes this in native currency | None — yfinance handles internally |
| **Email report: market cap** | `whale_hunter.py:390` | `f"Cap: ${state.get('market_cap', 0):,.0f}"` — market cap is in native currency (CAD/AUD/GBP) but prefixed with `$` | Medium — misleading but not a logic error |
| **LLM prompt: dollar signs** | `whale_hunter.py:287, 204, 209` | `f"Price: ${price}"` — uses `$` for all currencies | Medium — LLM may interpret as USD, producing wrong mental model |

### E.4 Cross-currency comparison risk

**The real danger is NOT within a single stock's calculation** (EPS, BV, and price are all in the same currency). The risk is:
1. **The LLM doesn't know the currency** — the prompt says `$price` which implies USD. A UK stock priced at £1.50 looks like $1.50 USD to the LLM, making it appear much cheaper than it is.
2. **Cross-region comparison** — if the email report compares market caps across regions (e.g., "UK avg $50M vs US avg $30M"), the numbers are in different currencies.
3. **Kelly criterion position sizing** — `position_size` (whale_hunter.py:263, 325) is a percentage of portfolio, so currency doesn't matter here.

### E.5 Currency normalization recommendations

| Change | Location | Effort | Priority |
|---|---|---|---|
| **Include currency symbol in all prompts** — change `$` to the actual currency code | `whale_hunter.py:204, 209, 287` | Low | ✅ High — prevents LLM misinterpretation |
| **Fetch live FX rates and convert to USD** for all non-US stocks | `whale_hunter.py:197-199` (new code) | Medium | ✅ High — enables true cross-region comparison |
| **Update `normalize_price` to convert to USD** | `ticker_utils.py:136-140` | Medium | Medium — full currency conversion |
| **Include currency in `deep_fundamentals` context** | `whale_hunter.py:238-239` (new code) | Low | ✅ High — LLM needs to know currency |
| **Update email report to show currency** | `whale_hunter.py:390` | Low | Medium — cosmetic but important for users |

**Recommended approach**: The cheapest effective fix is to (1) replace `$` with the actual currency code in prompts, and (2) add `info.get("currency", "USD")` to the deep-fundamentals context. Full USD normalization is ideal but requires a reliable FX rate source (yfinance can fetch `currency=X` tickers, e.g., `GBPUSD=X`).

---

## Prioritized Change List

### P0 — Immediate, zero/low cost, highest impact

| # | Change | File:Line | What It Fixes |
|---|---|---|---|
| 1 | **Pass yfinance `info` dict into analyst for non-US regions** — replace the Brave-only `deep_fundamentals` in the `else` branch with a formatted summary of `info` fields | `whale_hunter.py:238-239` | Non-US regions get real fundamentals (ROE, margins, FCF, debt, sector, etc.) instead of a 1500-char Brave snippet |
| 2 | **Replace `$` with actual currency code in prompts** — use `info.get("currency", "USD")` instead of hardcoded `$` | `whale_hunter.py:204, 209, 287` | LLM stops treating GBP/CAD/AUD values as USD |
| 3 | **Add currency to deep-fundamentals context** for non-US | `whale_hunter.py:238-239` (in the enrichment string) | LLM knows the currency of all figures |

### P1 — Short-term, moderate effort

| # | Change | File:Line | What It Fixes |
|---|---|---|---|
| 4 | **Remove `.`-guards on Finnhub tools** — try-and-catch instead of hard-reject | `finance_tools.py:153, 193, 221`; `insider_feed.py:28-29` | Finnhub data flows for non-US when available (paid plan) or fails gracefully (free plan) |
| 5 | **Switch `get_insider_buys` to use `insider-transactions` endpoint** (global coverage) instead of `insider-sentiment` (US-only) | `insider_feed.py:28-37` | Insider data for UK/CA/AU stocks |
| 6 | **Pre-validate Brave tickers against yfinance** before merging into pool | `whale_hunter.py:91` (after `get_trending_tickers_from_brave`) | Dead/broken tickers don't enter the screener pool |
| 7 | **Expand `_SEED_POOLS`** to 50+ names per non-US region | `screener.py:22-46` | Pool exhaustion addressed; more candidates to pick from |

### P2 — Medium-term, higher effort

| # | Change | File:Line | What It Fixes |
|---|---|---|---|
| 8 | **Add `normalize_price` currency conversion to USD** using yfinance FX rates | `ticker_utils.py:136-140` | True cross-region comparable numbers |
| 9 | **Add Companies House API integration for UK filings** | New module or extend `sec_edgar.py` | UK stocks get ground-truth regulatory filings |
| 10 | **Add CEO.ca / SEDAR+ integration for Canada filings** | New module | Canadian stocks get ground-truth regulatory filings |
| 11 | **Automated seed pool refresh** — weekly job pulling fresh index constituents | `screener.py:22-46` | Pools stay current; no stale/delisted tickers |
| 12 | **Add `OTCQB` and exchange names to NOISE_WORDS** | `ticker_utils.py:14-52` | Reduce false-positive ticker extraction |

### P3 — Nice-to-have

| # | Change | File:Line | What It Fixes |
|---|---|---|---|
| 13 | **Pop 2-3 candidates per run** when pool has >10 unseen names | `whale_hunter.py:108-112` | Faster coverage of fresh universe |
| 14 | **Include `get_official_filing_link` output** in analyst prompt | `whale_hunter.py:215-221` + `global_router.py:12-35` | Analyst can reference official filings (currently dead code) |

---

## Research Sources

- Finnhub API documentation: finnhub.io/docs/api, finnhub.io/pricing — confirmed global coverage for basic financials (paid), insider transactions (all plans), and symbol format (`Exchange_Ticker.Exchange_Code`)
- Finnhub Python client: github.com/Finnhub-Stock-API/finnhub-python — confirms API structure and parameters
- Finnhub pricing page: confirmed free plan = US fundamentals only, All-In-One plan = Global fundamentals
- Finnhub insider-transactions docs: confirmed global coverage (US, UK, CA, AU, India, EU)
- Codebase: all files in `/tmp/primogreedy-src/src/` — direct trace with file:line citations
