"""In-memory backtest executor — no database, no real orders."""

from __future__ import annotations

from perp_bot.backtest.cost_model import FeeModel, FundingModel, SlippageModel
from perp_bot.backtest.results import TradeRecord


class BacktestExecutor:
    """Executes trades in-memory with slippage and fee application."""

    def __init__(
        self,
        fee_model: FeeModel,
        slippage_model: SlippageModel,
        funding_model: FundingModel,
    ) -> None:
        self.fee_model = fee_model
        self.slippage_model = slippage_model
        self.funding_model = funding_model
        self.trades: list[TradeRecord] = []
        self.open_trade: _OpenTrade | None = None
        self._next_id = 1

    def open_position(
        self, symbol: str, side: str, size_usd: float, price: float, time_ms: int
    ) -> None:
        """Open a new position with slippage and entry fee."""
        filled_price = self.slippage_model.apply(price, side, is_entry=True)
        entry_fee = self.fee_model.entry_fee(size_usd)
        self.open_trade = _OpenTrade(
            id=self._next_id,
            symbol=symbol,
            side=side,
            size_usd=size_usd,
            entry_time_ms=time_ms,
            entry_price=filled_price,
            raw_entry_price=price,
            entry_fee=entry_fee,
        )
        self._next_id += 1

    def close_position(
        self, price: float, time_ms: int, reason: str, is_stop_loss: bool = False
    ) -> TradeRecord:
        """Close the open position and return the completed TradeRecord."""
        t = self.open_trade
        assert t is not None, "No open position to close"

        filled_price = self.slippage_model.apply(price, t.side, is_entry=False)
        exit_fee = self.fee_model.exit_fee(t.size_usd, is_stop_loss=is_stop_loss)

        # Raw P&L from price movement
        if t.side == "long":
            pnl = (filled_price - t.entry_price) / t.entry_price * t.size_usd
        else:
            pnl = (t.entry_price - filled_price) / t.entry_price * t.size_usd

        # Funding cost over holding period
        funding_cost = self.funding_model.cost_between(
            t.side, t.size_usd, t.entry_time_ms, time_ms
        )

        net_pnl = pnl - t.entry_fee - exit_fee - funding_cost

        record = TradeRecord(
            id=t.id,
            symbol=t.symbol,
            side=t.side,
            entry_time_ms=t.entry_time_ms,
            exit_time_ms=time_ms,
            entry_price=t.entry_price,
            exit_price=filled_price,
            raw_entry_price=t.raw_entry_price,
            raw_exit_price=price,
            size_usd=t.size_usd,
            pnl=pnl,
            entry_fee=t.entry_fee,
            exit_fee=exit_fee,
            funding_cost=funding_cost,
            net_pnl=net_pnl,
            exit_reason=reason,
        )
        self.trades.append(record)
        self.open_trade = None
        return record

    @property
    def has_position(self) -> bool:
        return self.open_trade is not None


class _OpenTrade:
    """Lightweight container for an in-flight trade."""

    __slots__ = (
        "id", "symbol", "side", "size_usd",
        "entry_time_ms", "entry_price", "raw_entry_price", "entry_fee",
    )

    def __init__(
        self, id: int, symbol: str, side: str, size_usd: float,
        entry_time_ms: int, entry_price: float, raw_entry_price: float,
        entry_fee: float,
    ) -> None:
        self.id = id
        self.symbol = symbol
        self.side = side
        self.size_usd = size_usd
        self.entry_time_ms = entry_time_ms
        self.entry_price = entry_price
        self.raw_entry_price = raw_entry_price
        self.entry_fee = entry_fee
