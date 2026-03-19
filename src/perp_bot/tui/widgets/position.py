"""Position widget — current open position with live unrealised PnL."""

from __future__ import annotations

from textual.widgets import Static


class PositionWidget(Static):
    """Displays the current open position, or 'FLAT' if none."""

    BORDER_TITLE = "POSITION"

    def update_state(self, state: dict | None, open_trades: list[dict] | None = None) -> None:
        if state is None:
            self.update("[dim]Daemon offline[/]")
            return

        if not open_trades:
            self.update("[dim]FLAT — no open position[/]")
            return

        trade = open_trades[0]
        symbol = trade.get("symbol", "?")
        side = trade.get("side", "?")
        entry_price = trade.get("entry_price", 0)
        size_usd = trade.get("size_usd", 0)

        # Calculate unrealised PnL from current mid price
        mid_prices = state.get("mid_prices", {})
        current_price = mid_prices.get(symbol)
        upnl_str = "[dim]---[/]"
        pct_str = ""

        if current_price and entry_price > 0:
            if side == "long":
                upnl = (current_price - entry_price) / entry_price * size_usd
            else:
                upnl = (entry_price - current_price) / entry_price * size_usd
            pct = upnl / size_usd * 100 if size_usd else 0
            color = "green" if upnl >= 0 else "red"
            sign = "+" if upnl >= 0 else ""
            upnl_str = f"[{color}]{sign}${upnl:.2f}[/]"
            pct_str = f" [{color}]({sign}{pct:.2f}%)[/]"

        # Duration
        import time
        entry_time = trade.get("entry_time", 0)
        if entry_time:
            elapsed_s = int(time.time() * 1000 - entry_time) // 1000
            h, rem = divmod(elapsed_s, 3600)
            m, _ = divmod(rem, 60)
            duration = f"{h}h {m:02d}m" if h else f"{m}m"
        else:
            duration = "---"

        side_color = "green" if side == "long" else "red"
        lines = [
            f"[{side_color}]{side.upper()}[/] {symbol} @ {entry_price:.2f}",
            f"Size: ${size_usd:.0f}",
            f"uPnL: {upnl_str}{pct_str}",
            f"Duration: {duration}",
        ]
        self.update("\n".join(lines))
