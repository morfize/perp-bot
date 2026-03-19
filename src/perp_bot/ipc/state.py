"""Thread-safe daemon state container shared between trading loop and IPC server."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class DaemonState:
    """Volatile runtime state exposed to TUI clients via the socket."""

    mode: str = "paper"
    paused: bool = False
    start_time: float = field(default_factory=time.time)
    tick_count: int = 0
    last_tick_ms: int = 0
    mid_prices: dict[str, float] = field(default_factory=dict)
    latest_signals: dict[str, dict] = field(default_factory=dict)
    ws_healthy: bool = False
    prediction_regime: str = "NORMAL"
    risk_allowed: bool = True
    risk_reason: str = "all_checks_passed"
    cooldown_remaining_s: float = 0.0
    daily_pnl: float = 0.0
    slippage_stats: dict = field(
        default_factory=lambda: {"count": 0, "avg_pct": 0.0, "max_pct": 0.0},
    )

    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False,
    )

    def snapshot(self) -> dict:
        """Return a frozen dict copy of all state — safe for JSON serialisation."""
        with self._lock:
            return {
                "mode": self.mode,
                "paused": self.paused,
                "uptime_seconds": time.time() - self.start_time,
                "tick_count": self.tick_count,
                "last_tick_ms": self.last_tick_ms,
                "mid_prices": dict(self.mid_prices),
                "latest_signals": {k: dict(v) for k, v in self.latest_signals.items()},
                "ws_healthy": self.ws_healthy,
                "prediction_regime": self.prediction_regime,
                "risk_allowed": self.risk_allowed,
                "risk_reason": self.risk_reason,
                "cooldown_remaining_s": self.cooldown_remaining_s,
                "daily_pnl": self.daily_pnl,
                "slippage_stats": dict(self.slippage_stats),
            }

    def update(self, **kwargs) -> None:
        """Thread-safe bulk update of state fields."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key) and not key.startswith("_"):
                    setattr(self, key, value)
