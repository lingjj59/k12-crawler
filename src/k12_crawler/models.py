"""Domain models shared by discovery, parsing, storage and export."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def now_iso() -> str:
    """Local ISO-8601 timestamp with timezone offset."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class ValidationIssue:
    code: str
    severity: str  # "fail" | "warn"
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass
class Option:
    label: str
    text: str
    # Normalized HTML for this option (with <sup>/<sub> and [[IMG:n]] kept).
    # None for rows collected before phase 3.
    html: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {"label": self.label, "text": self.text, "html": self.html}


@dataclass
class Resource:
    ordinal: int
    role: str  # stem | option | answer | analysis
    original_url: str
    placeholder: str
    option_label: Optional[str] = None
    original_extension: Optional[str] = None
    format: Optional[str] = None
    local_path: Optional[str] = None
    converted_path: Optional[str] = None
    conversion_status: str = "pending"
    width: Optional[int] = None
    height: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "role": self.role,
            "option_label": self.option_label,
            "placeholder": self.placeholder,
            "original_url": self.original_url,
            "original_extension": self.original_extension,
            "format": self.format,
            "local_path": self.local_path,
            "converted_path": self.converted_path,
            "conversion_status": self.conversion_status,
            "width": self.width,
            "height": self.height,
            "error": self.error,
        }


@dataclass
class PaperRef:
    source: str
    paper_id: str
    title: str
    url: str
    page: int = 1
    total_questions: Optional[int] = None
    updated_at: Optional[str] = None
    downloads: Optional[str] = None


@dataclass
class Paper:
    source: str
    paper_id: str
    title: str
    url: str
    subject: str
    stage: str
    page_kind: str = "paper"  # paper | detail
    year: Optional[int] = None
    region: Optional[str] = None
    exam_type: Optional[str] = None
    raw_html_path: Optional[str] = None
    questions: List["Question"] = field(default_factory=list)
    collected_at: str = field(default_factory=now_iso)


@dataclass
class Question:
    source: str
    source_question_id: str
    paper_id: str
    paper_title: str
    subject: str
    stage: str
    question_type: str
    question_number: Optional[int]
    stem_html: str
    stem_text: str
    options: List[Option]
    answer: Optional[str]
    analysis_html: Optional[str]
    analysis_text: Optional[str]
    knowledge_points: List[str]
    difficulty: Optional[str]
    difficulty_stars: Optional[int]
    source_url: str
    paper_url: str
    collected_at: str = field(default_factory=now_iso)
    raw_html_path: Optional[str] = None
    raw_question_html_path: Optional[str] = None
    raw_block_html: Optional[str] = None
    resources: List[Resource] = field(default_factory=list)
    validation_issues: List[ValidationIssue] = field(default_factory=list)
    validation_status: str = "pending"
    choice_type: str = "unknown"
    content_hash: str = ""

    def to_dict(self, include_resources: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "source": self.source,
            "source_question_id": self.source_question_id,
            "paper_id": self.paper_id,
            "paper_title": self.paper_title,
            "subject": self.subject,
            "stage": self.stage,
            "question_type": self.question_type,
            "choice_type": self.choice_type,
            "question_number": self.question_number,
            "stem_html": self.stem_html,
            "stem_text": self.stem_text,
            "options": [o.to_dict() for o in self.options],
            "answer": self.answer,
            "analysis_html": self.analysis_html,
            "analysis_text": self.analysis_text,
            "knowledge_points": self.knowledge_points,
            "difficulty": self.difficulty,
            "difficulty_stars": self.difficulty_stars,
            "source_url": self.source_url,
            "paper_url": self.paper_url,
            "raw_html_path": self.raw_html_path,
            "raw_question_html_path": self.raw_question_html_path,
            "collected_at": self.collected_at,
            "content_hash": self.content_hash,
            "validation_status": self.validation_status,
            "validation_issues": [i.to_dict() for i in self.validation_issues],
        }
        if include_resources:
            data["resources"] = [r.to_dict() for r in self.resources]
        return data


@dataclass
class RunSummary:
    run_id: Optional[int] = None
    job_id: Optional[int] = None
    resumed: bool = False
    pages_fetched: int = 0
    papers_seen: int = 0
    papers_parsed: int = 0
    papers_completed: int = 0
    papers_failed: int = 0
    papers_skipped: int = 0
    questions_parsed: int = 0
    questions_selected: int = 0
    questions_stored: int = 0
    questions_passed: int = 0
    questions_warned: int = 0
    questions_failed: int = 0
    questions_duplicate: int = 0
    questions_capped: int = 0
    resources_total: int = 0
    resources_downloaded: int = 0
    resources_failed: int = 0
    errors: int = 0
    cap_reached: bool = False
    status: str = "running"
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "resumed": self.resumed,
            "pages_fetched": self.pages_fetched,
            "papers_seen": self.papers_seen,
            "papers_parsed": self.papers_parsed,
            "papers_completed": self.papers_completed,
            "papers_failed": self.papers_failed,
            "papers_skipped": self.papers_skipped,
            "questions_parsed": self.questions_parsed,
            "questions_selected": self.questions_selected,
            "questions_stored": self.questions_stored,
            "questions_passed": self.questions_passed,
            "questions_warned": self.questions_warned,
            "questions_failed": self.questions_failed,
            "questions_duplicate": self.questions_duplicate,
            "questions_capped": self.questions_capped,
            "resources_total": self.resources_total,
            "resources_downloaded": self.resources_downloaded,
            "resources_failed": self.resources_failed,
            "errors": self.errors,
            "cap_reached": self.cap_reached,
            "status": self.status,
            "note": self.note,
        }
