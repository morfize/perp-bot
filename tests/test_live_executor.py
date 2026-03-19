"""Tests for the LiveExecutor — unit tests using mocked SDK."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from perp_bot.config import (
    BotConfig,
    DataConfig,
    ExecutionConfig,
    RiskConfig,
    SignalConfig,
    TradingConfig,
)
from perp_bot.data.db import Database
from perp_bot.execution.live_executor import (
    LiveExecutor,
    _round_price,
    _round_size,
)


def _make_config() -> BotConfig:
    return BotConfig(
        trading=TradingConfig(
            symbols=["ETH"], leverage=3,
            capital_usd=670.0, margin_usage_limit=0.5,
        ),
        signals=SignalConfig(
            zscore_lookback=20, zscore_entry_threshold=2.0,
            zscore_exit_threshold=0.3, zscore_stop_threshold=3.0,
            bollinger_period=20, bollinger_std=2.0,
            rsi_period=14, rsi_overbought=70, rsi_oversold=30,
            adx_period=14, adx_threshold=25,
        ),
        risk=RiskConfig(
            max_loss_per_trade_pct=0.03, daily_loss_limit_pct=0.08,
            max_positions=1, cooldown_seconds=1800,
            position_timeout_hours=24,
        ),
        data=DataConfig(
            timeframes=["15m"], primary_timeframe="15m",
            history_days=90, db_path=":memory:",
        ),
        execution=ExecutionConfig(
            order_type="limit", taker_fallback_seconds=30,
            use_server_side_stop=True,
        ),
        mode="live",
        hl_private_key="0x" + "ab" * 32,
        hl_wallet_address="0x" + "cd" * 20,
    )


class TestRoundPrice:
    def test_round_to_5_sig_figs(self):
        assert _round_price(2543.67891) == 2543.7
        assert _round_price(0.00012345) == 0.00012345

    def test_zero(self):
        assert _round_price(0) == 0.0


class TestRoundSize:
    def test_round_to_decimals(self):
        assert _round_size(0.12345, 3) == 0.123
        assert _round_size(0.12345, 2) == 0.12
        assert _round_size(1.5, 0) == 2.0


class TestLiveExecutorInit:
    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_init_creates_exchange(self, mock_from_key, mock_exchange):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ab" * 20
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [
            {"universe": [{"name": "ETH", "szDecimals": 4}]}
        ]
        mock_ex.info = mock_info
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")

        executor = LiveExecutor(config, db)
        assert executor._sz_decimals.get("ETH") == 4
        mock_exchange.assert_called_once()
        db.close()


class TestLiveExecutorOpenPosition:
    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_open_position_calls_bulk_orders(
        self, mock_from_key, mock_exchange
    ):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ab" * 20
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [
            {"universe": [{"name": "ETH", "szDecimals": 3}]}
        ]
        mock_ex.info = mock_info

        # Simulate a filled order response
        mock_ex.bulk_orders.return_value = {
            "response": {
                "data": {
                    "statuses": [
                        {"filled": {"oid": 123, "avgPx": "2500.0"}},
                        {"resting": {"oid": 456}},
                    ]
                }
            }
        }
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")
        executor = LiveExecutor(config, db)

        trade_id = executor.open_position("ETH", "long", 1005.0, 2500.0)

        # Should have placed bulk_orders with entry + SL
        mock_ex.bulk_orders.assert_called_once()
        orders = mock_ex.bulk_orders.call_args[0][0]
        assert len(orders) == 2
        assert orders[0]["is_buy"] is True
        assert orders[1]["order_type"]["trigger"]["tpsl"] == "sl"

        # Trade should be recorded in DB
        assert trade_id is not None
        trades = db.get_open_trades("ETH")
        assert len(trades) == 1
        assert trades[0]["side"] == "long"
        db.close()


class TestLiveExecutorClosePosition:
    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_close_position_calls_market_close(
        self, mock_from_key, mock_exchange
    ):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ab" * 20
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [{"universe": []}]
        mock_info.frontend_open_orders.return_value = []
        mock_ex.info = mock_info
        mock_ex.market_close.return_value = {
            "response": {
                "data": {
                    "statuses": [{"filled": {"avgPx": "2600.0"}}]
                }
            }
        }
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")
        executor = LiveExecutor(config, db)

        # Insert a trade to close
        trade_id = db.insert_trade({
            "symbol": "ETH", "side": "long", "entry_time": 1000000,
            "entry_price": 2500.0, "size_usd": 1005.0, "is_paper": 0,
        })

        result = executor.close_position(
            trade_id, "ETH", 2600.0, 40.0, "mean_reversion_complete"
        )

        assert result is True
        mock_ex.market_close.assert_called_once()
        # Verify trigger orders were checked for cancellation
        mock_info.frontend_open_orders.assert_called_once()
        db.close()


class TestFillPriceExtraction:
    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_get_fill_price_uses_vwap_from_fills(
        self, mock_from_key, mock_exchange
    ):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ab" * 20
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [{"universe": []}]
        # Simulate two partial fills at different prices
        mock_info.user_fills_by_time.return_value = [
            {"oid": 100, "coin": "ETH", "px": "2500.0", "sz": "0.3"},
            {"oid": 100, "coin": "ETH", "px": "2502.0", "sz": "0.2"},
            {"oid": 999, "coin": "ETH", "px": "9999.0", "sz": "1.0"},  # different oid
        ]
        mock_ex.info = mock_info
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")
        executor = LiveExecutor(config, db)

        price = executor._get_fill_price("ETH", 100)
        # VWAP = (2500*0.3 + 2502*0.2) / 0.5 = (750 + 500.4) / 0.5 = 2500.8
        assert price is not None
        assert abs(price - 2500.8) < 0.01
        db.close()


class TestSlPlacementEscalation:
    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_sl_retries_once_on_failure(self, mock_from_key, mock_exchange):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ab" * 20
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [{"universe": []}]
        mock_ex.info = mock_info
        # First call fails, second succeeds
        mock_ex.order.side_effect = [Exception("API error"), MagicMock()]
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")
        executor = LiveExecutor(config, db)

        result = executor._place_server_side_sl("ETH", True, 0.5, 2400.0)
        assert result is True
        assert mock_ex.order.call_count == 2
        db.close()

    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_sl_returns_false_after_two_failures(self, mock_from_key, mock_exchange):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ab" * 20
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [{"universe": []}]
        mock_ex.info = mock_info
        mock_ex.order.side_effect = Exception("API down")
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")
        executor = LiveExecutor(config, db)

        result = executor._place_server_side_sl("ETH", True, 0.5, 2400.0)
        assert result is False
        assert mock_ex.order.call_count == 2
        db.close()


class TestLeverageValidation:
    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_set_leverage_returns_true_on_success(self, mock_from_key, mock_exchange):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ab" * 20
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [{"universe": []}]
        mock_ex.info = mock_info
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")
        executor = LiveExecutor(config, db)
        assert executor.set_leverage("ETH", 3) is True
        db.close()

    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_set_leverage_returns_false_on_failure(self, mock_from_key, mock_exchange):
        mock_wallet = MagicMock()
        mock_wallet.address = "0x" + "ab" * 20
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [{"universe": []}]
        mock_ex.info = mock_info
        mock_ex.update_leverage.side_effect = Exception("nope")
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")
        executor = LiveExecutor(config, db)
        assert executor.set_leverage("ETH", 3) is False
        db.close()


class TestLiveExecutorPositionQuery:
    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_get_exchange_position_returns_position(
        self, mock_from_key, mock_exchange
    ):
        mock_wallet = MagicMock()
        mock_wallet.address = "0xtest"
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [{"universe": []}]
        mock_info.user_state.return_value = {
            "assetPositions": [
                {
                    "position": {
                        "coin": "ETH",
                        "szi": "0.5",
                        "entryPx": "2500.0",
                        "unrealizedPnl": "25.0",
                    }
                }
            ]
        }
        mock_ex.info = mock_info
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")
        executor = LiveExecutor(config, db)
        pos = executor.get_exchange_position("ETH")

        assert pos is not None
        assert pos["side"] == "long"
        assert pos["size_base"] == 0.5
        assert pos["entry_price"] == 2500.0
        db.close()

    @patch("perp_bot.execution.live_executor.Exchange")
    @patch("perp_bot.execution.live_executor.eth_account.Account.from_key")
    def test_get_exchange_position_returns_none_if_empty(
        self, mock_from_key, mock_exchange
    ):
        mock_wallet = MagicMock()
        mock_wallet.address = "0xtest"
        mock_from_key.return_value = mock_wallet

        mock_ex = MagicMock()
        mock_info = MagicMock()
        mock_info.meta_and_asset_ctxs.return_value = [{"universe": []}]
        mock_info.user_state.return_value = {"assetPositions": []}
        mock_ex.info = mock_info
        mock_exchange.return_value = mock_ex

        config = _make_config()
        db = Database(":memory:")
        executor = LiveExecutor(config, db)
        pos = executor.get_exchange_position("ETH")
        assert pos is None
        db.close()
