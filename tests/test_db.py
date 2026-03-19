"""Tests for database access helpers."""

from perp_bot.data.db import Database


def test_get_candles_descending_returns_latest_window_in_ascending_order():
    db = Database(":memory:")
    db.insert_candles([
        {
            "symbol": "ETH",
            "timeframe": "15m",
            "open_time": open_time,
            "open": float(open_time),
            "high": float(open_time) + 1,
            "low": float(open_time) - 1,
            "close": float(open_time),
            "volume": 100.0,
            "num_trades": 10,
        }
        for open_time in (1_000, 2_000, 3_000, 4_000)
    ])

    candles = db.get_candles("ETH", "15m", limit=2, descending=True)

    assert [c["open_time"] for c in candles] == [3_000, 4_000]
    db.close()
