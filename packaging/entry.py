"""PyInstaller entry point for the standalone Windows build."""

from k12_crawler.cli_main import main

if __name__ == "__main__":
    raise SystemExit(main())
