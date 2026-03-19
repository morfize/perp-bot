"""Compatibility shim for direct ``python main.py ...`` usage."""

from perp_bot.cli import main


if __name__ == "__main__":
    main()
