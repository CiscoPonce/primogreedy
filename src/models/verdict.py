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
