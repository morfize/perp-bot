"""Tests for the IPC layer — protocol, state, server, client."""

from __future__ import annotations

import os
import tempfile
import time

import pytest

from perp_bot.ipc.client import DaemonClient
from perp_bot.ipc.protocol import (
    SOCKET_NAME,
    get_socket_path,
)
from perp_bot.ipc.server import DaemonStateServer
from perp_bot.ipc.state import DaemonState

# ── Protocol ──────────────────────────────────────────


class TestProtocol:
    def test_socket_path_derives_from_db(self, tmp_path):
        db_path = tmp_path / "data" / "bot.db"
        db_path.parent.mkdir(parents=True)
        result = get_socket_path(str(db_path))
        assert result == db_path.parent / SOCKET_NAME

    def test_socket_name_constant(self):
        assert SOCKET_NAME == "perp-bot.sock"


# ── DaemonState ───────────────────────────────────────


class TestDaemonState:
    def test_default_state(self):
        state = DaemonState()
        assert state.mode == "paper"
        assert state.paused is False
        assert state.tick_count == 0
        assert state.ws_healthy is False

    def test_snapshot_returns_dict(self):
        state = DaemonState(mode="live", tick_count=42)
        snap = state.snapshot()
        assert isinstance(snap, dict)
        assert snap["mode"] == "live"
        assert snap["tick_count"] == 42
        assert "uptime_seconds" in snap

    def test_snapshot_is_deep_copy(self):
        state = DaemonState()
        state.mid_prices["ETH"] = 3200.0
        snap = state.snapshot()
        state.mid_prices["ETH"] = 9999.0
        assert snap["mid_prices"]["ETH"] == 3200.0

    def test_update_multiple_fields(self):
        state = DaemonState()
        state.update(mode="live", paused=True, tick_count=10)
        assert state.mode == "live"
        assert state.paused is True
        assert state.tick_count == 10

    def test_update_ignores_private_fields(self):
        state = DaemonState()
        state.update(_lock="hacked")
        import threading
        assert isinstance(state._lock, threading.Lock)


# ── Server + Client round-trip ────────────────────────


@pytest.fixture
def short_sock_path():
    """Create a short socket path that fits in AF_UNIX's 104-byte limit."""
    # /tmp/pb-XXXXXXXX/pb.sock is well under the limit
    tmpdir = tempfile.mkdtemp(prefix="pb-", dir="/tmp")
    from pathlib import Path
    sock = Path(tmpdir) / "pb.sock"
    yield sock
    # Cleanup
    if sock.exists():
        sock.unlink()
    os.rmdir(tmpdir)


@pytest.fixture
def ipc_pair(short_sock_path):
    """Start server + client pair for testing, auto-cleanup."""
    state = DaemonState(mode="paper")
    server = DaemonStateServer(short_sock_path, state)
    server.start()
    client = DaemonClient(short_sock_path)
    time.sleep(0.1)
    yield state, server, client
    server.stop()


class TestServerClient:
    def test_get_state(self, ipc_pair):
        state, _server, client = ipc_pair
        state.update(tick_count=5, ws_healthy=True)
        result = client.get_state()
        assert result is not None
        assert result["tick_count"] == 5
        assert result["ws_healthy"] is True

    def test_pause_resume(self, ipc_pair):
        state, _server, client = ipc_pair
        assert state.paused is False

        resp = client.pause()
        assert resp["ok"] is True
        assert state.paused is True

        resp = client.resume()
        assert resp["ok"] is True
        assert state.paused is False

    def test_is_running(self, ipc_pair):
        _state, _server, client = ipc_pair
        assert client.is_running() is True

    def test_is_running_no_server(self, tmp_path):
        sock_path = tmp_path / "nonexistent.sock"
        client = DaemonClient(sock_path)
        assert client.is_running() is False

    def test_unknown_command(self, ipc_pair):
        _state, _server, client = ipc_pair
        result = client._request({"cmd": "bogus"})
        assert result is not None
        assert result["ok"] is False
        assert "unknown_command" in result.get("error", "")

    def test_emergency_close_no_executor(self, ipc_pair):
        _state, _server, client = ipc_pair
        result = client.emergency_close("ETH")
        assert result is not None
        assert result["ok"] is False
        assert "executor" in result.get("error", "")

    def test_emergency_close_no_open_trades(self, ipc_pair):
        _state, server, client = ipc_pair
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db.get_open_trades.return_value = []
        server._db = mock_db
        server._executor = MagicMock()

        result = client.emergency_close("ETH")
        assert result["ok"] is True
        assert "no_open_trades" in result.get("message", "")

    def test_server_cleans_stale_socket(self, short_sock_path):
        short_sock_path.touch()
        state = DaemonState()
        server = DaemonStateServer(short_sock_path, state)
        server.start()
        time.sleep(0.1)

        client = DaemonClient(short_sock_path)
        assert client.is_running() is True
        server.stop()

    def test_multiple_sequential_requests(self, ipc_pair):
        state, _server, client = ipc_pair
        for i in range(5):
            state.update(tick_count=i)
            result = client.get_state()
            assert result["tick_count"] == i

    def test_client_handles_dead_server(self, short_sock_path):
        state = DaemonState()
        server = DaemonStateServer(short_sock_path, state)
        server.start()
        time.sleep(0.1)
        client = DaemonClient(short_sock_path)

        server.stop()
        time.sleep(0.1)

        result = client.get_state()
        assert result is None

    def test_server_stop_removes_socket(self, short_sock_path):
        state = DaemonState()
        server = DaemonStateServer(short_sock_path, state)
        server.start()
        time.sleep(0.1)
        assert short_sock_path.exists()
        server.stop()
        assert not short_sock_path.exists()
