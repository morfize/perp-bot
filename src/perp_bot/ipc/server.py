"""Threaded Unix socket server for daemon state exposure and command dispatch."""

from __future__ import annotations

import json
import logging
import socket
import threading
from pathlib import Path

from perp_bot.ipc.protocol import (
    CMD_EMERGENCY_CLOSE,
    CMD_GET_STATE,
    CMD_PAUSE,
    CMD_RESUME,
)
from perp_bot.ipc.state import DaemonState

logger = logging.getLogger(__name__)

_RECV_BUF = 4096


class DaemonStateServer:
    """Unix socket server that exposes daemon state and accepts commands.

    Runs in a daemon thread — auto-dies with the main process.
    Each connection is stateless: read one JSON line, dispatch, respond, close.
    """

    def __init__(
        self,
        socket_path: Path,
        state: DaemonState,
        executor=None,
        db=None,
    ) -> None:
        self._socket_path = socket_path
        self._state = state
        self._executor = executor
        self._db = db
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        """Start the server thread."""
        # Clean up stale socket file
        if self._socket_path.exists():
            try:
                self._socket_path.unlink()
            except OSError:
                logger.warning("Could not remove stale socket %s", self._socket_path)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(str(self._socket_path))
        self._server_sock.listen(5)
        self._server_sock.settimeout(1.0)  # Allow periodic shutdown checks
        self._running = True

        self._thread = threading.Thread(
            target=self._serve_loop, name="ipc-server", daemon=True,
        )
        self._thread.start()
        logger.info("IPC server listening on %s", self._socket_path)

    def stop(self) -> None:
        """Stop the server and clean up the socket file."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        if self._socket_path.exists():
            try:
                self._socket_path.unlink()
            except OSError:
                pass
        logger.info("IPC server stopped")

    def _serve_loop(self) -> None:
        """Accept connections in a loop until stopped."""
        while self._running:
            try:
                conn, _ = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.debug("Socket accept error")
                break

            try:
                self._handle_connection(conn)
            except Exception:
                logger.debug("Error handling IPC connection", exc_info=True)
            finally:
                conn.close()

    def _handle_connection(self, conn: socket.socket) -> None:
        """Read one JSON command, dispatch, write JSON response, close."""
        conn.settimeout(5.0)
        data = conn.recv(_RECV_BUF)
        if not data:
            return

        line = data.decode("utf-8").strip()
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            self._send_response(conn, {"error": "invalid_json"})
            return

        cmd = request.get("cmd", "")
        response = self._dispatch(cmd, request)
        self._send_response(conn, response)

    def _dispatch(self, cmd: str, request: dict) -> dict:
        if cmd == CMD_GET_STATE:
            return {"ok": True, "state": self._state.snapshot()}

        if cmd == CMD_PAUSE:
            self._state.update(paused=True)
            logger.info("Daemon PAUSED via IPC")
            return {"ok": True, "paused": True}

        if cmd == CMD_RESUME:
            self._state.update(paused=False)
            logger.info("Daemon RESUMED via IPC")
            return {"ok": True, "paused": False}

        if cmd == CMD_EMERGENCY_CLOSE:
            return self._handle_emergency_close(request)

        return {"ok": False, "error": f"unknown_command: {cmd}"}

    def _handle_emergency_close(self, request: dict) -> dict:
        symbol = request.get("symbol")
        if not symbol:
            return {"ok": False, "error": "symbol_required"}

        if not self._executor or not self._db:
            return {"ok": False, "error": "executor_not_available"}

        open_trades = self._db.get_open_trades(symbol)
        if not open_trades:
            return {"ok": True, "message": f"no_open_trades_for_{symbol}"}

        closed = 0
        for trade in open_trades:
            try:
                self._executor.close_position(
                    trade["id"], symbol, trade["entry_price"], 0.0,
                    "emergency_close_ipc",
                )
                closed += 1
            except Exception:
                logger.exception(
                    "Failed to emergency-close trade #%d", trade["id"],
                )

        logger.warning(
            "EMERGENCY CLOSE: closed %d/%d trades for %s",
            closed, len(open_trades), symbol,
        )
        return {"ok": True, "closed": closed, "total": len(open_trades)}

    @staticmethod
    def _send_response(conn: socket.socket, response: dict) -> None:
        try:
            payload = json.dumps(response) + "\n"
            conn.sendall(payload.encode("utf-8"))
        except (BrokenPipeError, OSError):
            pass
