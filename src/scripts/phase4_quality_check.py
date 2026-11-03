#!/usr/bin/env python3
"""Read-only quality audit for the phase-4 database and JSONL export."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from PIL import Image

from k12_crawler.config import load_config
from k12_crawler.storage import Storage


def main() -> int:
    config = load_config()
    storage = Storage(config.db_path)
    db = storage.conn
    result: dict = {}
    result["db_path"] = str(config.db_path)
    result["papers"] = db.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"]
    result["questions"] = db.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
    result["resources"] = db.execute("SELECT COUNT(*) c FROM resources").fetchone()["c"]
    result["by_source"] = {
        row["source"]: row["c"]
        for row in db.execute("SELECT source, COUNT(*) c FROM questions GROUP BY source")
    }
    result["by_stage"] = {
        row["stage"]: row["c"]
        for row in db.execute("SELECT stage, COUNT(*) c FROM questions GROUP BY stage")
    }
    result["by_source_stage"] = {
        f"{row['source']}/{row['stage']}": row["c"]
        for row in db.execute(
            "SELECT source, stage, COUNT(*) c FROM questions GROUP BY source, stage"
        )
    }
    result["by_validation"] = {
        row["validation_status"]: row["c"]
        for row in db.execute(
            "SELECT validation_status, COUNT(*) c FROM questions GROUP BY validation_status"
        )
    }
    result["by_choice_type"] = {
        row["choice_type"]: row["c"]
        for row in db.execute("SELECT choice_type, COUNT(*) c FROM questions GROUP BY choice_type")
    }
    result["validation_issue_counts"] = {
        row["code"]: row["c"]
        for row in db.execute(
            "SELECT code, COUNT(*) c FROM validation_issues GROUP BY code ORDER BY c DESC"
        )
    }
    result["questions_without_answer"] = db.execute(
        "SELECT COUNT(*) c FROM questions WHERE answer IS NULL OR answer=''"
    ).fetchone()["c"]
    result["questions_without_options"] = db.execute(
        "SELECT COUNT(*) c FROM questions WHERE options_json IS NULL OR options_json IN ('','[]')"
    ).fetchone()["c"]
    result["questions_without_hash"] = db.execute(
        "SELECT COUNT(*) c FROM questions WHERE content_hash IS NULL OR content_hash=''"
    ).fetchone()["c"]
    result["duplicate_audit_rows"] = db.execute("SELECT COUNT(*) c FROM duplicates").fetchone()["c"]
    result["unique_content_hashes"] = db.execute(
        "SELECT COUNT(DISTINCT content_hash) c FROM questions"
    ).fetchone()["c"]

    # Resource integrity.
    missing = []
    bad = []
    formats = Counter()
    widths = Counter()
    for row in db.execute(
        "SELECT r.id, r.local_path, r.format, r.width, r.height, q.source_question_id, q.source "
        "FROM resources r JOIN questions q ON q.id=r.question_id"
    ):
        formats[row["format"] or "unknown"] += 1
        if not row["local_path"]:
            missing.append(row["source_question_id"])
            continue
        path = Path(row["local_path"])
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists() or path.stat().st_size == 0:
            missing.append(row["source_question_id"])
            continue
        try:
            with Image.open(path) as image:
                width, height = image.size
            if not row["width"] or not row["height"]:
                bad.append(row["source_question_id"])
            widths[f"{width}x{height}"] += 1
        except Exception:
            bad.append(row["source_question_id"])
    result["resource_formats"] = dict(formats)
    result["resource_missing_files"] = len(missing)
    result["resource_bad_images"] = len(bad)
    result["unique_image_sizes"] = len(widths)
    result["image_size_top"] = widths.most_common(8)

    # Placeholder coverage.
    placeholder_issues = 0
    for row in db.execute(
        "SELECT id, stem_text, options_json, analysis_text FROM questions"
    ):
        text = row["stem_text"] or ""
        try:
            options = json.loads(row["options_json"] or "[]")
        except json.JSONDecodeError:
            options = []
        text += "".join(str(option.get("text") or "") for option in options)
        text += row["analysis_text"] or ""
        ordinals = {
            int(n)
            for n in re.findall(r"\[\[IMG:(\d+)\]\]", text)
        }
        if ordinals:
            have = {
                r["ordinal"]
                for r in db.execute(
                    "SELECT ordinal FROM resources WHERE question_id=?", (row["id"],)
                )
            }
            if not ordinals.issubset(have):
                placeholder_issues += 1
    result["placeholder_resource_mismatches"] = placeholder_issues

    export = config.export_dir / "high_school_physics.jsonl"
    if export.exists():
        lines = 0
        with export.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    lines += 1
        result["jsonl_path"] = str(export)
        result["jsonl_lines"] = lines
    high_school = config.export_dir / "high_school_physics.jsonl"
    if high_school.exists():
        with high_school.open(encoding="utf-8") as handle:
            result["high_school_jsonl_lines"] = sum(1 for line in handle if line.strip())
    result["open_sources_dir"] = str(ROOT / "data" / "physics" / "open_sources")
    result["k12stem_image_note"] = (
        "K12-STEM 的图片 CDN assets.stem-science.com 已 NXDOMAIN；本库只纳入其纯文本选择题，"
        "未把失效图片当作成功资源。"
    )
    storage.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out = ROOT / "docs" / "quality-report.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
