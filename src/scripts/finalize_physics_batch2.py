#!/usr/bin/env python3
"""Merge selected physics batch2 candidates into the final 2000-question JSONL.

Also writes the release ``manifest.json`` (counts, sources, coverage, image
stats and the SHA-256 of the JSONL) so the batch is self-describing.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import shutil
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
        subject=str(record.get("subject") or "物理"),
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


def _stage_images(records: List[Dict[str, Any]], release_dir: Path) -> int:
    """Copy used image files into the release and rewrite ``local_path``.

    The release must be self-contained (work/ is disposable), so every resource
    that survived selection is copied to ``<release>/images/<qid>/``.
    """
    copied = 0
    for record in records:
        qid = str(record.get("source_question_id") or "").replace(":", "_")
        for resource in record.get("resources") or []:
            local_path = resource.get("local_path")
            if not local_path:
                continue
            source = PROJECT_ROOT / local_path
            if not source.is_file():
                continue
            target = release_dir / "images" / qid / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size != source.stat().st_size:
                shutil.copy2(source, target)
                copied += 1
            resource["local_path"] = str(target.relative_to(PROJECT_ROOT))
    return copied


def _images_ready(record: Dict[str, Any], *, require_images: bool = True) -> bool:
    resources = record.get("resources") or []
    if not resources:
        return not require_images
    for item in resources:
        local_path = item.get("local_path")
        if not local_path:
            return False
        if not (PROJECT_ROOT / local_path).exists():
            return False
    return True


def _write_manifest(
    output_path: Path,
    written: List[Dict[str, Any]],
    *,
    release_id: str,
) -> Dict[str, Any]:
    question_type: Dict[str, int] = {}
    choice_type: Dict[str, int] = {}
    sources: Dict[str, int] = {}
    status: Dict[str, int] = {}
    with_images = resources = local_refs = 0
    no_image_slot = image_slot = text_only = formula_only = 0
    local_files: List[str] = []
    external_or_missing = 0
    missing_local = 0
    coverage = {
        "answer": 0,
        "analysis": 0,
        "knowledge_points": 0,
        "year": 0,
        "region": 0,
        "exam_type": 0,
    }
    for row in written:
        question_type[row.get("question_type") or "未知"] = (
            question_type.get(row.get("question_type") or "未知", 0) + 1
        )
        choice_type[row.get("choice_type") or "unknown"] = (
            choice_type.get(row.get("choice_type") or "unknown", 0) + 1
        )
        sources[row.get("source") or "unknown"] = sources.get(row.get("source") or "unknown", 0) + 1
        status[row.get("validation_status") or "unknown"] = (
            status.get(row.get("validation_status") or "unknown", 0) + 1
        )
        row_figure = any(
            (resource.get("height") or 0) > 80 or (resource.get("width") or 0) > 300
            for resource in row.get("resources") or []
        )
        if row_figure:
            image_slot += 1
        else:
            no_image_slot += 1
            if row.get("resources"):
                formula_only += 1
            else:
                text_only += 1
        if row.get("answer"):
            coverage["answer"] += 1
        if row.get("analysis_text"):
            coverage["analysis"] += 1
        if row.get("knowledge_points"):
            coverage["knowledge_points"] += 1
        if row.get("paper_year"):
            coverage["year"] += 1
        if row.get("paper_region"):
            coverage["region"] += 1
        if row.get("paper_exam_type"):
            coverage["exam_type"] += 1
        row_resources = row.get("resources") or []
        if row_resources:
            with_images += 1
        resources += len(row_resources)
        for resource in row_resources:
            local_path = resource.get("local_path")
            if not local_path:
                external_or_missing += 1
                continue
            local_refs += 1
            local_files.append(local_path)
            if not (PROJECT_ROOT / local_path).exists():
                missing_local += 1

    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "release_id": release_id,
        "subject": "physics",
        "stage": "高中",
        "created_at": datetime.now().astimezone().date().isoformat(),
        "question_count": len(written),
        "no_image_questions": no_image_slot or (len(written) - with_images),
        "image_questions": image_slot or with_images,
        "no_image_breakdown": {
            "definition": "no figure picture; inline formula fragments (<=80px tall, "
            "<=300px wide) do not count as images",
            "text_only": text_only,
            "formula_only": formula_only,
        },
        "question_type": question_type,
        "choice_type": choice_type,
        "sources": sources,
        "validation": {
            "pass": status.get("pass", 0),
            "warn": status.get("warn", 0),
            "fail": status.get("fail", 0),
        },
        "coverage": coverage,
        "images": {
            "questions_with_images": with_images,
            "resources": resources,
            "local_file_references": local_refs,
            "local_files": len(set(local_files)),
            "external_or_missing": external_or_missing,
            "missing_local_files": missing_local,
        },
        "raw_html_missing": 0,
        "files": {
            "questions": output_path.name,
            "images_dir": "images",
        },
        "questions_sha256": digest,
    }
    manifest_path = output_path.with_name("manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize physics batch2")
    parser.add_argument("--input", default="data/physics/work/batch02_baidu_2000/candidates_downloaded.jsonl")
    parser.add_argument("--out", default="data/physics/releases/batch02_baidu_2000/high_school_physics.jsonl")
    parser.add_argument("--no-image", type=int, default=1700)
    parser.add_argument("--image", type=int, default=300)
    parser.add_argument("--release-id", default="batch02_baidu_2000")
    args = parser.parse_args()

    rows = _load(PROJECT_ROOT / args.input)
    no_image = [r for r in rows if r.get("selection_slot") == "no_image"]
    image = [r for r in rows if r.get("selection_slot") == "image"]
    # Questions whose formula fragments failed to download would render with
    # broken placeholders, so drop them and backfill from the over-selection.
    no_image_ready = [r for r in no_image if _images_ready(r, require_images=False)]
    image_ready = [r for r in image if _images_ready(r)]

    selected = no_image_ready[: args.no_image]
    selected += image_ready[: args.image]

    output_path = PROJECT_ROOT / args.out
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staged = _stage_images(selected, output_path.parent)
    stats: Dict[str, Any] = {
        "no_image_available": len(no_image),
        "no_image_with_all_files": len(no_image_ready),
        "image_available": len(image),
        "image_with_all_files": len(image_ready),
        "selected_no_image": min(len(no_image), args.no_image),
        "selected_image": min(len(image_ready), args.image),
        "image_rejected_missing_files": len(image) - len(image_ready),
        "no_image_rejected_missing_files": len(no_image) - len(no_image_ready),
        "images_staged": staged,
        "status_counts": {},
        "validation_issue_counts": {},
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    written: List[Dict[str, Any]] = []
    with output_path.open("w", encoding="utf-8") as handle:
        for record in selected:
            question = _question(record)
            data = question.to_dict()
            data["release_id"] = args.release_id
            data["paper_year"] = record.get("paper_year")
            data["paper_region"] = record.get("paper_region")
            data["paper_exam_type"] = record.get("paper_exam_type")
            data["crawl_job_id"] = None
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")
            written.append(data)
            stats["status_counts"][question.validation_status] = (
                stats["status_counts"].get(question.validation_status, 0) + 1
            )
            for issue in question.validation_issues:
                stats["validation_issue_counts"][issue.code] = (
                    stats["validation_issue_counts"].get(issue.code, 0) + 1
                )

    stats["total_written"] = len(selected)
    stats["manifest"] = _write_manifest(
        output_path, written, release_id=args.release_id
    )["questions_sha256"]
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
