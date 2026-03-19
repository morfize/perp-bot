"""Log widget — tails the daemon log file."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import RichLog


class LogWidget(RichLog):
    """Tails the daemon's JSON log file and displays recent entries."""

    BORDER_TITLE = "LOG"

    def __init__(self, log_path: Path | str, **kwargs) -> None:
        super().__init__(max_lines=200, wrap=True, markup=True, **kwargs)
        self._log_path = Path(log_path)
        self._last_pos: int = 0
        # Start from near end of file
        if self._log_path.exists():
            size = self._log_path.stat().st_size
            self._last_pos = max(0, size - 4096)

    def poll_log(self) -> None:
        """Read new lines from the log file since last poll."""
        if not self._log_path.exists():
            return

        try:
            with open(self._log_path) as f:
                f.seek(self._last_pos)
                new_data = f.read()
                self._last_pos = f.tell()
        except OSError:
            return

        if not new_data:
            return

        import json
        for line in new_data.strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("ts", "")
                # Extract HH:MM:SS from ISO timestamp
                time_part = ts.split("T")[1][:8] if "T" in ts else ts[:8]
                level = entry.get("level", "INFO")
                msg = entry.get("msg", line)

                level_colors = {
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold red",
                }
                color = level_colors.get(level, "white")
                self.write(f"[dim]{time_part}[/] [{color}]{msg}[/]")
            except (json.JSONDecodeError, KeyError):
                self.write(line)
