"""Import harvested ``items.jsonl`` rows into the shared SQLite store.

``harvest`` deliberately does not write SQLite (see ``SKILL.md``).  This module
is the single bridge from a harvested JSONL file to the database: it rebuilds
``Paper``/``Question`` models, validates when the file has no verdict yet, and
reuses ``Storage.save_paper``/``save_question`` so source and content
duplicates are handled by the same rules as the legacy pipeline.
"""

from __future__ import annotations

import json
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import Config, PROJECT_ROOT
from .html_utils import content_hash
from .models import Option, Paper, Question, Resource, ValidationIssue, now_iso
from .storage import Storage
from .validation import validate_question

VALID_STATUSES = ("pass", "warn", "fail")


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def load_items(path: Path) -> List[Dict[str, Any]]:
    """Read a JSONL file; blank lines are ignored, malformed lines reported."""
    rows: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_no}: expected a JSON object")
            rows.append(item)
    return rows


def _options(raw: Any) -> List[Option]:
    options: List[Option] = []
    for entry in raw or []:
        if isinstance(entry, dict):
            options.append(
                Option(
                    label=str(entry.get("label") or ""),
                    text=str(entry.get("text") or ""),
                    html=entry.get("html"),
                )
            )
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            options.append(Option(label=str(entry[0]), text=str(entry[1])))
    return options


def _resources(raw: Any) -> List[Resource]:
    resources: List[Resource] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        ordinal = _int(entry.get("ordinal")) or len(resources) + 1
        resources.append(
            Resource(
                ordinal=ordinal,
                role=str(entry.get("role") or "stem"),
                original_url=str(entry.get("original_url") or ""),
                placeholder=str(entry.get("placeholder") or f"[[IMG:{ordinal}]]"),
                option_label=entry.get("option_label"),
                original_extension=entry.get("original_extension"),
                format=entry.get("format"),
                local_path=entry.get("local_path"),
                converted_path=entry.get("converted_path"),
                conversion_status=str(entry.get("conversion_status") or "pending"),
                width=_int(entry.get("width")),
                height=_int(entry.get("height")),
                error=entry.get("error"),
            )
        )
    return resources


def _issues(raw: Any) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        issues.append(
            ValidationIssue(
                code=str(entry.get("code") or ""),
                severity=str(entry.get("severity") or "warn"),
                message=str(entry.get("message") or ""),
            )
        )
    return issues


def _build_question(
    record: Dict[str, Any],
    *,
    revalidate: bool,
    check_files: bool,
) -> Question:
    options = _options(record.get("options"))
    stem_text = str(record.get("stem_text") or "")
    answer = record.get("answer")
    question = Question(
        source=str(record.get("source") or record.get("site") or ""),
        source_question_id=str(record.get("source_question_id") or ""),
        paper_id=str(record.get("paper_id") or ""),
        paper_title=str(record.get("paper_title") or record.get("paper_id") or ""),
        subject=str(record.get("subject") or ""),
        stage=str(record.get("stage") or ""),
        question_type=str(record.get("question_type") or "选择题"),
        question_number=_int(record.get("question_number")),
        stem_html=str(record.get("stem_html") or ""),
        stem_text=stem_text,
        options=options,
        answer=str(answer) if answer else None,
        analysis_html=record.get("analysis_html"),
        analysis_text=record.get("analysis_text"),
        knowledge_points=[str(point) for point in (record.get("knowledge_points") or [])],
        difficulty=record.get("difficulty"),
        difficulty_stars=_int(record.get("difficulty_stars")),
        source_url=str(record.get("source_url") or ""),
        paper_url=str(record.get("paper_url") or ""),
        collected_at=str(record.get("collected_at") or now_iso()),
        raw_html_path=record.get("raw_html_path"),
        raw_question_html_path=record.get("raw_question_html_path"),
        raw_block_html=record.get("raw_block_html"),
        resources=_resources(record.get("resources")),
        validation_issues=_issues(record.get("validation_issues")),
        validation_status=str(record.get("validation_status") or "pending"),
        choice_type=str(record.get("choice_type") or "unknown"),
        content_hash=str(record.get("content_hash") or ""),
    )
    if not question.source:
        raise ValueError("item missing required field: source")
    if not question.source_question_id:
        raise ValueError("item missing required field: source_question_id")
    if not question.content_hash:
        question.content_hash = content_hash(
            question.stem_text,
            [(option.label, option.text) for option in question.options],
            question.answer,
        )
    if revalidate or question.validation_status not in VALID_STATUSES:
        validate_question(question, check_files=check_files)
    return question


