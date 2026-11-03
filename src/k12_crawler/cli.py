"""Console entry point: ``k12-crawler`` and ``python -m k12_crawler``."""

from __future__ import annotations

from .cli_main import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
