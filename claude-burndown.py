#!/usr/bin/env python3
"""Launcher for Claude Burndown (Enterprise Edition)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_burndown.cli import main

if __name__ == "__main__":
    main()
