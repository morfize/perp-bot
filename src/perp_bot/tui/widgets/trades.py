"""Trades widget — recent closed trades in a DataTable."""

from __future__ import annotations

from textual.widgets import DataTable


class TradesWidget(DataTable):
    """Shows the last N closed trades."""

    BORDER_TITLE = "RECENT TRADES"

    def on_mount(self) -> None:
        self.add_columns("ID", "Symbol", "Side", "PnL", "Reason")
        self.cursor_type = "none"

    def update_trades(self, trades: list[dict]) -> None:
        self.clear()
        for t in trades[-10:]:
            pnl = t.get("pnl", 0) or 0
            pnl_val = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            self.add_row(
                str(t.get("id", "?")),
                t.get("symbol", "?"),
                t.get("side", "?").upper(),
                pnl_val,
                t.get("exit_reason", "?"),
            )
