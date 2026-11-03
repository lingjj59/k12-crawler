#!/usr/bin/env python3
"""Merge selected batch2 candidates into the final 2000-question JSONL."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from k12_crawler.models import Option, Question, Resource, ValidationIssue  # noqa: E402
from k12_crawler.validation import validate_question  # noqa: E402


def _load(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _question(record: Dict[str, Any]) -> Question:
    resources = []
    for item in record.get("resources") or []:
        resources.append(
            Resource(
                ordinal=int(item.get("ordinal") or len(resources) + 1),
                role=item.get("role") or "stem",
                original_url=item.get("original_url") or "",
                placeholder=item.get("placeholder") or f"[[IMG:{len(resources) + 1}]]",
                option_label=item.get("option_label"),
                original_extension=item.get("original_extension"),
                format=item.get("format"),
                local_path=item.get("local_path"),
                converted_path=item.get("converted_path"),
                conversion_status=item.get("conversion_status") or "pending",
                width=item.get("width"),
                height=item.get("height"),
                error=item.get("error"),
            )
        )
    question = Question(
        source=record.get("source") or "baidu_tiku",
        source_question_id=str(record.get("source_question_id")),
        paper_id=str(record.get("paper_id") or ""),
        paper_title=str(record.get("paper_title") or ""),
        subject=str(record.get("subject") or "化学"),
        stage=str(record.get("stage") or "高中"),
        question_type=str(record.get("question_type") or "选择题"),
        question_number=record.get("question_number"),
        stem_html=str(record.get("stem_html") or ""),
        stem_text=str(record.get("stem_text") or ""),
        options=[
            Option(label=str(o.get("label")), text=str(o.get("text") or ""), html=o.get("html"))
            for o in record.get("options") or []
        ],
        answer=record.get("answer"),
        analysis_html=record.get("analysis_html"),
        analysis_text=record.get("analysis_text"),
        knowledge_points=[
            point
            for point in (record.get("knowledge_points") or [])
            if not re.match(r"^第[一二三四五六七八九十]+章", point)
        ],
        difficulty=record.get("difficulty"),
        difficulty_stars=record.get("difficulty_stars"),
        source_url=str(record.get("source_url") or ""),
        paper_url=str(record.get("paper_url") or ""),
        collected_at=str(record.get("collected_at") or ""),
        raw_html_path=record.get("raw_html_path"),
        raw_question_html_path=record.get("raw_question_html_path"),
        resources=resources,
        choice_type=str(record.get("choice_type") or "unknown"),
        content_hash=str(record.get("content_hash") or ""),
    )
    issues = validate_question(question, check_files=True)
    question.validation_issues = [
        ValidationIssue(code=i.code, severity=i.severity, message=i.message) for i in issues
    ]
    return question


def _images_ready(record: Dict[str, Any]) -> bool:
    resources = record.get("resources") or []
    if not resources:
        return False
    for item in resources:
        local_path = item.get("local_path")
        if not local_path:
            return False
        if not (PROJECT_ROOT / local_path).exists():
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize chemistry batch2")
    parser.add_argument("--input", default="data/chemistry/work/batch02_baidu_2000/candidates_downloaded.jsonl")
    parser.add_argument("--out", default="data/chemistry/releases/batch02_baidu_2000/high_school_chemistry.jsonl")
    parser.add_argument("--no-image", type=int, default=1700)
    parser.add_argument("--image", type=int, default=300)
    args = parser.parse_args()

    rows = _load(PROJECT_ROOT / args.input)
    no_image = [r for r in rows if r.get("selection_slot") == "no_image"]
    image = [r for r in rows if r.get("selection_slot") == "image"]
    image_ready = [r for r in image if _images_ready(r)]

    selected = no_image[: args.no_image]
    selected += image_ready[: args.image]

    output_path = PROJECT_ROOT / args.out
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats: Dict[str, Any] = {
        "no_image_available": len(no_image),
        "image_available": len(image),
        "image_with_all_files": len(image_ready),
        "selected_no_image": min(len(no_image), args.no_image),
        "selected_image": min(len(image_ready), args.image),
        "image_rejected_missing_files": len(image) - len(image_ready),
        "status_counts": {},
        "validation_issue_counts": {},
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    with output_path.open("w", encoding="utf-8") as handle:
        for record in selected:
            question = _question(record)
            data = question.to_dict()
            data["release_id"] = "batch02_baidu_2000"
            data["paper_year"] = record.get("paper_year")
            data["paper_region"] = record.get("paper_region")
            data["paper_exam_type"] = record.get("paper_exam_type")
            data["crawl_job_id"] = None
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")
            stats["status_counts"][question.validation_status] = (
                stats["status_counts"].get(question.validation_status, 0) + 1
            )
            for issue in question.validation_issues:
                stats["validation_issue_counts"][issue.code] = (
                    stats["validation_issue_counts"].get(issue.code, 0) + 1
                )

    stats["total_written"] = len(selected)
    try:
        stats["output"] = str(output_path.relative_to(PROJECT_ROOT))
    except ValueError:
        stats["output"] = str(output_path)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if len(selected) != args.no_image + args.image:
        print(
            f"ERROR: only {len(selected)} selected, expected {args.no_image + args.image}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
