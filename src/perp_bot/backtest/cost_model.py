"""Cost models — fees, slippage, and funding for realistic backtest simulation."""

from __future__ import annotations

import random


class FeeModel:
    """Hyperliquid fee model: maker for limit orders, taker for market/stop orders."""

    def __init__(self, maker_rate: float = 0.00015, taker_rate: float = 0.00045) -> None:
        self.maker_rate = maker_rate
        self.taker_rate = taker_rate

    def entry_fee(self, notional: float) -> float:
        """Entry uses maker rate (limit order)."""
        return notional * self.maker_rate

    def exit_fee(self, notional: float, is_stop_loss: bool = False) -> float:
        """Exit uses taker rate for stop-loss (market order), maker otherwise."""
        rate = self.taker_rate if is_stop_loss else self.maker_rate
        return notional * rate


class SlippageModel:
    """Simulates random slippage that always worsens the fill price."""

    def __init__(
        self,
        min_pct: float = 0.0001,
        max_pct: float = 0.0005,
        seed: int = 42,
    ) -> None:
        self.min_pct = min_pct
        self.max_pct = max_pct
        self.rng = random.Random(seed)

    def apply(self, price: float, side: str, is_entry: bool) -> float:
        """Apply slippage to a price. Always worsens the fill.

        Long entry / short exit → price goes up (pay more / receive less).
        Short entry / long exit → price goes down (receive less / pay more).
        """
        slip_pct = self.rng.uniform(self.min_pct, self.max_pct)

        # Determine if slippage moves price up or down
        if (side == "long" and is_entry) or (side == "short" and not is_entry):
            return price * (1 + slip_pct)
        else:
            return price * (1 - slip_pct)


_HOUR_MS = 3_600_000


class FundingModel:
    """Accumulates Hyperliquid hourly funding costs over a position's lifetime.

    Hyperliquid settles funding every hour (1/8 of the 8h rate).
    Long pays positive rate; short receives it (and vice versa for negative).
    """

    def __init__(self, funding_rates: list[dict]) -> None:
        # Build {hour_boundary_ms: hourly_rate}
        self._rates: dict[int, float] = {}
        for fr in funding_rates:
            ts = fr["time"]
            # Snap to hour boundary
            hour_ms = (ts // _HOUR_MS) * _HOUR_MS
            self._rates[hour_ms] = fr["rate"]

    def cost_between(
        self, side: str, notional: float, start_ms: int, end_ms: int
    ) -> float:
        """Compute total funding cost for holding a position.

        Positive return = cost to the trader (negative P&L impact).
        """
        total = 0.0
        # Iterate over each hourly boundary between start and end
        first_hour = ((start_ms // _HOUR_MS) + 1) * _HOUR_MS
        hour = first_hour
        while hour <= end_ms:
            rate = self._rates.get(hour, 0.0)
            if side == "long":
                total += rate * notional
            else:
                total -= rate * notional
            hour += _HOUR_MS
        return total
