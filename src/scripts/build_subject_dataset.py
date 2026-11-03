#!/usr/bin/env python3
"""Export a clean, fixed-size subject dataset from the project SQLite store.

The raw ``export`` command exports every row matching a filter.  This script is
the final delivery step: it keeps only validated rows (``pass`` / ``warn``),
caps the output at a requested size, and writes the result atomically.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from k12_crawler.storage import Storage  # noqa: E402


def build(
    db_path: Path,
    output: Path,
    subject: str,
    stage: str,
    limit: int,
    statuses: tuple[str, ...] = ("pass", "warn"),
) -> List[Dict[str, Any]]:
    storage = Storage(db_path)
    try:
        selected = [
            record
            for record in storage.iter_question_records(subject=subject, stage=stage)
            if record.get("validation_status") in statuses
        ][:limit]
    finally:
        storage.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(output.parent), prefix=output.name, suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in selected:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "shared" / "k12_high_school.sqlite")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "chemistry" / "work" / "exports" / "high_school_chemistry.jsonl")
    parser.add_argument("--subject", default="化学")
    parser.add_argument("--stage", default="高中")
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()

    records = build(args.db, args.output, args.subject, args.stage, args.limit)
    print(f"wrote {len(records)} records to {args.output}")
    if len(records) < args.limit:
        print(f"warning: only {len(records)} valid records available", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