def _paper_meta(record: Dict[str, Any], question: Question) -> Paper:
    return Paper(
        source=question.source,
        paper_id=question.paper_id,
        title=question.paper_title,
        url=question.paper_url,
        subject=question.subject,
        stage=question.stage,
        page_kind=str(record.get("page_kind") or "paper"),
        year=_int(record.get("paper_year", record.get("year"))),
        region=record.get("paper_region") or record.get("region"),
        exam_type=record.get("paper_exam_type") or record.get("exam_type"),
        raw_html_path=question.raw_html_path,
        collected_at=question.collected_at,
    )


def _dry_run_status(
    storage: Storage,
    question: Question,
    seen_source: set,
    seen_hash: set,
) -> str:
    source_key = (question.source, question.source_question_id)
    if source_key in seen_source or storage.question_exists(
        question.source, question.source_question_id
    ):
        return "source_duplicate"
    if question.content_hash in seen_hash:
        return "content_duplicate"
    row = storage.conn.execute(
        "SELECT 1 FROM questions WHERE content_hash=? LIMIT 1",
        (question.content_hash,),
    ).fetchone()
    if row is not None:
        return "content_duplicate"
    seen_source.add(source_key)
    seen_hash.add(question.content_hash)
    return "stored"


def import_items(
    config: Config,
    items_path: Path,
    *,
    release_id: Optional[str] = None,
    dry_run: bool = False,
    revalidate: bool = False,
    check_files: bool = True,
) -> Dict[str, Any]:
    """Import one ``items.jsonl`` file; safe to re-run (dedupe is idempotent)."""
    path = Path(items_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"items file not found: {path}")

    rows = load_items(path)
    groups: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
    for record in rows:
        question = _build_question(record, revalidate=revalidate, check_files=check_files)
        key = (question.source, question.paper_id)
        group = groups.setdefault(key, {"paper": None, "questions": OrderedDict()})
        if group["paper"] is None:
            group["paper"] = _paper_meta(record, question)
        group["questions"].setdefault(question.source_question_id, question)

    counts: Counter = Counter()
    statuses: Counter = Counter()
    storage = Storage(config.db_path)
    seen_source: set = set()
    seen_hash: set = set()
    try:
        for group in groups.values():
            paper: Paper = group["paper"]
            paper.questions = list(group["questions"].values())
            if not dry_run:
                storage.save_paper(paper)
            counts["papers"] += 1
            for question in paper.questions:
                if dry_run:
                    status = _dry_run_status(storage, question, seen_source, seen_hash)
                else:
                    status, _duplicate_of, internal_id = storage.save_question(question)
                    if release_id and status == "stored" and internal_id is not None:
                        storage.conn.execute(
                            "UPDATE questions SET release_id=? "
                            "WHERE id=? AND release_id IS NULL",
                            (release_id, internal_id),
                        )
                counts[status] += 1
                statuses[question.validation_status] += 1
        if not dry_run:
            storage.conn.commit()
    finally:
        storage.close()

    return {
        "status": "completed",
        "items": str(path),
        "rows": len(rows),
        "papers": counts["papers"],
        "questions": sum(counts[key] for key in ("stored", "source_duplicate", "content_duplicate")),
        "stored": counts["stored"],
        "source_duplicate": counts["source_duplicate"],
        "content_duplicate": counts["content_duplicate"],
        "validation": {
            "pass": statuses["pass"],
            "warn": statuses["warn"],
            "fail": statuses["fail"],
        },
        "release_id": release_id,
        "dry_run": dry_run,
    }
