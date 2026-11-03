"""SQLite persistence for papers, questions, resources, runs and failures."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .models import Paper, Question, RunSummary, now_iso


class ComplianceLimitError(RuntimeError):
    """The unauthorized-mode global paper/question cap would be exceeded."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_paper_id TEXT NOT NULL,
    title TEXT,
    url TEXT,
    subject TEXT,
    stage TEXT,
    page_kind TEXT,
    year INTEGER,
    region TEXT,
    exam_type TEXT,
    raw_html_path TEXT,
    question_count INTEGER,
    collected_at TEXT,
    crawl_job_id INTEGER,
    UNIQUE(source, source_paper_id)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_question_id TEXT NOT NULL,
    paper_id TEXT,
    paper_title TEXT,
    subject TEXT,
    stage TEXT,
    question_type TEXT,
    choice_type TEXT,
    question_number INTEGER,
    stem_html TEXT,
    stem_text TEXT,
    options_json TEXT,
    answer TEXT,
    analysis_html TEXT,
    analysis_text TEXT,
    knowledge_points TEXT,
    difficulty TEXT,
    difficulty_stars INTEGER,
    source_url TEXT,
    paper_url TEXT,
    raw_html_path TEXT,
    raw_question_html_path TEXT,
    collected_at TEXT,
    content_hash TEXT,
    validation_status TEXT,
    crawl_job_id INTEGER,
    release_id TEXT,
    UNIQUE(source, source_question_id)
);

CREATE INDEX IF NOT EXISTS idx_questions_content_hash ON questions(content_hash);
CREATE INDEX IF NOT EXISTS idx_questions_status ON questions(validation_status);
CREATE INDEX IF NOT EXISTS idx_questions_release ON questions(release_id);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    role TEXT,
    option_label TEXT,
    placeholder TEXT,
    original_url TEXT,
    original_extension TEXT,
    format TEXT,
    local_path TEXT,
    converted_path TEXT,
    conversion_status TEXT,
    width INTEGER,
    height INTEGER,
    error TEXT,
    UNIQUE(question_id, ordinal),
    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS validation_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    code TEXT,
    severity TEXT,
    message TEXT,
    created_at TEXT,
    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS duplicates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    source_question_id TEXT,
    duplicate_of_source_question_id TEXT,
    content_hash TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    job_id INTEGER,
    command TEXT,
    pages_fetched INTEGER DEFAULT 0,
    papers_seen INTEGER DEFAULT 0,
    papers_parsed INTEGER DEFAULT 0,
    questions_parsed INTEGER DEFAULT 0,
    questions_selected INTEGER DEFAULT 0,
    questions_stored INTEGER DEFAULT 0,
    questions_passed INTEGER DEFAULT 0,
    questions_warned INTEGER DEFAULT 0,
    questions_failed INTEGER DEFAULT 0,
    questions_duplicate INTEGER DEFAULT 0,
    resources_total INTEGER DEFAULT 0,
    resources_downloaded INTEGER DEFAULT 0,
    resources_failed INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    note TEXT
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    stage TEXT,
    url TEXT,
    error_type TEXT,
    message TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    max_papers INTEGER,
    max_questions INTEGER,
    paper_ids TEXT,
    download_images INTEGER DEFAULT 1,
    next_page INTEGER DEFAULT 1,
    total_pages INTEGER DEFAULT 1,
    questions_selected INTEGER DEFAULT 0,
    questions_stored INTEGER DEFAULT 0,
    papers_completed INTEGER DEFAULT 0,
    papers_failed INTEGER DEFAULT 0,
    papers_skipped INTEGER DEFAULT 0,
    cap_reached INTEGER DEFAULT 0,
    last_run_id INTEGER,
    error TEXT,
    note TEXT,
    created_at TEXT,
    updated_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_crawl_jobs_status ON crawl_jobs(status);

CREATE TABLE IF NOT EXISTS crawl_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    task_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    page INTEGER,
    paper_id TEXT,
    url TEXT,
    payload_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    retryable INTEGER NOT NULL DEFAULT 1,
    error_type TEXT,
    error_message TEXT,
    created_at TEXT,
    updated_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(job_id, task_key),
    FOREIGN KEY(job_id) REFERENCES crawl_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_crawl_tasks_job_status ON crawl_tasks(job_id, status);
CREATE INDEX IF NOT EXISTS idx_crawl_tasks_paper ON crawl_tasks(source, paper_id);

