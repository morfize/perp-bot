"""Header widget — mode badge, lead price, WS health, regime, uptime."""

from __future__ import annotations

from textual.widgets import Static


def _fmt_uptime(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


class HeaderWidget(Static):
    """Top bar showing daemon overview."""

    DEFAULT_CSS = """
    HeaderWidget {
        dock: top;
        height: 1;
        background: $surface;
        color: $text;
        content-align: center middle;
    }
    """

    def update_state(self, state: dict | None) -> None:
        if state is None:
            self.update("[bold red] DAEMON OFFLINE [/]")
            return

        mode = state.get("mode", "?")
        mode_style = "green" if mode == "paper" else "red"
        if state.get("paused"):
            mode_style = "yellow"
            mode = f"{mode} PAUSED"

        ws = "[green]OK[/]" if state.get("ws_healthy") else "[red]STALE[/]"

        regime = state.get("prediction_regime", "NORMAL")
        regime_colors = {
            "NORMAL": "green", "HIGH_RISK": "yellow",
            "DOVISH_SHIFT": "yellow", "HAWKISH_SHIFT": "yellow",
            "CRISIS": "red",
        }
        rc = regime_colors.get(regime, "white")

        # Lead symbol price
        mid_prices = state.get("mid_prices", {})
        price_parts = []
        for sym, px in mid_prices.items():
            price_parts.append(f"{sym} ${px:,.2f}")
        price_str = " | ".join(price_parts) if price_parts else "---"

        uptime = _fmt_uptime(state.get("uptime_seconds", 0))

        self.update(
            f"  PERP-BOT  [{mode_style}]{mode}[/] | {price_str}"
            f" | WS:{ws} | [{rc}]{regime}[/] | {uptime}"
        )
