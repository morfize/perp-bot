"""Tests for TUI widgets — rendering and state updates."""

from __future__ import annotations

from perp_bot.tui.widgets.header import HeaderWidget, _fmt_uptime
from perp_bot.tui.widgets.signals import _bar


class TestHeaderFormatting:
    def test_fmt_uptime_minutes(self):
        assert _fmt_uptime(125) == "2m 05s"

    def test_fmt_uptime_hours(self):
        assert _fmt_uptime(3661) == "1h 01m"

    def test_fmt_uptime_zero(self):
        assert _fmt_uptime(0) == "0m 00s"


class TestSignalBar:
    def test_bar_full(self):
        result = _bar(10.0, 0.0, 10.0, width=5)
        assert result == "[=====]"

    def test_bar_empty(self):
        result = _bar(0.0, 0.0, 10.0, width=5)
        assert result == "[-----]"

    def test_bar_half(self):
        result = _bar(5.0, 0.0, 10.0, width=10)
        # [ + 10 chars + ] = 12 total
        assert len(result) == 12
        assert result.startswith("[=====")
        assert result.endswith("]")

    def test_bar_clamped_above(self):
        result = _bar(999.0, 0.0, 10.0, width=5)
        assert result == "[=====]"

    def test_bar_clamped_below(self):
        result = _bar(-5.0, 0.0, 10.0, width=5)
        assert result == "[-----]"


class TestWidgetStateUpdates:
    """Test that widgets handle None state gracefully."""

    def test_header_offline_state(self):
        w = HeaderWidget()
        # Should not raise when called before mount
        w.update_state(None)

    def test_header_with_state(self):
        w = HeaderWidget()
        state = {
            "mode": "paper",
            "paused": False,
            "ws_healthy": True,
            "prediction_regime": "NORMAL",
            "mid_prices": {"ETH": 3245.67},
            "uptime_seconds": 3600,
        }
        w.update_state(state)
