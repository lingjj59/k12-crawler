"""Export helpers kept separate from the storage implementation."""

from __future__ import annotations

from pathlib import Path

from .storage import Storage


def export_jsonl(storage: Storage, output: Path) -> int:
    return storage.export_jsonl(Path(output))
