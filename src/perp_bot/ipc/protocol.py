"""Shared IPC constants and helpers."""

from __future__ import annotations

from pathlib import Path

SOCKET_NAME = "perp-bot.sock"

# Command constants
CMD_GET_STATE = "get_state"
CMD_PAUSE = "pause"
CMD_RESUME = "resume"
CMD_EMERGENCY_CLOSE = "emergency_close"


def get_socket_path(db_path: str | Path) -> Path:
    """Derive the socket path from the database directory.

    The socket lives alongside the SQLite DB so that the TUI can
    locate it from the same config.
    """
    return Path(db_path).parent / SOCKET_NAME
