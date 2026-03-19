"""Client for communicating with the daemon via Unix socket."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from perp_bot.ipc.protocol import (
    CMD_EMERGENCY_CLOSE,
    CMD_GET_STATE,
    CMD_PAUSE,
    CMD_RESUME,
)

_RECV_BUF = 65536
_TIMEOUT = 2.0


class DaemonClient:
    """Connects to the daemon's Unix socket for state queries and commands.

    All methods return None on connection failure for graceful degradation.
    """

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path

    def is_running(self) -> bool:
        """Check if the daemon is reachable."""
        if not self._socket_path.exists():
            return False
        result = self._request({"cmd": CMD_GET_STATE})
        return result is not None and result.get("ok", False)

    def get_state(self) -> dict | None:
        """Fetch full daemon state snapshot."""
        result = self._request({"cmd": CMD_GET_STATE})
        if result and result.get("ok"):
            return result.get("state")
        return None

    def pause(self) -> dict | None:
        """Pause the trading loop."""
        return self._request({"cmd": CMD_PAUSE})

    def resume(self) -> dict | None:
        """Resume the trading loop."""
        return self._request({"cmd": CMD_RESUME})

    def emergency_close(self, symbol: str) -> dict | None:
        """Emergency-close all positions for a symbol."""
        return self._request({"cmd": CMD_EMERGENCY_CLOSE, "symbol": symbol})

    def _request(self, msg: dict) -> dict | None:
        """Send a JSON request and receive a JSON response.

        Returns None on any connection failure.
        """
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(_TIMEOUT)
            sock.connect(str(self._socket_path))
            sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))

            # Receive response
            chunks = []
            while True:
                chunk = sock.recv(_RECV_BUF)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break

            sock.close()
            data = b"".join(chunks).decode("utf-8").strip()
            return json.loads(data) if data else None
        except (OSError, json.JSONDecodeError, ConnectionRefusedError):
            return None
