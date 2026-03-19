"""Tests for alert dispatch — Discord and Telegram (mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from perp_bot.infra.alerts import send_discord_alert, send_telegram_alert


class TestDiscordAlert:
    def test_returns_false_if_no_url(self):
        assert send_discord_alert("", "test") is False

    @patch("perp_bot.infra.alerts.urllib.request.urlopen")
    def test_sends_payload(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = send_discord_alert("https://example.com/webhook", "hello")
        assert result is True
        mock_urlopen.assert_called_once()


class TestTelegramAlert:
    def test_returns_false_if_no_token(self):
        assert send_telegram_alert("", "123", "test") is False

    def test_returns_false_if_no_chat_id(self):
        assert send_telegram_alert("bot123", "", "test") is False

    @patch("perp_bot.infra.alerts.urllib.request.urlopen")
    def test_sends_message(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = send_telegram_alert("bot123", "456", "hello")
        assert result is True
        mock_urlopen.assert_called_once()
        # Verify URL contains the bot token and message params
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert "bot123" in req.full_url
        assert "456" in req.full_url
