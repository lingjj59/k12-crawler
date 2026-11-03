#!/usr/bin/env python3
"""Select a second, non-overlapping chemistry set from crawled Baidu questions.

Target: 1700 text-only questions + 300 image questions.  The image candidates
are intentionally over-selected (the default is 350) so failed image downloads
can be replaced without rerunning the whole crawl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from k12_crawler.html_utils import clean_text  # noqa: E402
from k12_crawler.models import Option, Question, Resource, ValidationIssue  # noqa: E402
from k12_crawler.validation import validate_question  # noqa: E402


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _norm(text: Optional[str]) -> str:
    text = clean_text(text or "")
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def loose_key(record: Dict[str, Any]) -> str:
    parts = [_norm(record.get("stem_text"))[:600]]
    for option in record.get("options") or []:
        parts.append(_norm(option.get("text"))[:300])
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


PHYSICS_TERMS = (
    "光在", "光的折射", "光的干涉", "光的衍射", "光的偏振", "透镜", "光子",
    "电场", "磁场", "电流", "电阻", "电压", "电动势", "洛伦兹", "安培力",
    "加速度", "速度", "位移", "动量", "机械能", "重力", "摩擦力", "牛顿",
    "简谐", "波长", "频率", "波速", "热力学", "内能", "布朗运动", "放射性",
    "核反应", "半衰期", "光电效应", "相对论",
)
BIO_TERMS = (
    "细胞", "克隆", "基因", "遗传", "染色体", "核型", "组织培养", "光合作用",
    "呼吸作用", "免疫", "生态", "种群", "群落", "神经系统", "激素", "DNA",
    "RNA", "酶", "细胞分裂", "细胞分化", "动物细胞", "植物组织",
)
CHEMISTRY_TERMS = (
    "化学", "溶液", "反应", "元素", "化合物", "离子", "氧化", "还原",
    "酸", "碱", "盐", "有机", "燃烧", "沉淀", "催化", "电解", "电池",
    "化学平衡", "焓", "熵", "烃", "醇", "醛", "酯", "蛋白质", "葡萄糖",
    "淀粉", "纤维素", "胶体", "晶体", "配位", "络合", "滴定", "摩尔",
    "浓度", "电离", "水解", "电负性", "周期表", "同分异构", "官能团",
    "苯", "乙醇", "乙酸", "油脂", "糖类", "核酸", "氧化物", "二氧化硫",
    "甲醛", "亚硝酸钠", "碳酸", "硫酸", "盐酸", "硝酸", "氢氧化",
)


def _is_likely_chemistry(record: Dict[str, Any]) -> bool:
    text = clean_text(record.get("stem_text") or "")
    text += "".join(clean_text(option.get("text") or "") for option in record.get("options") or [])
    text += clean_text(record.get("analysis_text") or "")
    if any(term in text for term in CHEMISTRY_TERMS):
        return True
    if any(term in text for term in PHYSICS_TERMS) or any(term in text for term in BIO_TERMS):
        return False
    letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    chinese = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    if letters >= 30 and letters > chinese * 2:
        return False
    return True


def _valid_options(record: Dict[str, Any]) -> bool:
    options = record.get("options") or []
    labels = [str(option.get("label") or "").strip().upper() for option in options]
    return len(options) >= 2 and labels == [chr(ord("A") + i) for i in range(len(labels))]


def _valid_answer(record: Dict[str, Any]) -> bool:
    answer = str(record.get("answer") or "").upper()
    labels = {str(option.get("label") or "").upper() for option in record.get("options") or []}
    if not answer or any(ch not in labels for ch in answer):
        return False
    if record.get("choice_type") == "single":
        return len(answer) == 1
    if record.get("choice_type") == "multiple":
        return 2 <= len(answer) <= 4
    return False


def _qa_score(record: Dict[str, Any]) -> Tuple[int, int, int]:
    score = 0
    if record.get("analysis_text"):
        score += 40
    if record.get("knowledge_points"):
        score += 8
    if record.get("difficulty_stars"):
        score += 3
    year = record.get("paper_year")
    if isinstance(year, int):
        score += max(0, min(12, (year - 2000) // 2))
    if record.get("choice_type") == "single":
        score += 5
    if len(record.get("stem_text") or "") >= 30:
        score += 4
    # Prefer non-generic stems only as a tie-breaker, not a hard filter.
    generic = 1 if re.fullmatch(r"下列说法(正确|错误)的是[。.]?", clean_text(record.get("stem_text") or "")) else 0
    return (score, -generic, len(record.get("stem_text") or ""))


def _image_score(record: Dict[str, Any]) -> Tuple[int, int, int]:
    max_dim = 0
    has_stem_image = False
    for resource in record.get("resources") or []:
        width = resource.get("width") or 0
        height = resource.get("height") or 0
        max_dim = max(max_dim, width, height)
        if resource.get("role") == "stem":
            has_stem_image = True
    score = 0
    if max_dim >= 150:
        score += 50
    elif max_dim >= 100:
        score += 35
    elif max_dim >= 50:
        score += 15
    if has_stem_image:
        score += 20
    if record.get("analysis_text"):
        score += 15
    if record.get("choice_type") == "single":
        score += 5
    return (score, max_dim, len(record.get("stem_text") or ""))


def _record_to_question(record: Dict[str, Any], *, check_files: bool) -> Question:
    resources: List[Resource] = []
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
    options = [
        Option(label=str(option.get("label")), text=str(option.get("text") or ""), html=option.get("html"))
        for option in record.get("options") or []
    ]
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
        options=options,
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
    issues = validate_question(question, check_files=check_files)
    question.validation_issues = [
        ValidationIssue(code=i.code, severity=i.severity, message=i.message) for i in issues
    ]
    return question


def _to_output(question: Question, *, slot: str, record: Dict[str, Any]) -> Dict[str, Any]:
    data = question.to_dict()
    data["batch"] = "batch2"
    data["selection_slot"] = slot
    data["paper_year"] = record.get("paper_year")
    data["paper_region"] = record.get("paper_region")
    data["paper_exam_type"] = record.get("paper_exam_type")
    data["crawl_job_id"] = None
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Build chemistry batch2 candidate set")
    parser.add_argument("--input", default="data/chemistry/raw/baidu/gzhx/items.jsonl")
    parser.add_argument(
        "--extra-input",
        nargs="*",
        default=["data/chemistry/work/batch02_baidu_2000/open_items.jsonl"],
        help="additional JSONL sources used to fill the no-image side",
    )
    parser.add_argument("--batch1", default="data/chemistry/releases/batch01_cooco_2000/high_school_chemistry.jsonl")
    parser.add_argument("--out", default="data/chemistry/work/batch02_baidu_2000")
    parser.add_argument("--no-image", type=int, default=1700)
    parser.add_argument("--image", type=int, default=300)
    parser.add_argument("--image-candidates", type=int, default=380)
    parser.add_argument(
        "--no-image-candidates",
        type=int,
        default=1800,
        help="over-select no-image questions as a validation buffer",
    )
    args = parser.parse_args()

    out_dir = PROJECT_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    batch1 = _load_jsonl(PROJECT_ROOT / args.batch1)
    seen_hash: set = set()
    seen_loose: set = set()
    for record in batch1:
        if record.get("content_hash"):
            seen_hash.add(record["content_hash"])
        seen_loose.add(loose_key(record))

    rows: List[Dict[str, Any]] = []
    for path in [args.input] + list(args.extra_input or []):
        rows.extend(_load_jsonl(PROJECT_ROOT / path))
    no_image: List[Dict[str, Any]] = []
    image: List[Dict[str, Any]] = []
    dropped = {
        "duplicate_batch1_or_batch2": 0,
        "invalid": 0,
        "meta_or_empty": 0,
    }

    for record in rows:
        if not _valid_options(record) or not _valid_answer(record):
            dropped["invalid"] += 1
            continue
        if record.get("question_type") != "选择题":
            dropped["invalid"] += 1
            continue
        if not _is_likely_chemistry(record):
            dropped["physics_or_non_chemistry"] = (
                dropped.get("physics_or_non_chemistry", 0) + 1
            )
            continue
        stem = clean_text(record.get("stem_text") or "")
        if len(stem) < 8:
            dropped["meta_or_empty"] += 1
            continue
        full_text = stem + "".join(
            clean_text(option.get("text") or "") for option in record.get("options") or []
        ) + clean_text(record.get("analysis_text") or "")
        if "[[IMG:" in full_text and not record.get("resources"):
            dropped["invalid"] += 1
            continue
        key_hash = record.get("content_hash") or ""
        key_loose = loose_key(record)
        if key_hash in seen_hash or key_loose in seen_loose:
            dropped["duplicate_batch1_or_batch2"] += 1
            continue
        seen_hash.add(key_hash)
        seen_loose.add(key_loose)
        if record.get("resources"):
            image.append(record)
        else:
            no_image.append(record)

    no_image.sort(key=_qa_score, reverse=True)
    image.sort(key=_image_score, reverse=True)

    selected_no_image = no_image[: args.no_image_candidates]
    selected_image = image[: args.image_candidates]

    candidates_path = out_dir / "candidates.jsonl"
    with candidates_path.open("w", encoding="utf-8") as handle:
        for record in selected_no_image:
            question = _record_to_question(record, check_files=False)
            data = _to_output(question, slot="no_image", record=record)
            data["paper_year"] = record.get("paper_year")
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")
        for record in selected_image:
            question = _record_to_question(record, check_files=False)
            data = _to_output(question, slot="image", record=record)
            data["paper_year"] = record.get("paper_year")
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")

    summary = {
        "source_rows": len(rows),
        "available_no_image": len(no_image),
        "available_image": len(image),
        "selected_no_image_candidates": len(selected_no_image),
        "target_no_image_candidates": args.no_image_candidates,
        "selected_image_candidates": len(selected_image),
        "target_no_image": args.no_image,
        "target_image": args.image,
        "dropped": dropped,
        "batch1_rows": len(batch1),
        "dedupe": {
            "content_hash": len(seen_hash),
            "loose_key": len(seen_loose),
        },
    }
    (out_dir / "selection.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
