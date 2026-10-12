import re
from typing import Literal

from pydantic import BaseModel, Field

_HEADER_RE = re.compile(r"^#{1,4}\s+.*$", re.MULTILINE)


def _strip_headers(text: str) -> str:
    """Remove any markdown headers the LLM embedded in a field value."""
    return _HEADER_RE.sub("", text).strip()


class InvestmentVerdict(BaseModel):
    """Structured analyst verdict returned by the Senior Broker LLM."""

    quantitative_base: str = Field(
        description="Price vs calculated valuation, margin of safety math"
    )
    lynch_pitch: str = Field(
        description="What insiders are doing + the one catalyst"
    )
    munger_invert: str = Field(
        description="How an investor could lose money, the bear evidence"
    )
    verdict: Literal["STRONG BUY", "BUY", "WATCH", "AVOID"]
    bottom_line: str = Field(
        description="One-sentence final summary"
    )
    position_size: float = Field(
        default=0.0,
        description="Kelly-derived position size as % of portfolio (set post-LLM)",
    )
    kelly_win_rate: float = Field(
        default=0.0,
        description="Historical win rate used for Kelly calc (set post-LLM)",
    )
    kelly_total_trades: int = Field(
        default=0,
        description="Number of trades used for Kelly calc (set post-LLM)",
    )

    # --- 3-layer framework fields (proposal.md) — all optional, filled by the
    # --- Senior Broker when data supports them. No universal thresholds. -------
    business_summary: str | None = Field(
        default=None, description="One paragraph: business model + sector"
    )
    financial_evolution: str | None = Field(
        default=None, description="Revenue / EPS / EBITDA trend over recent periods"
    )
    cash_flow: str | None = Field(
        default=None, description="Operating cash flow vs net income, FCF sign & trajectory"
    )
    debt_liquidity: str | None = Field(
        default=None, description="Total debt, D/E, current ratio, refinancing risk"
    )
    dilution: str | None = Field(
        default=None, description="Share-count trend, shelf/ATM risk, recent raises"
    )
    valuation_method: str | None = Field(
        default=None, description="Method(s) used (multiples, DCF, asset-based) — never one formula alone"
    )
    valuation_bear: str | None = Field(
        default=None, description="Bear scenario value + the assumption driving it"
    )
    valuation_base: str | None = Field(
        default=None, description="Base scenario value + the assumption driving it"
    )
    valuation_bull: str | None = Field(
        default=None, description="Bull scenario value + the assumption driving it"
    )
    valuation_range_low: float | None = Field(
        default=None, description="Low end of the fair-value range (number only)"
    )
    valuation_range_high: float | None = Field(
        default=None, description="High end of the fair-value range (number only)"
    )
    margin_of_safety: str | None = Field(
        default=None, description="Current price vs base-scenario value as a %"
    )
    risks: str | None = Field(
        default=None, description="Top 3-5 specific risks, each with why"
    )
    invalidation_facts: str | None = Field(
        default=None, description="Exact facts whose publication would break the thesis"
    )
    macro_factors: str | None = Field(
        default=None, description="Rate / inflation / sector-cycle links that matter"
    )
    portfolio_fit: str | None = Field(
        default=None, description="Position size vs cap, sector overlap, liquidity"
    )
    sources: list | None = Field(
        default=None, description="[{type,url,date,period}] cited per quantitative claim; published vs estimates vs inferences separated"
    )
    data_quality: dict | None = Field(
        default=None, description="{facts, estimates, inferences, unverified, pending}"
    )
    proposed_action: str | None = Field(
        default=None, description="INITIATE|MAINTAIN|INCREASE|REDUCE|CLOSE|DISCARD|PENDING"
    )
    review_trigger: str | None = Field(
        default=None, description="Market/company event that triggers the next review"
    )
    next_review_at: str | None = Field(
        default=None, description="Concrete next-review date (YYYY-MM-DD)"
    )

    def to_report(self) -> str:
        """Render as the markdown report format the pipeline expects."""
        qb = _strip_headers(self.quantitative_base)
        lp = _strip_headers(self.lynch_pitch)
        mi = _strip_headers(self.munger_invert)

        report = (
            f"### THE QUANTITATIVE BASE (Graham / Asset Play)\n"
            f"{qb}\n\n"
            f"### THE LYNCH PITCH (Why I would own this)\n"
            f"{lp}\n\n"
            f"### THE MUNGER INVERT (How I could lose money)\n"
            f"{mi}\n\n"
            f"### FINAL VERDICT\n"
            f"**{self.verdict}** — {self.bottom_line}"
        )

        if self.position_size > 0:
            report += (
                f"\n\n### POSITION SIZING (Kelly Criterion)\n"
                f"**Recommended allocation: {self.position_size:.1f}% of portfolio**\n"
                f"Based on historical win rate of {self.kelly_win_rate * 100:.0f}% "
                f"across {self.kelly_total_trades} trades."
            )

        return report
