#!/usr/bin/env python3
"""Compatibility shim for the old entry point.

Version 1 was a single script run as ``python autotrader_bot.py``. The bot is
now the ``autotrader`` package; this forwards so any old cron job, workflow or
shell alias keeps working.
"""

import sys

if __name__ == "__main__":
    print("Note: autotrader_bot.py is now a wrapper. Use 'python -m autotrader run'.",
          file=sys.stderr)
    from autotrader.cli import main
    sys.exit(main(sys.argv[1:] or ["run"]))
