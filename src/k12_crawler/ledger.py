"""Append-only compliance ledger for the unauthorized-mode hard caps.

The phase-2 safety cap was enforced per process, which meant that splitting a
run into many invocations (or resetting the SQLite file) could collect more
than the 2-paper / 30-question technical sample.  This ledger records every
paper/question ever accepted, in a small JSONL file outside the main DB, and
is used as an additional gate before new content is fetched or stored.

Normal commands never delete or rewrite the ledger.  Deleting both the DB and
the ledger is a deliberate manual bypass of the site's terms, not something the
CLI can do.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set, Tuple


class ComplianceLedger:
    def __init__(self, path: Path, source: str):
        self.path = Path(path)
        self.source = source
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._papers: Dict[str, Set[str]] = {}
        self._questions: Dict[str, Set[str]] = {}
        self._load()

    # ------------------------------------------------------------------
    # loading / appending
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # A partially written final line is harmless; the ledger is a
                # safety net, not the primary source of truth.
                continue
            self._remember(
                str(record.get("kind") or ""),
                str(record.get("source") or ""),
                str(record.get("id") or ""),
            )

    def _remember(self, kind: str, source: str, item_id: str) -> bool:
        if not kind or not source or not item_id:
            return False
        bucket = self._papers if kind == "paper" else self._questions if kind == "question" else None
        if bucket is None:
            return False
        values = bucket.setdefault(source, set())
        if item_id in values:
            return False
        values.add(item_id)
        return True

    def _append(self, kind: str, source: str, item_id: str) -> None:
        record = {"kind": kind, "source": source, "id": item_id}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def register_paper(self, source: str, paper_id: str) -> bool:
        if not self._remember("paper", source, paper_id):
            return False
        self._append("paper", source, paper_id)
        return True

    def register_question(self, source: str, question_id: str) -> bool:
        if not self._remember("question", source, question_id):
            return False
        self._append("question", source, question_id)
        return True

    def seed_from_storage(self, storage: Any) -> Tuple[int, int]:
        """Import IDs already present in SQLite so the ledger is not reset by DB loss."""
        papers = 0
        questions = 0
        # The ledger is a cooco compliance gate, not a global question index.
        # Open-dataset sources have their own licences and must not consume or
        # pollute cooco's 2-paper/30-question budget.
        for source, paper_id in storage.iter_paper_ids():
            if source != self.source:
                continue
            if self.register_paper(source, paper_id):
                papers += 1
        for source, question_id in storage.iter_question_source_ids():
            if source != self.source:
                continue
            if self.register_question(source, question_id):
                questions += 1
        return papers, questions

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def has_paper(self, source: str, paper_id: str) -> bool:
        return paper_id in self._papers.get(source, set())

    def has_question(self, source: str, question_id: str) -> bool:
        return question_id in self._questions.get(source, set())

    def count_papers(self, source: Optional[str] = None) -> int:
        if source is not None:
            return len(self._papers.get(source, set()))
        return sum(len(values) for values in self._papers.values())

    def count_questions(self, source: Optional[str] = None) -> int:
        if source is not None:
            return len(self._questions.get(source, set()))
        return sum(len(values) for values in self._questions.values())

    def summary(self, source: Optional[str] = None) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "papers": self.count_papers(source),
            "questions": self.count_questions(source),
        }
