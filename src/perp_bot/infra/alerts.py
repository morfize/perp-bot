"""Alert dispatch — Discord webhook + Telegram Bot API."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def send_discord_alert(webhook_url: str, message: str) -> bool:
    """Send a message to a Discord channel via webhook. Returns True on success."""
    if not webhook_url:
        logger.debug("No Discord webhook configured, skipping alert")
        return False

    payload = json.dumps({"content": message}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 204
    except Exception:
        logger.exception("Failed to send Discord alert")
        return False


def send_telegram_alert(bot_token: str, chat_id: str, message: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    if not bot_token or not chat_id:
        logger.debug("Telegram not configured, skipping alert")
        return False

    url = (
        f"https://api.telegram.org/bot{bot_token}/sendMessage?"
        + urllib.parse.urlencode({"chat_id": chat_id, "text": message})
    )
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        logger.exception("Failed to send Telegram alert")
        return False
