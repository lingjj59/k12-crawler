#!/usr/bin/env python3
"""Import the finalized chemistry batch2 JSONL into the shared SQLite DB."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "scripts"))

from finalize_chemistry_batch2 import _load, _question  # noqa: E402
from k12_crawler.storage import Storage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import chemistry batch2 into SQLite")
    parser.add_argument(
        "--input", default="data/chemistry/releases/batch02_baidu_2000/high_school_chemistry.jsonl"
    )
    parser.add_argument("--db", default="data/shared/k12_high_school.sqlite")
    args = parser.parse_args()

    rows = _load(PROJECT_ROOT / args.input)
    storage = Storage(PROJECT_ROOT / args.db)
    counts: Counter = Counter()
    duplicates = []
    try:
        before = storage.count_questions()
        for record in rows:
            question = _question(record)
            status, duplicate_of, internal_id = storage.save_question(question)
            counts[status] += 1
            if status in ("source_duplicate", "content_duplicate"):
                duplicates.append(
                    {
                        "source": question.source,
                        "source_question_id": question.source_question_id,
                        "duplicate_of": duplicate_of,
                    }
                )
        after = storage.count_questions()
    finally:
        storage.close()

    summary = {
        "input": str((PROJECT_ROOT / args.input).relative_to(PROJECT_ROOT)),
        "rows": len(rows),
        "status": dict(counts),
        "questions_before": before,
        "questions_after": after,
        "inserted": after - before,
        "duplicates": duplicates[:20],
        "duplicate_count": len(duplicates),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
