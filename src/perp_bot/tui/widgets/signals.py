"""Signals widget — Z-score, RSI, ADX values with visual bars."""

from __future__ import annotations

from textual.widgets import Static


def _bar(value: float, lo: float, hi: float, width: int = 10) -> str:
    """Render a simple ASCII gauge bar."""
    clamped = max(lo, min(hi, value))
    filled = int((clamped - lo) / (hi - lo) * width)
    return "[" + "=" * filled + "-" * (width - filled) + "]"


class SignalsWidget(Static):
    """Displays the latest signal indicators for each symbol."""

    BORDER_TITLE = "SIGNALS"

    def update_state(self, state: dict | None) -> None:
        if state is None:
            self.update("[dim]Daemon offline[/]")
            return

        signals = state.get("latest_signals", {})
        if not signals:
            self.update("[dim]No signal data yet[/]")
            return

        lines = []
        for symbol, sig in signals.items():
            z = sig.get("zscore", 0)
            r = sig.get("rsi", 50)
            a = sig.get("adx", 0)
            signal_val = sig.get("signal", "none")

            # Colour-code z-score
            z_color = "green" if abs(z) < 1.0 else "yellow" if abs(z) < 2.0 else "red"

            # RSI colour
            r_color = "red" if r > 70 else "green" if r < 30 else "white"

            # ADX colour (low = range-bound = good for mean-reversion)
            a_color = "green" if a < 25 else "yellow" if a < 40 else "red"

            # Signal badge
            sig_badges = {
                "long": "[green bold]LONG[/]",
                "short": "[red bold]SHORT[/]",
                "close": "[yellow bold]CLOSE[/]",
                "none": "[dim]NONE[/]",
            }
            badge = sig_badges.get(signal_val, f"[dim]{signal_val}[/]")

            lines.append(f"[bold]{symbol}[/]")
            lines.append(
                f"  Z-Score: [{z_color}]{z:+.2f}[/]  {_bar(abs(z), 0, 4)}"
            )
            lines.append(
                f"  RSI:     [{r_color}]{r:.1f}[/]   {_bar(r, 0, 100)}"
            )
            lines.append(
                f"  ADX:     [{a_color}]{a:.1f}[/]   {_bar(a, 0, 60)}"
            )
            lines.append(f"  Signal:  {badge}")

        self.update("\n".join(lines))
