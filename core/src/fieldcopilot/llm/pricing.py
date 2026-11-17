"""Model prices, in USD per million tokens.

From Anthropic's published table (November 2026). Cache writes are priced here at
1.25x the input rate (the standard 5-minute cache); cache reads at the published read
price. Update this file when prices change; tests pin the arithmetic, not the prices.
"""
from __future__ import annotations

from dataclasses import dataclass

from fieldcopilot.llm.types import Usage


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float

    @property
    def cache_write(self) -> float:
        return self.input * 1.25


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.10),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),
    # The fake and recorded providers report these names so cost plumbing is exercised
    # without pretending to be a real model's bill.
    "fake-strong": Price(4.00, 20.00, 0.20),
    "fake-fast": Price(0.10, 0.50, 0.01),
}


def cost_usd(model: str, usage: Usage) -> float:
    # Bedrock IDs ("anthropic.claude-opus-5-5") are priced at the same list rates here; AWS
    # bills them, and regional endpoints can cost more, so check the AWS price list for a quote.
    price = PRICES.get(model) or PRICES.get(model.removeprefix("anthropic."))
    if price is None:
        return 0.0
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return round(total / 1_000_000, 6)
