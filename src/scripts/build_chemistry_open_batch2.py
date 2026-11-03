#!/usr/bin/env python3
"""Convert public chemistry multiple-choice datasets to the batch2 schema.

These are used only to fill the no-image side of batch2; Baidu remains the
primary source.  Records with any figure reference are excluded so the
"no image" slot stays clean.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bs4 import BeautifulSoup  # noqa: E402
from k12_crawler.html_utils import clean_text, content_hash  # noqa: E402

OPEN_ROOT = PROJECT_ROOT / "data/chemistry/open_sources"
OPTION_MARKER = re.compile(r"(?m)(?:^|\n)\s*([A-H])[.、．]\s*")


def _brace_group(text: str, start: int) -> tuple:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return start + 1, index
    return start + 1, len(text)


def _replace_frac(text: str) -> str:
    marker = "\\frac"
    while marker in text:
        start = text.index(marker)
        cursor = start + len(marker)
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != "{":
            text = text[:start] + "/" + text[cursor:]
            continue
        numerator_start, numerator_end = _brace_group(text, cursor)
        cursor = numerator_end + 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != "{":
            text = text[:start] + "/" + text[numerator_start:numerator_end] + text[cursor:]
            continue
        denominator_start, denominator_end = _brace_group(text, cursor)
        text = (
            text[:start]
            + text[numerator_start:numerator_end]
            + "/"
            + text[denominator_start:denominator_end]
            + text[denominator_end + 1 :]
        )
    return text


def _clean_math(text: str) -> str:
    """Flatten the simple LaTeX used by the open datasets for text display."""
    # Commands that wrap their argument.
    text = re.sub(
        r"\\(?:mathrm|rm|text|mathit|mathbf|operatorname)\s*\{([^{}]*)\}",
        r"\1",
        text,
    )
    text = _replace_frac(text)
    # Drop the braces around simple sub/superscripts first; this also removes
    # one level of nesting such as {{}_{3}}.
    for _ in range(3):
        text = re.sub(r"_\{([^{}]*)\}", r"_\1", text)
        text = re.sub(r"\^\{([^{}]*)\}", r"^\1", text)
    symbols = {
        "\\leftharpoons": "⇌",
        "\\rightleftharpoons": "⇌",
        "\\longrightarrow": "→",
        "\\rightarrow": "→",
        "\\to": "→",
        "\\uparrow": "↑",
        "\\downarrow": "↓",
        "\\Delta": "Δ",
        "\\alpha": "α",
        "\\beta": "β",
        "\\gamma": "γ",
        "\\delta": "δ",
        "\\pi": "π",
        "\\times": "×",
        "\\pm": "±",
        "\\leq": "≤",
        "\\geq": "≥",
        "\\neq": "≠",
        "\\approx": "≈",
        "\\sim": "≈",
        "\\cdot": "·",
        "\\circ": "°",
        "\\%": "%",
    }
    for source, target in symbols.items():
        text = text.replace(source, target)
    text = re.sub(r"\\(?:left|right)(?![a-zA-Z])\s*", "", text)
    text = text.replace("\\ ", " ").replace("\\,", " ").replace("\\;", " ")
    text = text.replace("\\:", " ").replace("\\!", "")
    text = re.sub(r"\\[a-zA-Z]+\s*", "", text)
    text = text.replace("$", "").replace("{", "").replace("}", "").replace("\\", "")
    return re.sub(r"\s+", " ", text).strip()


def _plain(value: Any) -> str:
    text = str(value or "")
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
    return clean_text(_clean_math(text))


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def _option_dict(labels: Sequence[str], values: Sequence[str]) -> List[Dict[str, str]]:
    return [
        {"label": label, "text": _plain(value), "html": None}
        for label, value in zip(labels, values)
        if _plain(value)
    ]


def _answer(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    text = str(value or "").upper()
    return re.sub(r"[^A-H]", "", text)


def _split_inline(text: str) -> Tuple[str, List[Dict[str, str]]]:
    matches = list(OPTION_MARKER.finditer(text))
    if not matches:
        return _plain(text), []
    stem = _plain(text[: matches[0].start()])
    options: List[Dict[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = _plain(text[match.end() : end])
        if body:
            options.append({"label": match.group(1), "text": body, "html": None})
    return stem, options


def _has_figure(text: str) -> bool:
    return "![" in text or "[[IMG:" in text or "<img" in text.lower()


def _record(
    *,
    source: str,
    qid: str,
    stem: str,
    options: List[Dict[str, str]],
    answer: str,
    analysis: str = "",
    year: Any = None,
    region: Any = None,
    knowledge_points: Optional[Sequence[str]] = None,
    paper_title: str = "",
) -> Optional[Dict[str, Any]]:
    stem = _plain(stem)
    if not stem or _has_figure(stem + analysis):
        return None
    if not options or len(options) < 2:
        return None
    labels = [option["label"] for option in options]
    answer = _answer(answer)
    if not answer or any(letter not in labels for letter in answer):
        return None
    if len(answer) == 1:
        choice_type = "single"
    elif 2 <= len(answer) <= 4:
        choice_type = "multiple"
    else:
        return None
    digest = content_hash(stem, [(o["label"], o["text"]) for o in options], answer)
    return {
        "source": source,
        "source_question_id": qid,
        "paper_id": f"{source}:{paper_title or 'open'}",
        "paper_title": paper_title or source,
        "paper_year": int(year) if str(year or "").isdigit() else None,
        "paper_region": region,
        "paper_exam_type": "高考" if "gaokao" in source else None,
        "subject": "化学",
        "stage": "高中",
        "question_type": "选择题",
        "choice_type": choice_type,
        "question_number": None,
        "stem_text": stem,
        "stem_html": stem,
        "options": options,
        "answer": answer,
        "analysis_text": _plain(analysis) or None,
        "analysis_html": None,
        "knowledge_points": [str(point) for point in (knowledge_points or []) if str(point).strip()],
        "difficulty": None,
        "difficulty_stars": None,
        "source_url": "",
        "paper_url": "",
        "raw_html_path": None,
        "raw_question_html_path": None,
        "content_hash": digest,
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "resources": [],
        "validation_status": "warn",
        "validation_issues": [],
        "batch": "batch2",
        "selection_slot": "no_image",
    }


def _ceval() -> List[Dict[str, Any]]:
    import pyarrow.parquet as pq

    rows: List[Dict[str, Any]] = []
    for split in ("dev", "val", "test"):
        path = OPEN_ROOT / "ceval" / f"{split}.parquet"
        if not path.exists():
            continue
        table = pq.read_table(path)
        for row in table.to_pylist():
            options = _option_dict(["A", "B", "C", "D"], [row.get(k) for k in "ABCD"])
            record = _record(
                source="ceval",
                qid=f"ceval:{split}:{row.get('id')}",
                stem=row.get("question") or "",
                options=options,
                answer=row.get("answer") or "",
                analysis=row.get("explanation") or "",
                paper_title=f"C-Eval 高中化学 {split}",
            )
            if record:
                rows.append(record)
    return rows


def _cmmlu() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for split in ("dev", "test"):
        path = OPEN_ROOT / "cmmlu" / split / "high_school_chemistry.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for index, row in enumerate(reader):
                options = _option_dict(["A", "B", "C", "D"], [row.get(k) for k in "ABCD"])
                record = _record(
                    source="cmmlu",
                    qid=f"cmmlu:{split}:{index}",
                    stem=row.get("Question") or "",
                    options=options,
                    answer=row.get("Answer") or "",
                    paper_title=f"CMMLU 高中化学 {split}",
                )
                if record:
                    rows.append(record)
    return rows


def _agieval() -> List[Dict[str, Any]]:
    import pyarrow.parquet as pq

    path = OPEN_ROOT / "agieval" / "test.parquet"
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for index, row in enumerate(pq.read_table(path).to_pylist()):
        query = str(row.get("query") or "")
        stem, options = _split_inline(query.split("选项：")[0] if "选项：" in query else query)
        choices = row.get("choices") or []
        if choices:
            options = _option_dict([chr(ord("A") + i) for i in range(len(choices))], choices)
        gold = row.get("gold") or []
        if isinstance(gold, list) and gold:
            answer = "".join(chr(ord("A") + int(value)) for value in gold if isinstance(value, int))
        else:
            answer = str(gold or "")
        record = _record(
            source="agieval",
            qid=f"agieval:{index}",
            stem=stem,
            options=options,
            answer=answer,
            paper_title="AGIEval 高考化学",
        )
        if record:
            rows.append(record)
    return rows


def _gaokao_bench() -> List[Dict[str, Any]]:
    path = OPEN_ROOT / "gaokao_bench" / "chemistry_mcqs.jsonl"
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        raw = json.loads(line)
        stem, options = _split_inline(str(raw.get("question") or ""))
        record = _record(
            source="gaokao_bench",
            qid=f"gaokao_bench:{index}",
            stem=stem,
            options=options,
            answer=raw.get("answer"),
            analysis=raw.get("analysis") or "",
            year=raw.get("year"),
            region=raw.get("category"),
            paper_title="GAOKAO-Bench 化学多选题",
        )
        if record:
            rows.append(record)
    return rows


def _gaokao_rain() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted((OPEN_ROOT / "gaokao_rain").glob("*选择题.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            stem, options = _split_inline(str(raw.get("question") or ""))
            record = _record(
                source="gaokao_rain",
                qid=f"gaokao_rain:{raw.get('id')}",
                stem=stem,
                options=options,
                answer=raw.get("answer"),
                analysis=raw.get("analysis") or "",
                year=raw.get("year"),
                region=raw.get("province") or raw.get("paper_type"),
                paper_title=f"GAOKAO 化学 {raw.get('year')} {raw.get('paper_type')}",
            )
            if record:
                rows.append(record)
    return rows


def _k12stem() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for split in ("train", "validation", "test"):
        path = OPEN_ROOT / "k12stem" / f"{split}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            if raw.get("subject") != "Chemistry" or raw.get("grade") not in (10, 11, 12):
                continue
            if raw.get("question_type") != "Multiple-choice" or raw.get("question_images"):
                continue
            options = raw.get("options") or {}
            if not isinstance(options, dict):
                continue
            labels = sorted(options)
            option_rows = _option_dict(labels, [options[label] for label in labels])
            record = _record(
                source="k12stem",
                qid=f"k12stem:{raw.get('question_id')}",
                stem=raw.get("question_text") or "",
                options=option_rows,
                answer=raw.get("answer") or "",
                analysis=raw.get("step_by_step_proof") or raw.get("method_analysis") or "",
                year=raw.get("onshelf_date", "")[:4],
                knowledge_points=raw.get("knowledge_points") or [],
                paper_title=f"K12-STEM Grade {raw.get('grade')} 化学",
            )
            if record:
                rows.append(record)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert public chemistry questions")
    parser.add_argument("--out", default="data/chemistry/work/batch02_baidu_2000/open_items.jsonl")
    args = parser.parse_args()
    rows: List[Dict[str, Any]] = []
    rows += _ceval()
    rows += _cmmlu()
    rows += _agieval()
    rows += _gaokao_bench()
    rows += _gaokao_rain()
    rows += _k12stem()

    seen_hash: set = set()
    seen_loose: set = set()
    unique: List[Dict[str, Any]] = []
    for record in rows:
        loose = hashlib.sha1(
            "|".join(
                [_norm(record["stem_text"])[:600]]
                + [_norm(option["text"])[:300] for option in record["options"]]
            ).encode("utf-8")
        ).hexdigest()
        if record["content_hash"] in seen_hash or loose in seen_loose:
            continue
        seen_hash.add(record["content_hash"])
        seen_loose.add(loose)
        unique.append(record)

    output = PROJECT_ROOT / args.out
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in unique:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    stats = {
        "converted": len(rows),
        "unique": len(unique),
        "by_source": {},
        "output": str(output.relative_to(PROJECT_ROOT)),
    }
    for record in unique:
        stats["by_source"][record["source"]] = stats["by_source"].get(record["source"], 0) + 1
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
