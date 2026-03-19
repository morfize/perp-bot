"""Periodic health check — heartbeat with system status."""

from __future__ import annotations

import logging
import time

from perp_bot.data.db import Database
from perp_bot.data.ws_client import WsClient
from perp_bot.infra.alerts import send_discord_alert, send_telegram_alert

logger = logging.getLogger(__name__)


class HealthChecker:
    """Sends periodic heartbeat messages with bot status."""

    def __init__(
        self,
        config,
        db: Database,
        ws_client: WsClient | None = None,
        executor=None,
        interval_minutes: int = 30,
    ) -> None:
        self._config = config
        self._db = db
        self._ws_client = ws_client
        self._executor = executor
        self._interval_ms = interval_minutes * 60 * 1000
        self._last_heartbeat_ms: int = 0

    def tick(self, current_regime: str = "NORMAL") -> None:
        """Call on each main loop iteration. Sends heartbeat if interval has elapsed."""
        now_ms = int(time.time() * 1000)
        if now_ms - self._last_heartbeat_ms < self._interval_ms:
            return

        self._last_heartbeat_ms = now_ms
        status = self._build_status(current_regime)

        logger.info("HEARTBEAT: %s", status)
        if self._config.discord_webhook_url:
            send_discord_alert(self._config.discord_webhook_url, status)
        if self._config.telegram_bot_token and self._config.telegram_chat_id:
            send_telegram_alert(
                self._config.telegram_bot_token,
                self._config.telegram_chat_id,
                status,
            )

    def _build_status(self, regime: str) -> str:
        open_trades = self._db.get_open_trades()
        ws_ok = self._ws_client.is_healthy() if self._ws_client else "N/A"

        parts = [
            f"mode={self._config.mode}",
            f"regime={regime}",
            f"open_positions={len(open_trades)}",
            f"ws_healthy={ws_ok}",
        ]

        if open_trades:
            t = open_trades[0]
            parts.append(
                f"pos={t['side']} {t['symbol']}"
                f" @ {t['entry_price']:.2f}"
            )

        # Slippage stats from live executor
        if self._executor and hasattr(self._executor, "get_slippage_stats"):
            stats = self._executor.get_slippage_stats()
            if stats["count"] > 0:
                parts.append(
                    f"slip_avg={stats['avg_pct']:.4f}%"
                    f" max={stats['max_pct']:.4f}%"
                )

        return " | ".join(parts)
