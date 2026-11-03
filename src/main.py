#!/usr/bin/env python3
"""Source-tree shim: ``python src/main.py ...`` keeps working.

The real implementation lives in :mod:`k12_crawler.cli_main`.
"""

from __future__ import annotations

import sys

from k12_crawler.cli_main import build_parser, main

__all__ = ["build_parser", "main"]

if __name__ == "__main__":
    sys.exit(main())
