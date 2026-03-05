from typing import Literal

from pydantic import BaseModel, Field


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

    def to_report(self) -> str:
        """Render as the markdown report format the pipeline expects."""
        return (
            f"### THE QUANTITATIVE BASE (Graham / Asset Play)\n"
            f"{self.quantitative_base}\n\n"
            f"### THE LYNCH PITCH (Why I would own this)\n"
            f"{self.lynch_pitch}\n\n"
            f"### THE MUNGER INVERT (How I could lose money)\n"
            f"{self.munger_invert}\n\n"
            f"### FINAL VERDICT\n"
            f"**{self.verdict}** — {self.bottom_line}"
        )
