"""Tests for repo-local CLI entrypoints."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_py_help_runs() -> None:
    result = subprocess.run(
        ["./main.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Hyperliquid mean-reversion bot" in result.stdout


def test_repo_launcher_help_runs() -> None:
    result = subprocess.run(
        ["./perpbot", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": os.environ["PATH"]},
    )

    assert result.returncode == 0
    assert "Hyperliquid mean-reversion bot" in result.stdout
