"""Live executor — places real orders on Hyperliquid via the Exchange API."""

from __future__ import annotations

import logging
import time
from typing import Any

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from perp_bot.config import BotConfig
from perp_bot.data.db import Database
from perp_bot.execution.executor import Executor

logger = logging.getLogger(__name__)

# Price rounding to avoid rejection (Hyperliquid requires specific tick sizes)
_SIGNIFICANT_FIGURES = 5


def _round_price(price: float) -> float:
    """Round price to 5 significant figures for Hyperliquid compatibility."""
    if price == 0:
        return 0.0
    return float(f"{price:.{_SIGNIFICANT_FIGURES}g}")


def _round_size(size: float, sz_decimals: int) -> float:
    """Round size to the asset's allowed decimal places."""
    return round(size, sz_decimals)


class LiveExecutor(Executor):
    """Places real orders on Hyperliquid with limit-first, taker-fallback strategy.

    Features:
    - GTC limit entry with taker fallback after timeout
    - Server-side stop-loss attached atomically to entry orders
    - Position tracking via DB for crash recovery
    - Slippage-bounded market orders (IOC with price limit)
    """

    def __init__(
        self,
        config: BotConfig,
        db: Database,
        base_url: str | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self._base_url = base_url or constants.MAINNET_API_URL

        # Initialise exchange connection
        wallet = eth_account.Account.from_key(config.hl_private_key)
        self._exchange = Exchange(wallet, base_url=self._base_url)
        self._info: Info = self._exchange.info
        self._wallet_address = (
            config.hl_wallet_address or wallet.address
        )

        # Cache asset metadata for size rounding
        self._sz_decimals: dict[str, int] = {}
        self._sl_failed: bool = False  # Set True when SL placement fails
        self._slippage_history: list[float] = []  # Track fill vs expected
        self._load_asset_meta()

        logger.info(
            "LiveExecutor initialised — wallet=%s, url=%s",
            self._wallet_address[:10], self._base_url,
        )

    def _load_asset_meta(self) -> None:
        """Load size decimal precision for each asset."""
        try:
            meta = self._info.meta_and_asset_ctxs()
            if meta and len(meta) >= 1:
                for asset in meta[0].get("universe", []):
                    name = asset["name"]
                    self._sz_decimals[name] = asset.get("szDecimals", 3)
        except Exception:
            logger.warning("Failed to load asset metadata, using default precision")

    def _get_sz_decimals(self, symbol: str) -> int:
        return self._sz_decimals.get(symbol, 3)

    def _get_mid_price(self, symbol: str) -> float:
        """Fetch current mid price via REST."""
        l2 = self._info.l2_snapshot(symbol)
        best_bid = float(l2["levels"][0][0]["px"])
        best_ask = float(l2["levels"][1][0]["px"])
        return (best_bid + best_ask) / 2

    # ── Entry ────────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        side: str,
        size_usd: float,
        price: float,
    ) -> int | None:
        """Open a position with limit-first, taker-fallback strategy.

        Also places a server-side stop-loss for redundancy.
        Returns trade_id from DB, or None on failure.
        Sets self._sl_failed = True if the position has no server-side SL.
        """
        self._sl_failed = False
        is_buy = side == "long"
        sz_decimals = self._get_sz_decimals(symbol)
        size_base = _round_size(size_usd / price, sz_decimals)

        if size_base <= 0:
            logger.error("Computed size_base <= 0 for %s", symbol)
            return None

        # Compute stop-loss price
        sl_pct = self.config.risk.max_loss_per_trade_pct
        if is_buy:
            sl_price = _round_price(price * (1 - sl_pct))
        else:
            sl_price = _round_price(price * (1 + sl_pct))

        limit_price = _round_price(price)

        # Try limit order with server-side SL attached
        fill_price = self._try_limit_with_sl(
            symbol, is_buy, size_base, limit_price, sl_price,
        )

        if fill_price is None:
            # Fallback: taker (IOC) order
            logger.info("Limit unfilled after timeout, falling back to taker")
            fill_price = self._try_taker(symbol, is_buy, size_base, price)

            if fill_price is None:
                logger.error("Taker order also failed for %s %s", side, symbol)
                return None

            # Place server-side SL separately since the bulk entry failed
            sl_ok = self._place_server_side_sl(
                symbol, not is_buy, size_base, sl_price,
            )
            if not sl_ok:
                self._sl_failed = True
                logger.critical(
                    "UNHEDGED POSITION: %s %s has no server-side SL!",
                    side, symbol,
                )

        # Track slippage: actual fill vs requested price
        self._record_slippage(price, fill_price, is_buy)

        # Record in DB
        now = int(time.time() * 1000)
        trade_id = self.db.insert_trade({
            "symbol": symbol,
            "side": side,
            "entry_time": now,
            "entry_price": fill_price,
            "size_usd": fill_price * size_base,
            "is_paper": 0,
        })
        logger.info(
            "[LIVE] Opened %s %s @ %.2f, size=%.4f %s ($%.2f) (trade #%d)",
            side, symbol, fill_price, size_base, symbol,
            fill_price * size_base, trade_id,
        )
        return trade_id

    def _try_limit_with_sl(
        self,
        symbol: str,
        is_buy: bool,
        size_base: float,
        limit_price: float,
        sl_price: float,
    ) -> float | None:
        """Place GTC limit entry + server-side SL atomically.

        Waits up to taker_fallback_seconds for fill.
        Returns fill price, or None if unfilled (cancels the order).
        """
        orders = [
            {
                "coin": symbol,
                "is_buy": is_buy,
                "sz": size_base,
                "limit_px": limit_price,
                "order_type": {"limit": {"tif": "Gtc"}},
                "reduce_only": False,
            },
            {
                "coin": symbol,
                "is_buy": not is_buy,
                "sz": size_base,
                "limit_px": sl_price,
                "order_type": {
                    "trigger": {
                        "triggerPx": sl_price,
                        "isMarket": True,
                        "tpsl": "sl",
                    }
                },
                "reduce_only": True,
            },
        ]

        try:
            result = self._exchange.bulk_orders(orders, grouping="normalTpsl")
            logger.debug("bulk_orders result: %s", result)
        except Exception:
            logger.exception("bulk_orders failed")
            return None

        # Check if entry order was immediately filled
        immediate_fill = self._extract_fill_price(result)
        if immediate_fill is not None:
            logger.info("Entry order filled immediately at %.2f", immediate_fill)
            return immediate_fill

        # Order is resting — extract OID and wait for fill
        entry_oid = self._extract_oid(result, index=0)
        if entry_oid is None:
            logger.warning("Could not extract entry OID from result")
            return None

        # Wait for fill
        timeout = self.config.execution.taker_fallback_seconds
        fill_price = self._wait_for_fill(symbol, entry_oid, timeout)

        if fill_price is None:
            # Cancel the unfilled limit order (SL may remain — that's fine)
            try:
                self._exchange.cancel(symbol, entry_oid)
                logger.info("Cancelled unfilled limit order %d", entry_oid)
            except Exception:
                logger.warning("Failed to cancel order %d", entry_oid)
            # Also cancel the SL since the entry didn't fill
            self._cancel_open_trigger_orders(symbol)

        return fill_price

    def _try_taker(
        self,
        symbol: str,
        is_buy: bool,
        size_base: float,
        reference_price: float,
    ) -> float | None:
        """Place an IOC (taker) order with bounded slippage.

        Returns fill price, or None on failure.
        """
        # Allow 0.5% slippage from reference price
        slippage = 0.005
        if is_buy:
            limit_px = _round_price(reference_price * (1 + slippage))
        else:
            limit_px = _round_price(reference_price * (1 - slippage))

        try:
            result = self._exchange.order(
                symbol,
                is_buy=is_buy,
                sz=size_base,
                limit_px=limit_px,
                order_type={"limit": {"tif": "Ioc"}},
            )
            logger.debug("IOC order result: %s", result)
        except Exception:
            logger.exception("IOC order failed")
            return None

        return self._extract_fill_price(result)

    def _place_server_side_sl(
        self,
        symbol: str,
        is_buy: bool,
        size_base: float,
        sl_price: float,
    ) -> bool:
        """Place a standalone server-side stop-loss trigger order.

        Retries once on failure. Returns True on success.
        """
        for attempt in range(2):
            try:
                self._exchange.order(
                    symbol,
                    is_buy=is_buy,
                    sz=size_base,
                    limit_px=sl_price,
                    order_type={
                        "trigger": {
                            "triggerPx": sl_price,
                            "isMarket": True,
                            "tpsl": "sl",
                        }
                    },
                    reduce_only=True,
                )
                logger.info("Placed server-side SL at %.2f for %s", sl_price, symbol)
                return True
            except Exception:
                if attempt == 0:
                    logger.warning("SL placement failed, retrying once...")
                    time.sleep(1)
                else:
                    logger.exception("CRITICAL: Server-side SL placement failed after retry")
        return False

    # ── Exit ─────────────────────────────────────────────

    def close_position(
        self,
        trade_id: int,
        symbol: str,
        price: float,
        pnl: float,
        reason: str,
    ) -> bool:
        """Close a position using market_close, then update DB.

        Returns True on success.
        """
        # Cancel any remaining trigger orders (server-side SL)
        self._cancel_open_trigger_orders(symbol)

        # Use market close for immediate execution
        try:
            result = self._exchange.market_close(
                symbol, slippage=0.01,  # 1% slippage tolerance
            )
            logger.debug("market_close result: %s", result)
        except Exception:
            logger.exception("market_close failed for %s", symbol)
            return False

        fill_price = self._extract_fill_price(result)
        if fill_price is None:
            # Fallback: use the provided price estimate
            fill_price = price
            logger.warning(
                "Could not extract fill price from close, using estimate %.2f",
                price,
            )

        # Recalculate actual PnL from fill
        trade = self._get_trade(trade_id)
        if trade:
            actual_pnl = self._calc_pnl(trade, fill_price)
        else:
            actual_pnl = pnl

        now = int(time.time() * 1000)
        self.db.close_trade(trade_id, now, fill_price, actual_pnl, reason)
        logger.info(
            "[LIVE] Closed trade #%d %s @ %.2f, pnl=%.2f, reason=%s",
            trade_id, symbol, fill_price, actual_pnl, reason,
        )
        return True

    # ── Helpers ──────────────────────────────────────────

    def _wait_for_fill(
        self, symbol: str, oid: int, timeout_seconds: int
    ) -> float | None:
        """Poll order status until filled or timeout. Returns fill price."""
        deadline = time.time() + timeout_seconds
        poll_interval = 2.0  # seconds

        while time.time() < deadline:
            try:
                status = self._info.query_order_by_oid(
                    self._wallet_address, oid
                )
                order = status.get("order", {})
                order_status = order.get("status", "")

                if order_status == "filled":
                    return self._get_fill_price(symbol, oid)

                if order_status in ("canceled", "rejected"):
                    logger.warning("Order %d was %s", oid, order_status)
                    return None

            except Exception:
                logger.debug("Error polling order %d status", oid)

            time.sleep(poll_interval)

        return None

    def _extract_oid(self, result: Any, index: int = 0) -> int | None:
        """Extract order ID from API response."""
        try:
            statuses = result.get("response", {}).get("data", {}).get(
                "statuses", []
            )
            if index < len(statuses):
                status = statuses[index]
                if "resting" in status:
                    return status["resting"]["oid"]
                if "filled" in status:
                    return status["filled"]["oid"]
        except (AttributeError, KeyError, TypeError):
            pass
        return None

    def _extract_fill_price(self, result: Any) -> float | None:
        """Extract fill price from an order result."""
        try:
            statuses = result.get("response", {}).get("data", {}).get(
                "statuses", []
            )
            if statuses:
                status = statuses[0]
                if "filled" in status:
                    return float(status["filled"]["avgPx"])
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
        return None

    def _get_fill_price(self, symbol: str, oid: int) -> float | None:
        """Get the actual average fill price for an order using user_fills_by_time.

        This queries the fills API rather than reading limitPx, which would
        give the requested price instead of the actual execution price.
        """
        try:
            # Query recent fills (last 60 seconds should be sufficient)
            start_time = int((time.time() - 60) * 1000)
            fills = self._info.user_fills_by_time(
                self._wallet_address, start_time
            )
            # Find fills matching this order
            order_fills = [
                f for f in fills
                if f.get("oid") == oid and f.get("coin") == symbol
            ]
            if order_fills:
                # Compute volume-weighted average price
                total_sz = sum(float(f["sz"]) for f in order_fills)
                if total_sz > 0:
                    vwap = sum(
                        float(f["px"]) * float(f["sz"])
                        for f in order_fills
                    ) / total_sz
                    return vwap
        except Exception:
            logger.warning("Failed to get fill price via user_fills_by_time")
        return None

    def _cancel_open_trigger_orders(self, symbol: str) -> None:
        """Cancel all open trigger orders (server-side SL/TP) for a symbol."""
        try:
            open_orders = self._info.frontend_open_orders(self._wallet_address)
            for order in open_orders:
                if (
                    order.get("coin") == symbol
                    and order.get("orderType") == "trigger"
                ):
                    self._exchange.cancel(symbol, order["oid"])
                    logger.debug("Cancelled trigger order %d", order["oid"])
        except Exception:
            logger.warning("Failed to cancel trigger orders for %s", symbol)

    def _get_trade(self, trade_id: int) -> dict | None:
        """Fetch a trade record from DB."""
        cur = self.db.conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        )
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None

    @staticmethod
    def _calc_pnl(trade: dict, exit_price: float) -> float:
        if trade["side"] == "long":
            return (
                (exit_price - trade["entry_price"])
                / trade["entry_price"]
                * trade["size_usd"]
            )
        return (
            (trade["entry_price"] - exit_price)
            / trade["entry_price"]
            * trade["size_usd"]
        )

    # ── Slippage monitoring ────────────────────────────────

    def _record_slippage(
        self, expected: float, actual: float, is_buy: bool,
    ) -> None:
        """Record slippage for a fill and warn if excessive."""
        if is_buy:
            slippage_pct = (actual - expected) / expected * 100
        else:
            slippage_pct = (expected - actual) / expected * 100
        self._slippage_history.append(slippage_pct)
        logger.info(
            "Slippage: expected=%.2f actual=%.2f slip=%.4f%%",
            expected, actual, slippage_pct,
        )
        if abs(slippage_pct) > 0.1:
            logger.warning(
                "High slippage detected: %.4f%%", slippage_pct,
            )

    def get_slippage_stats(self) -> dict:
        """Return slippage statistics for monitoring."""
        if not self._slippage_history:
            return {"count": 0, "avg_pct": 0.0, "max_pct": 0.0}
        return {
            "count": len(self._slippage_history),
            "avg_pct": sum(self._slippage_history)
            / len(self._slippage_history),
            "max_pct": max(
                abs(s) for s in self._slippage_history
            ),
        }

    # ── Position queries ─────────────────────────────────

    def get_exchange_position(self, symbol: str) -> dict | None:
        """Query the exchange for current position state (crash recovery)."""
        try:
            state = self._info.user_state(self._wallet_address)
            for pos in state.get("assetPositions", []):
                position = pos.get("position", {})
                if position.get("coin") == symbol:
                    szi = float(position.get("szi", "0"))
                    if szi != 0:
                        return {
                            "symbol": symbol,
                            "side": "long" if szi > 0 else "short",
                            "size_base": abs(szi),
                            "entry_price": float(
                                position.get("entryPx", "0")
                            ),
                            "unrealized_pnl": float(
                                position.get("unrealizedPnl", "0")
                            ),
                        }
        except Exception:
            logger.exception("Failed to query exchange position for %s", symbol)
        return None

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol on the exchange. Returns True on success."""
        try:
            self._exchange.update_leverage(leverage, symbol, is_cross=True)
            logger.info("Set leverage for %s to %dx (cross)", symbol, leverage)
            return True
        except Exception:
            logger.exception("Failed to set leverage for %s", symbol)
            return False
