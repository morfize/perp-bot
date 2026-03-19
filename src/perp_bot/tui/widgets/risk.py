"""Risk widget — entry check status, daily PnL, cooldown timer."""

from __future__ import annotations

from textual.widgets import Static


class RiskWidget(Static):
    """Displays risk management state."""

    BORDER_TITLE = "RISK"

    def update_state(self, state: dict | None, daily_pnl: float = 0.0) -> None:
        if state is None:
            self.update("[dim]Daemon offline[/]")
            return

        allowed = state.get("risk_allowed", False)
        reason = state.get("risk_reason", "unknown")
        cooldown = state.get("cooldown_remaining_s", 0.0)
        pnl = state.get("daily_pnl", daily_pnl)

        # Entry status
        if allowed:
            entry_str = "[green]ALLOWED[/]"
        else:
            entry_str = f"[red]BLOCKED[/] ({reason})"

        # Daily PnL
        pnl_color = "green" if pnl >= 0 else "red"
        sign = "+" if pnl >= 0 else ""
        pnl_str = f"[{pnl_color}]{sign}${pnl:.2f}[/]"

        # Cooldown
        if cooldown > 0:
            m, s = divmod(int(cooldown), 60)
            cd_str = f"[yellow]{m}m {s:02d}s[/]"
        else:
            cd_str = "[dim]--[/]"

        # Slippage
        slip = state.get("slippage_stats", {})
        if slip.get("count", 0) > 0:
            slip_str = (
                f"avg={slip['avg_pct']:.4f}% "
                f"max={slip['max_pct']:.4f}% "
                f"({slip['count']} fills)"
            )
        else:
            slip_str = "[dim]no data[/]"

        lines = [
            f"Entry:    {entry_str}",
            f"Daily PnL: {pnl_str}",
            f"Cooldown:  {cd_str}",
            f"Slippage:  {slip_str}",
        ]
        self.update("\n".join(lines))
