"""Main TUI application — attaches to a running daemon for live monitoring."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList
from textual.widgets.option_list import Option

from perp_bot.config import BotConfig
from perp_bot.ipc.client import DaemonClient
from perp_bot.ipc.protocol import get_socket_path
from perp_bot.tui.widgets.header import HeaderWidget
from perp_bot.tui.widgets.log import LogWidget
from perp_bot.tui.widgets.position import PositionWidget
from perp_bot.tui.widgets.risk import RiskWidget
from perp_bot.tui.widgets.signals import SignalsWidget
from perp_bot.tui.widgets.trades import TradesWidget


class EmergencyCloseScreen(ModalScreen[str | None]):
    """Modal symbol picker for emergency close."""

    DEFAULT_CSS = """
    EmergencyCloseScreen {
        align: center middle;
    }
    EmergencyCloseScreen > OptionList {
        width: 40;
        height: auto;
        max-height: 12;
        border: heavy red;
        background: $surface;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, symbols: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._symbols = symbols

    def compose(self) -> ComposeResult:
        options = [Option(f"CLOSE {s}", id=s) for s in self._symbols]
        yield OptionList(*options)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PerpBotApp(App):
    """Terminal UI for monitoring the perp-bot daemon."""

    CSS_PATH = "app.tcss"
    TITLE = "perp-bot"

    BINDINGS = [
        Binding("p", "pause", "Pause"),
        Binding("r", "resume", "Resume"),
        Binding("e", "emergency_close", "Emergency Close"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: BotConfig, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._db_path = Path(config.data.db_path)
        self._socket_path = get_socket_path(config.data.db_path)
        self._log_path = self._socket_path.parent / "perp-bot.log"
        self._client = DaemonClient(self._socket_path)
        self._last_state: dict | None = None

    def compose(self) -> ComposeResult:
        yield HeaderWidget()
        yield PositionWidget(id="position")
        yield SignalsWidget(id="signals")
        yield RiskWidget(id="risk")
        yield TradesWidget(id="trades")
        yield LogWidget(self._log_path, id="log")
        yield Footer()

    def on_mount(self) -> None:
        # Staggered polling timers
        self.set_interval(2.0, self._poll_daemon)
        self.set_interval(15.0, self._poll_trades)
        self.set_interval(3.0, self._poll_log)
        # Initial fetch
        self._poll_daemon()
        self._poll_trades()

    def _poll_daemon(self) -> None:
        """Fetch daemon state via socket and update volatile widgets."""
        self.run_worker(self._fetch_and_update_state, thread=True)

    def _fetch_and_update_state(self) -> None:
        state = self._client.get_state()
        self._last_state = state
        self.call_from_thread(self._apply_state, state)

    def _apply_state(self, state: dict | None) -> None:
        self.query_one(HeaderWidget).update_state(state)
        self.query_one(SignalsWidget).update_state(state)
        self.query_one(RiskWidget).update_state(state)

        # Position needs open trades from DB
        open_trades = self._query_open_trades()
        self.query_one(PositionWidget).update_state(state, open_trades)

    def _poll_trades(self) -> None:
        """Read recent closed trades from DB."""
        self.run_worker(self._fetch_and_update_trades, thread=True)

    def _fetch_and_update_trades(self) -> None:
        trades = self._query_recent_trades()
        self.call_from_thread(self._apply_trades, trades)

    def _apply_trades(self, trades: list[dict]) -> None:
        self.query_one(TradesWidget).update_trades(trades)

    def _poll_log(self) -> None:
        """Tail the log file for new entries."""
        self.query_one(LogWidget).poll_log()

    # ── DB queries (read-only) ────────────────────────

    def _get_ro_connection(self) -> sqlite3.Connection:
        """Open a read-only SQLite connection."""
        uri = f"file:{self._db_path}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=2)

    def _query_open_trades(self) -> list[dict]:
        try:
            conn = self._get_ro_connection()
            cur = conn.execute(
                "SELECT * FROM trades WHERE exit_time IS NULL",
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []

    def _query_recent_trades(self) -> list[dict]:
        try:
            conn = self._get_ro_connection()
            now_ms = int(time.time() * 1000)
            week_ms = 7 * 24 * 3600 * 1000
            cur = conn.execute(
                "SELECT * FROM trades WHERE exit_time IS NOT NULL"
                " AND exit_time >= ? ORDER BY exit_time DESC LIMIT 10",
                (now_ms - week_ms,),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            conn.close()
            return list(reversed(rows))
        except Exception:
            return []

    # ── Actions ───────────────────────────────────────

    def action_pause(self) -> None:
        result = self._client.pause()
        if result and result.get("ok"):
            self.notify("Daemon PAUSED", severity="warning")
        else:
            self.notify("Failed to pause (daemon offline?)", severity="error")

    def action_resume(self) -> None:
        result = self._client.resume()
        if result and result.get("ok"):
            self.notify("Daemon RESUMED", severity="information")
        else:
            self.notify("Failed to resume (daemon offline?)", severity="error")

    def action_emergency_close(self) -> None:
        """Emergency close — show symbol picker, then close selected."""
        symbols = self._config.trading.symbols
        if not symbols:
            self.notify("No symbols configured", severity="error")
            return
        self.push_screen(
            EmergencyCloseScreen(symbols),
            callback=self._on_emergency_symbol_selected,
        )

    def _on_emergency_symbol_selected(self, symbol: str | None) -> None:
        if symbol is None:
            return
        result = self._client.emergency_close(symbol)
        if result and result.get("ok"):
            closed = result.get("closed", 0)
            self.notify(
                f"Emergency close: {closed} position(s) for {symbol}",
                severity="warning",
            )
        else:
            err = result.get("error", "unknown") if result else "no response"
            self.notify(f"Emergency close failed: {err}", severity="error")
