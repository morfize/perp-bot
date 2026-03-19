"""SQLite persistence for OHLCV candles and funding rates."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    open_time   INTEGER NOT NULL,  -- epoch ms
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    num_trades  INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe, open_time)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    symbol      TEXT    NOT NULL,
    time        INTEGER NOT NULL,  -- epoch ms
    rate        REAL    NOT NULL,
    premium     REAL    NOT NULL,
    PRIMARY KEY (symbol, time)
);

CREATE TABLE IF NOT EXISTS prediction_snapshots (
    source      TEXT    NOT NULL,  -- "polymarket" | "kalshi"
    market_id   TEXT    NOT NULL,
    market_slug TEXT    NOT NULL,
    category    TEXT    NOT NULL,  -- "war_risk" | "rate_change"
    timestamp   INTEGER NOT NULL,  -- epoch ms
    probability REAL    NOT NULL,  -- 0.0 to 1.0
    volume_24h  REAL,
    PRIMARY KEY (source, market_id, timestamp)
);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    side        TEXT    NOT NULL,  -- "long" | "short"
    entry_time  INTEGER NOT NULL,
    entry_price REAL    NOT NULL,
    exit_time   INTEGER,
    exit_price  REAL,
    size_usd    REAL    NOT NULL,
    pnl         REAL,
    exit_reason TEXT,
    is_paper    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)

    def insert_candles(self, rows: list[dict]) -> int:
        """Insert candles, skipping duplicates. Returns count inserted."""
        if not rows:
            return 0
        cur = self.conn.executemany(
            """INSERT OR IGNORE INTO candles
               (symbol, timeframe, open_time, open, high, low,
                close, volume, num_trades)
               VALUES (:symbol, :timeframe, :open_time, :open,
                :high, :low, :close, :volume, :num_trades)""",
            rows,
        )
        self.conn.commit()
        return cur.rowcount

    def insert_funding_rates(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        cur = self.conn.executemany(
            """INSERT OR IGNORE INTO funding_rates (symbol, time, rate, premium)
               VALUES (:symbol, :time, :rate, :premium)""",
            rows,
        )
        self.conn.commit()
        return cur.rowcount

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        start_time: int | None = None,
        limit: int = 5000,
        descending: bool = False,
    ) -> list[dict]:
        """Fetch candles ordered by open_time ascending."""
        query = "SELECT * FROM candles WHERE symbol = ? AND timeframe = ?"
        params: list = [symbol, timeframe]
        if start_time is not None:
            query += " AND open_time >= ?"
            params.append(start_time)
        order = "DESC" if descending else "ASC"
        query += f" ORDER BY open_time {order} LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(query, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        if descending:
            rows.reverse()
        return rows

    def get_latest_candle_time(self, symbol: str, timeframe: str) -> int | None:
        """Return the most recent open_time for incremental fetching."""
        cur = self.conn.execute(
            "SELECT MAX(open_time) FROM candles WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None

    def insert_trade(self, trade: dict) -> int:
        cur = self.conn.execute(
            """INSERT INTO trades (symbol, side, entry_time, entry_price, size_usd, is_paper)
               VALUES (:symbol, :side, :entry_time, :entry_price, :size_usd, :is_paper)""",
            trade,
        )
        self.conn.commit()
        return cur.lastrowid

    def close_trade(
        self, trade_id: int, exit_time: int,
        exit_price: float, pnl: float, reason: str,
    ) -> None:
        self.conn.execute(
            """UPDATE trades SET exit_time=?, exit_price=?, pnl=?, exit_reason=? WHERE id=?""",
            (exit_time, exit_price, pnl, reason, trade_id),
        )
        self.conn.commit()

    def get_open_trades(self, symbol: str | None = None) -> list[dict]:
        query = "SELECT * FROM trades WHERE exit_time IS NULL"
        params: list = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        cur = self.conn.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def insert_prediction_snapshots(self, rows: list[dict]) -> int:
        """Insert prediction snapshots, skipping duplicates."""
        if not rows:
            return 0
        cur = self.conn.executemany(
            """INSERT OR IGNORE INTO prediction_snapshots
               (source, market_id, market_slug, category,
                timestamp, probability, volume_24h)
               VALUES (:source, :market_id, :market_slug,
                :category, :timestamp, :probability,
                :volume_24h)""",
            rows,
        )
        self.conn.commit()
        return cur.rowcount

    def get_latest_predictions(self, source: str | None = None) -> list[dict]:
        """Get the most recent snapshot per market_slug."""
        query = """
            SELECT ps.* FROM prediction_snapshots ps
            INNER JOIN (
                SELECT market_slug, MAX(timestamp) AS max_ts
                FROM prediction_snapshots
                {where}
                GROUP BY market_slug
            ) latest ON ps.market_slug = latest.market_slug AND ps.timestamp = latest.max_ts
        """
        params: list = []
        if source:
            query = query.format(where="WHERE source = ?")
            params.append(source)
        else:
            query = query.format(where="")
        cur = self.conn.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_prediction_history(
        self, market_slug: str, start_time: int | None = None, limit: int = 1000
    ) -> list[dict]:
        """Get historical snapshots for a market, for backtesting."""
        query = "SELECT * FROM prediction_snapshots WHERE market_slug = ?"
        params: list = [market_slug]
        if start_time is not None:
            query += " AND timestamp >= ?"
            params.append(start_time)
        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_funding_rates(
        self, symbol: str, start_time: int, end_time: int
    ) -> list[dict]:
        """Fetch funding rates for a symbol within a time range."""
        cur = self.conn.execute(
            "SELECT * FROM funding_rates WHERE symbol = ?"
            " AND time >= ? AND time <= ? ORDER BY time ASC",
            (symbol, start_time, end_time),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_state(self, key: str) -> str | None:
        """Get a value from the bot_state table."""
        cur = self.conn.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        """Set a value in the bot_state table (upsert)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def get_closed_trades_in_range(
        self, start_ms: int, end_ms: int
    ) -> list[dict]:
        """Fetch trades closed within a time range."""
        cur = self.conn.execute(
            "SELECT * FROM trades WHERE exit_time IS NOT NULL"
            " AND exit_time >= ? AND exit_time <= ? ORDER BY exit_time ASC",
            (start_ms, end_ms),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_daily_pnl(self, day_start_ms: int) -> float:
        """Sum of realised P&L for trades closed since day_start_ms."""
        cur = self.conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE exit_time >= ?",
            (day_start_ms,),
        )
        return cur.fetchone()[0]

    def close(self) -> None:
        self.conn.close()