CREATE TABLE IF NOT EXISTS crawl_job_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    source_question_id TEXT NOT NULL,
    paper_id TEXT,
    created_at TEXT,
    UNIQUE(job_id, source_question_id),
    FOREIGN KEY(job_id) REFERENCES crawl_jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_crawl_job_questions_job ON crawl_job_questions(job_id);
"""


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add phase-3 columns to databases created by phase 2."""
        self._ensure_column("papers", "crawl_job_id", "INTEGER")
        self._ensure_column("questions", "crawl_job_id", "INTEGER")
        self._ensure_column("questions", "release_id", "TEXT")
        self._ensure_column("runs", "job_id", "INTEGER")
        self._ensure_column("runs", "command", "TEXT")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_job ON questions(crawl_job_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_questions_release ON questions(release_id)"
        )

    def _ensure_column(self, table: str, column: str, column_type: str) -> None:
        columns = {
            row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def close(self) -> None:
        self.conn.close()

    def start_run(
        self, note: str = "", job_id: Optional[int] = None, command: Optional[str] = None
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO runs(started_at, status, note, job_id, command) VALUES (?, ?, ?, ?, ?)",
            (now_iso(), "running", note, job_id, command),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def mark_run_interrupted(self, run_id: Optional[int]) -> None:
        if run_id is None:
            return
        self.conn.execute(
            "UPDATE runs SET status='interrupted', finished_at=? "
            "WHERE id=? AND status='running'",
            (now_iso(), run_id),
        )
        self.conn.commit()

    def finish_run(self, run_id: int, summary: RunSummary) -> None:
        self.conn.execute(
            """
            UPDATE runs SET finished_at=?, status=?, pages_fetched=?, papers_seen=?,
                papers_parsed=?, questions_parsed=?, questions_selected=?, questions_stored=?,
                questions_passed=?, questions_warned=?, questions_failed=?, questions_duplicate=?,
                resources_total=?, resources_downloaded=?, resources_failed=?, errors=?, note=?
            WHERE id=?
            """,
            (
                now_iso(),
                summary.status,
                summary.pages_fetched,
                summary.papers_seen,
                summary.papers_parsed,
                summary.questions_parsed,
                summary.questions_selected,
                summary.questions_stored,
                summary.questions_passed,
                summary.questions_warned,
                summary.questions_failed,
                summary.questions_duplicate,
                summary.resources_total,
                summary.resources_downloaded,
                summary.resources_failed,
                summary.errors,
                summary.note,
                run_id,
            ),
        )
        self.conn.commit()

    def record_error(
        self, run_id: Optional[int], stage: str, url: str, error_type: str, message: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO errors(run_id, stage, url, error_type, message, created_at) VALUES (?,?,?,?,?,?)",
            (run_id, stage, url, error_type, message[:2000], now_iso()),
        )
        self.conn.commit()

    def save_paper(
        self,
        paper: Paper,
        job_id: Optional[int] = None,
        max_total: Optional[int] = None,
    ) -> int:
        existing = self.conn.execute(
            "SELECT id FROM papers WHERE source=? AND source_paper_id=?",
            (paper.source, paper.paper_id),
        ).fetchone()
        if existing is None and max_total is not None:
            if self.count_papers(paper.source) >= int(max_total):
                raise ComplianceLimitError(
                    "unauthorized-mode paper cap reached: "
                    f"{self.count_papers(paper.source)} >= {int(max_total)}"
                )
        cursor = self.conn.execute(
            """
            INSERT INTO papers(source, source_paper_id, title, url, subject, stage, page_kind,
                year, region, exam_type, raw_html_path, question_count, collected_at,
                crawl_job_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source, source_paper_id) DO UPDATE SET
                title=excluded.title, url=excluded.url, subject=excluded.subject,
                stage=excluded.stage, page_kind=excluded.page_kind, year=excluded.year,
                region=excluded.region, exam_type=excluded.exam_type,
                raw_html_path=excluded.raw_html_path, question_count=excluded.question_count,
                collected_at=excluded.collected_at
            """,
            (
                paper.source,
                paper.paper_id,
                paper.title,
                paper.url,
                paper.subject,
                paper.stage,
                paper.page_kind,
                paper.year,
                paper.region,
                paper.exam_type,
                paper.raw_html_path,
                len(paper.questions),
                paper.collected_at,
                job_id,
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM papers WHERE source=? AND source_paper_id=?",
            (paper.source, paper.paper_id),
        ).fetchone()
        return int(row["id"])

    def save_question(
        self,
        question: Question,
        job_id: Optional[int] = None,
        max_total: Optional[int] = None,
    ) -> Tuple[str, Optional[str], Optional[int]]:
        """Return (status, duplicate_of, internal question id).

        ``status`` is one of ``stored``, ``source_duplicate``,
        ``content_duplicate`` or ``cap_reached``.  ``cap_reached`` means the
        unauthorized-mode global question cap would be exceeded, so nothing
        was written.
        """
        existing = self.conn.execute(
            "SELECT id, source_question_id FROM questions WHERE source=? AND source_question_id=?",
            (question.source, question.source_question_id),
        ).fetchone()
        if existing:
            self.conn.execute(
                "INSERT INTO duplicates(source, source_question_id, duplicate_of_source_question_id, content_hash, created_at) VALUES (?,?,?,?,?)",
                (
                    question.source,
                    question.source_question_id,
                    existing["source_question_id"],
                    question.content_hash,
                    now_iso(),
                ),
            )
            self.conn.commit()
            return "source_duplicate", existing["source_question_id"], int(existing["id"])

        same_hash = self.conn.execute(
            "SELECT id, source_question_id FROM questions WHERE content_hash=? LIMIT 1",
            (question.content_hash,),
        ).fetchone()
        if same_hash:
            self.conn.execute(
                "INSERT INTO duplicates(source, source_question_id, duplicate_of_source_question_id, content_hash, created_at) VALUES (?,?,?,?,?)",
                (
                    question.source,
                    question.source_question_id,
                    same_hash["source_question_id"],
                    question.content_hash,
                    now_iso(),
                ),
            )
            self.conn.commit()
            return "content_duplicate", same_hash["source_question_id"], int(same_hash["id"])

        if max_total is not None and self.count_questions(question.source) >= int(max_total):
            return "cap_reached", None, None

        cursor = self.conn.execute(
            """
            INSERT INTO questions(source, source_question_id, paper_id, paper_title, subject, stage,
                question_type, choice_type, question_number, stem_html, stem_text, options_json,
                answer, analysis_html, analysis_text, knowledge_points, difficulty, difficulty_stars,
                source_url, paper_url, raw_html_path, raw_question_html_path, collected_at,
                content_hash, validation_status, crawl_job_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                question.source,
                question.source_question_id,
                question.paper_id,
                question.paper_title,
                question.subject,
                question.stage,
                question.question_type,
                question.choice_type,
                question.question_number,
                question.stem_html,
                question.stem_text,
                json.dumps([o.to_dict() for o in question.options], ensure_ascii=False),
                question.answer,
                question.analysis_html,
                question.analysis_text,
                json.dumps(question.knowledge_points, ensure_ascii=False),
                question.difficulty,
                question.difficulty_stars,
                question.source_url,
                question.paper_url,
                question.raw_html_path,
                question.raw_question_html_path,
                question.collected_at,
                question.content_hash,
                question.validation_status,
                job_id,
            ),
        )
        internal_id = int(cursor.lastrowid)
        for resource in question.resources:
            self.conn.execute(
                """
                INSERT INTO resources(question_id, ordinal, role, option_label, placeholder,
                    original_url, original_extension, format, local_path, converted_path,
                    conversion_status, width, height, error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    internal_id,
                    resource.ordinal,
                    resource.role,
                    resource.option_label,
                    resource.placeholder,
                    resource.original_url,
                    resource.original_extension,
                    resource.format,
                    resource.local_path,
                    resource.converted_path,
                    resource.conversion_status,
                    resource.width,
                    resource.height,
                    resource.error,
                ),
            )
        for issue in question.validation_issues:
            self.conn.execute(
                "INSERT INTO validation_issues(question_id, code, severity, message, created_at) VALUES (?,?,?,?,?)",
                (internal_id, issue.code, issue.severity, issue.message, now_iso()),
            )
        self.conn.commit()
        return "stored", None, internal_id

    # ------------------------------------------------------------------
    # counts / lookups used by resume and compliance checks
    # ------------------------------------------------------------------
    def count_papers(self, source: Optional[str] = None) -> int:
        if source:
            row = self.conn.execute(
                "SELECT COUNT(*) AS c FROM papers WHERE source=?", (source,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS c FROM papers").fetchone()
        return int(row["c"])

    def counts_by_source(self) -> Dict[str, Any]:
        """Per-source question counts, used by the phase-4 ingest report."""
        rows = self.conn.execute(
            "SELECT source, COUNT(*) AS c FROM questions GROUP BY source ORDER BY source"
        ).fetchall()
        return {str(row["source"]): int(row["c"]) for row in rows}

    def count_questions(
        self, source: Optional[str] = None, job_id: Optional[int] = None
    ) -> int:
        clauses: List[str] = []
        params: List[Any] = []
        if source:
            clauses.append("source=?")
            params.append(source)
        if job_id is not None:
            clauses.append("crawl_job_id=?")
            params.append(job_id)
        sql = "SELECT COUNT(*) AS c FROM questions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return int(self.conn.execute(sql, params).fetchone()["c"])

    def paper_exists(self, source: str, paper_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM papers WHERE source=? AND source_paper_id=?",
            (source, paper_id),
        ).fetchone()
        return row is not None

    def question_exists(self, source: str, question_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM questions WHERE source=? AND source_question_id=?",
            (source, question_id),
        ).fetchone()
        return row is not None

    def iter_paper_ids(self) -> Iterator[Tuple[str, str]]:
        rows = self.conn.execute("SELECT source, source_paper_id FROM papers ORDER BY id")
        for row in rows:
            yield row["source"], row["source_paper_id"]

    def iter_question_source_ids(self) -> Iterator[Tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT source, source_question_id FROM questions ORDER BY id"
        )
        for row in rows:
            yield row["source"], row["source_question_id"]

    def list_papers(self, limit: int = 20, source: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = (
            "SELECT p.id, p.source, p.source_paper_id, p.title, p.year, p.region, "
            "p.exam_type, p.question_count, p.raw_html_path, p.collected_at, "
            "(SELECT COUNT(*) FROM questions q WHERE q.source=p.source "
            " AND q.paper_id=p.source_paper_id) AS stored_questions "
            "FROM papers p"
        )
        params: List[Any] = []
        if source:
            sql += " WHERE p.source=?"
            params.append(source)
        sql += " ORDER BY p.id DESC LIMIT ?"
        params.append(int(limit))
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def stats(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for table in ("papers", "questions", "resources", "duplicates", "errors"):
            result[table] = self.conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        result["by_status"] = {
            row["validation_status"]: row["c"]
            for row in self.conn.execute(
                "SELECT validation_status, COUNT(*) AS c FROM questions GROUP BY validation_status"
            )
        }
        result["by_choice_type"] = {
            row["choice_type"]: row["c"]
            for row in self.conn.execute(
                "SELECT choice_type, COUNT(*) AS c FROM questions GROUP BY choice_type"
            )
        }
        result["by_resource_format"] = {
            row["format"]: row["c"]
            for row in self.conn.execute(
                "SELECT format, COUNT(*) AS c FROM resources GROUP BY format"
            )
        }
        result["by_conversion_status"] = {
            row["conversion_status"]: row["c"]
            for row in self.conn.execute(
                "SELECT conversion_status, COUNT(*) AS c FROM resources GROUP BY conversion_status"
            )
        }
        result["by_original_extension"] = {
            row["original_extension"]: row["c"]
            for row in self.conn.execute(
                "SELECT original_extension, COUNT(*) AS c FROM resources GROUP BY original_extension"
            )
        }
        result["validation_issue_counts"] = {
            row["code"]: row["c"]
            for row in self.conn.execute(
                "SELECT code, COUNT(*) AS c FROM validation_issues GROUP BY code"
            )
        }
        result["unique_image_files"] = self.conn.execute(
            "SELECT COUNT(DISTINCT local_path) AS c FROM resources WHERE local_path IS NOT NULL"
        ).fetchone()["c"]
        result["runs"] = [
            dict(row) for row in self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 5")
        ]
        result["task_status"] = {
            row["status"]: row["c"]
            for row in self.conn.execute(
                "SELECT status, COUNT(*) AS c FROM crawl_tasks GROUP BY status"
            )
        }
        result["job_status"] = {
            row["status"]: row["c"]
            for row in self.conn.execute(
                "SELECT status, COUNT(*) AS c FROM crawl_jobs GROUP BY status"
            )
        }
        result["unfinished_jobs"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM crawl_jobs "
            "WHERE status IN ('pending','running','failed','partial','blocked')"
        ).fetchone()["c"]
        result["papers_detail"] = self.list_papers(limit=50)
        result["recent_errors"] = [
            dict(row)
            for row in self.conn.execute("SELECT * FROM errors ORDER BY id DESC LIMIT 10")
        ]
        return result

    def list_questions(
        self,
        limit: int = 20,
        paper_id: Optional[str] = None,
        source: Optional[str] = None,
        validation_status: Optional[str] = None,
        stage: Optional[str] = None,
        subject: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if paper_id:
            clauses.append("paper_id=?")
            params.append(paper_id)
        if source:
            clauses.append("source=?")
            params.append(source)
        if validation_status:
            clauses.append("validation_status=?")
            params.append(validation_status)
        if stage:
            clauses.append("stage=?")
            params.append(stage)
        if subject:
            clauses.append("subject=?")
            params.append(subject)
        sql = (
            "SELECT id, source, source_question_id, paper_id, question_type, choice_type, "
            "answer, stem_text, validation_status FROM questions"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id LIMIT ?"
        params.append(int(limit))
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _question_record(self, row: sqlite3.Row) -> Dict[str, Any]:
        resources = [
            dict(resource)
            for resource in self.conn.execute(
                "SELECT ordinal, role, option_label, placeholder, original_url, original_extension, "
                "format, local_path, converted_path, conversion_status, width, height, error "
                "FROM resources WHERE question_id=? ORDER BY ordinal",
                (row["id"],),
            )
        ]
        issues = [
            dict(issue)
            for issue in self.conn.execute(
                "SELECT code, severity, message FROM validation_issues WHERE question_id=? ORDER BY id",
                (row["id"],),
            )
        ]
        paper_row = self.conn.execute(
            "SELECT year, region, exam_type FROM papers WHERE source=? AND source_paper_id=?",
            (row["source"], row["paper_id"]),
        ).fetchone()
        options = json.loads(row["options_json"] or "[]")
        for option in options:
            if isinstance(option, dict):
                option.setdefault("html", None)
        return {
            "source": row["source"],
            "source_question_id": row["source_question_id"],
            "paper_id": row["paper_id"],
            "paper_title": row["paper_title"],
            "paper_year": paper_row["year"] if paper_row else None,
            "paper_region": paper_row["region"] if paper_row else None,
            "paper_exam_type": paper_row["exam_type"] if paper_row else None,
            "subject": row["subject"],
            "stage": row["stage"],
            "question_type": row["question_type"],
            "choice_type": row["choice_type"],
            "question_number": row["question_number"],
            "stem_html": row["stem_html"],
            "stem_text": row["stem_text"],
            "options": options,
            "answer": row["answer"],
            "analysis_html": row["analysis_html"],
            "analysis_text": row["analysis_text"],
            "knowledge_points": json.loads(row["knowledge_points"] or "[]"),
            "difficulty": row["difficulty"],
            "difficulty_stars": row["difficulty_stars"],
            "source_url": row["source_url"],
            "paper_url": row["paper_url"],
            "raw_html_path": row["raw_html_path"],
            "raw_question_html_path": row["raw_question_html_path"],
            "collected_at": row["collected_at"],
            "content_hash": row["content_hash"],
            "validation_status": row["validation_status"],
            "crawl_job_id": row["crawl_job_id"] if "crawl_job_id" in row.keys() else None,
            "validation_issues": issues,
            "resources": resources,
        }

    def iter_question_records(
        self,
        source: Optional[str] = None,
        paper_id: Optional[str] = None,
        paper_ids: Optional[Sequence[str]] = None,
        validation_status: Optional[str] = None,
        stage: Optional[str] = None,
        subject: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if source:
            clauses.append("source=?")
            params.append(source)
        if paper_ids:
            placeholders = ",".join("?" for _ in paper_ids)
            clauses.append(f"paper_id IN ({placeholders})")
            params.extend(paper_ids)
        elif paper_id:
            clauses.append("paper_id=?")
            params.append(paper_id)
        if validation_status:
            clauses.append("validation_status=?")
            params.append(validation_status)
        if stage:
            clauses.append("stage=?")
            params.append(stage)
        if subject:
            clauses.append("subject=?")
            params.append(subject)
        sql = "SELECT * FROM questions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self.conn.execute(sql, params)
        for row in rows:
            yield self._question_record(row)

    def export_jsonl(
        self,
        path: Path,
        source: Optional[str] = None,
        paper_id: Optional[str] = None,
        paper_ids: Optional[Sequence[str]] = None,
        validation_status: Optional[str] = None,
        stage: Optional[str] = None,
        subject: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> int:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w", encoding="utf-8") as fh:
            for record in self.iter_question_records(
                source=source,
                paper_id=paper_id,
                paper_ids=paper_ids,
                validation_status=validation_status,
                stage=stage,
                subject=subject,
                limit=limit,
            ):
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        return count

    def get_question(self, source: str, source_question_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM questions WHERE source=? AND source_question_id=?",
            (source, source_question_id),
        ).fetchone()
        return self._question_record(row) if row else None
