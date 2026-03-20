"""Tests for the binary installer script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install.sh"


def test_install_script_prints_default_asset_name() -> None:
    env = {**os.environ, "PERPBOT_OS": "Darwin", "PERPBOT_ARCH": "arm64"}
    result = subprocess.run(
        ["sh", str(SCRIPT), "--print-asset"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "perpbot-macos-arm64.tar.gz"


def test_install_script_prints_latest_download_url() -> None:
    env = {**os.environ, "PERPBOT_OS": "Linux", "PERPBOT_ARCH": "x86_64"}
    result = subprocess.run(
        ["sh", str(SCRIPT), "--print-url"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == (
        "https://github.com/morfize/perp-bot/releases/latest/download/perpbot-linux-x86_64.tar.gz"
    )


def test_install_script_prints_tagged_download_url() -> None:
    env = {
        **os.environ,
        "PERPBOT_OS": "Darwin",
        "PERPBOT_ARCH": "x86_64",
        "PERPBOT_VERSION": "v0.1.3",
    }
    result = subprocess.run(
        ["sh", str(SCRIPT), "--print-url"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == (
        "https://github.com/morfize/perp-bot/releases/download/v0.1.3/perpbot-macos-x86_64.tar.gz"
    )


def test_install_script_prints_install_dir_override() -> None:
    env = {**os.environ, "PERPBOT_INSTALL_DIR": "/tmp/perpbot-bin"}
    result = subprocess.run(
        ["sh", str(SCRIPT), "--print-install-dir"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "/tmp/perpbot-bin"


def test_install_script_rejects_unsupported_os() -> None:
    env = {**os.environ, "PERPBOT_OS": "FreeBSD", "PERPBOT_ARCH": "x86_64"}
    result = subprocess.run(
        ["sh", str(SCRIPT), "--print-asset"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert "Unsupported operating system: FreeBSD" in result.stderr


def test_install_script_rejects_unsupported_arch() -> None:
    env = {**os.environ, "PERPBOT_OS": "Linux", "PERPBOT_ARCH": "riscv64"}
    result = subprocess.run(
        ["sh", str(SCRIPT), "--print-asset"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert "Unsupported architecture: riscv64" in result.stderr
