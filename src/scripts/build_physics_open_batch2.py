#!/usr/bin/env python3
"""Convert public physics question sets used to fill batch2's no-image side.

Baidu Tiku is figure-heavy, so the strict/figure-free pool needs a top-up.  The
RainPPR GAOKAO MCQ release (2010-2024, all 选择题) is text-only, carries answers
and analysis, and is licensed for research use.  Records are normalized to the
same schema as the Baidu items so the existing selection step can merge them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from build_chemistry_open_batch2 import (  # noqa: E402
    _plain,
    _record,
    _split_inline,
)

OPEN_ROOT = PROJECT_ROOT / "data/physics/open_sources"


def _gaokao_physics() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted((OPEN_ROOT / "gaokao_rain").glob("*选择题.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            stem, options = _split_inline(str(raw.get("question") or ""))
            paper = str(raw.get("paper_type") or "")
            year = raw.get("year")
            record = _record(
                source="gaokao_rain",
                qid=f"gaokao_rain:{raw.get('id')}",
                stem=stem,
                options=options,
                answer=raw.get("answer"),
                analysis=raw.get("analysis") or "",
                year=year,
                region=raw.get("province") or paper,
                paper_title=f"GAOKAO 物理 {year} {paper}",
            )
            if record is None:
                continue
            # ``_record`` is chemistry-oriented; relabel for the physics batch.
            record["subject"] = "物理"
            record["paper_exam_type"] = "高考"
            rows.append(record)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert public physics questions")
    parser.add_argument(
        "--out",
        default="data/physics/work/batch02_baidu_2000/open_extra.jsonl",
    )
    args = parser.parse_args()

    rows = _gaokao_physics()
    output = PROJECT_ROOT / args.out
    output.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    unique: List[Dict[str, Any]] = []
    for record in rows:
        key = record["content_hash"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    with output.open("w", encoding="utf-8") as handle:
        for record in unique:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = {
        "output": str(output.relative_to(PROJECT_ROOT)),
        "records": len(unique),
        "duplicates": len(rows) - len(unique),
        "by_source": {"gaokao_rain": len(unique)},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
