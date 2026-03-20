#!/usr/bin/env python3
"""Compatibility shim for direct ``python main.py ...`` usage."""

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"

if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from perp_bot.cli import main


if __name__ == "__main__":
    main()
