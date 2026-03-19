"""Tests for the WebSocket client — unit tests using mocked SDK."""

from __future__ import annotations

from unittest.mock import patch

from perp_bot.data.ws_client import WsClient


class TestWsClientMidPrices:
    def test_get_mid_price_returns_none_before_subscribe(self):
        with patch("perp_bot.data.ws_client.Info"):
            ws = WsClient()
            assert ws.get_mid_price("ETH") is None

    def test_mid_price_cache_updated_by_callback(self):
        with patch("perp_bot.data.ws_client.Info") as MockInfo:
            mock_info = MockInfo.return_value
            mock_info.subscribe.return_value = 1

            ws = WsClient()
            ws.subscribe_mid_prices(["ETH", "BTC"])

            # Extract the callback that was passed to subscribe
            call_args = mock_info.subscribe.call_args
            callback = call_args[0][1]

            # Simulate a message from the WebSocket
            callback({"data": {"mids": {"ETH": "2500.5", "BTC": "95000.0"}}})

            assert ws.get_mid_price("ETH") == 2500.5
            assert ws.get_mid_price("BTC") == 95000.0
            assert ws.get_mid_price("SOL") is None  # not in data

    def test_mid_price_filters_to_watched_symbols(self):
        with patch("perp_bot.data.ws_client.Info") as MockInfo:
            mock_info = MockInfo.return_value
            mock_info.subscribe.return_value = 1

            ws = WsClient()
            ws.subscribe_mid_prices(["ETH"])

            callback = mock_info.subscribe.call_args[0][1]
            callback({
                "data": {"mids": {"ETH": "2500.0", "BTC": "95000.0"}},
            })

            assert ws.get_mid_price("ETH") == 2500.0
            # BTC not watched — should not be cached
            assert ws.get_mid_price("BTC") is None


class TestWsClientCandles:
    def test_candle_callback_receives_normalised_dict(self):
        with patch("perp_bot.data.ws_client.Info") as MockInfo:
            mock_info = MockInfo.return_value
            mock_info.subscribe.return_value = 1

            ws = WsClient()
            received = []
            ws.subscribe_candles("ETH", "15m", received.append)

            callback = mock_info.subscribe.call_args[0][1]
            callback({
                "data": {
                    "s": "ETH", "i": "15m", "t": 1000000,
                    "o": "2500", "h": "2510", "l": "2490",
                    "c": "2505", "v": "1000", "n": 50,
                },
            })

            assert len(received) == 1
            c = received[0]
            assert c["symbol"] == "ETH"
            assert c["timeframe"] == "15m"
            assert c["close"] == 2505.0
            assert c["num_trades"] == 50

    def test_malformed_candle_does_not_crash(self):
        with patch("perp_bot.data.ws_client.Info") as MockInfo:
            mock_info = MockInfo.return_value
            mock_info.subscribe.return_value = 1

            ws = WsClient()
            received = []
            ws.subscribe_candles("ETH", "15m", received.append)

            callback = mock_info.subscribe.call_args[0][1]
            # Missing keys — should log warning, not raise
            callback({"data": {"s": "ETH"}})
            assert len(received) == 0


class TestWsClientBbo:
    def test_bbo_callback_receives_bid_ask(self):
        with patch("perp_bot.data.ws_client.Info") as MockInfo:
            mock_info = MockInfo.return_value
            mock_info.subscribe.return_value = 1

            ws = WsClient()
            received = []
            ws.subscribe_bbo("ETH", lambda b, a: received.append((b, a)))

            callback = mock_info.subscribe.call_args[0][1]
            callback({
                "data": {
                    "coin": "ETH",
                    "time": 1000000,
                    "bbo": [{"px": "2500.0"}, {"px": "2500.5"}],
                },
            })

            assert len(received) == 1
            assert received[0] == (2500.0, 2500.5)


class TestWsClientHealthCheck:
    def test_healthy_when_no_subscriptions(self):
        with patch("perp_bot.data.ws_client.Info"):
            ws = WsClient()
            assert ws.is_healthy() is True

    def test_healthy_after_recent_update(self):
        with patch("perp_bot.data.ws_client.Info") as MockInfo:
            mock_info = MockInfo.return_value
            mock_info.subscribe.return_value = 1

            ws = WsClient()
            ws.subscribe_mid_prices(["ETH"])

            callback = mock_info.subscribe.call_args[0][1]
            callback({"data": {"mids": {"ETH": "2500.0"}}})

            assert ws.is_healthy() is True

    def test_unhealthy_when_stale(self):
        with patch("perp_bot.data.ws_client.Info") as MockInfo:
            mock_info = MockInfo.return_value
            mock_info.subscribe.return_value = 1

            ws = WsClient()
            ws._stale_threshold_seconds = 0.01  # Very short for testing
            ws.subscribe_mid_prices(["ETH"])

            callback = mock_info.subscribe.call_args[0][1]
            callback({"data": {"mids": {"ETH": "2500.0"}}})

            import time
            time.sleep(0.02)
            assert ws.is_healthy() is False

    def test_reconnect_recreates_info(self):
        with patch("perp_bot.data.ws_client.Info") as MockInfo:
            mock_info = MockInfo.return_value
            mock_info.subscribe.return_value = 1

            ws = WsClient()
            ws.subscribe_mid_prices(["ETH"])
            assert len(ws._sub_ids) == 1

            ws.reconnect()

            # Info should have been recreated (called twice total)
            assert MockInfo.call_count == 2
            # Should have re-subscribed mid prices
            assert len(ws._sub_ids) == 1


class TestWsClientLifecycle:
    def test_close_unsubscribes_all(self):
        with patch("perp_bot.data.ws_client.Info") as MockInfo:
            mock_info = MockInfo.return_value
            mock_info.subscribe.return_value = 1

            ws = WsClient()
            ws.subscribe_mid_prices(["ETH"])
            ws.subscribe_candles("ETH", "15m", lambda x: None)
            assert len(ws._sub_ids) == 2

            ws.close()
            assert mock_info.unsubscribe.call_count == 2
            assert len(ws._sub_ids) == 0
