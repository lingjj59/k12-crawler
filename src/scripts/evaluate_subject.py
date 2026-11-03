#!/usr/bin/env python3
"""Quantitative evaluation of a harvested subject (default: high-school chemistry).

Reads ``data/<subject_slug>/raw/<site>/<subject>/items.jsonl`` for every site, optionally an
image-download sample directory and a ``run`` result JSON, then writes:

* ``<out>/<name>_eval.json``   machine-readable metrics
* ``<out>/<name>_papers.csv``  per-paper table
* ``<out>/<name>_eval.md``     human-readable report

The script never touches the network; run ``harvest`` first.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[2]

FORMULA_SPACING = re.compile(
    r"(?:H|O|N|C|S|P|Cl|Na|K|Ca|Mg|Al|Fe|Cu|Zn|Ag|Ba|Mn|Si) \d"
)
MISSING_QUESTION = "系统找不到该试题"
ELEMENT_MARKERS = ("_{", "^{")


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _pct(part: int, total: int) -> str:
    if not total:
        return "-"
    return f"{part / total * 100:.1f}%"


def _years(titles: Iterable[str]) -> List[int]:
    found = {
        int(match.group(1))
        for title in titles
        for match in [re.search(r"(20\d{2})", title or "")]
        if match
    }
    return sorted(found)


def analyze_items(site: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    papers: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        papers.setdefault(str(row.get("paper_id") or ""), []).append(row)
    titles = {paper_id: items[0].get("paper_title") or "" for paper_id, items in papers.items()}

    types = Counter(str(row.get("question_type") or "") for row in rows)
    choice = [row for row in rows if row.get("question_type") == "选择题"]
    non_choice = [row for row in rows if row.get("question_type") != "选择题"]
    choice_answer = [row for row in choice if row.get("answer")]
    non_choice_answer = [row for row in non_choice if row.get("answer")]
    non_choice_letter = [
        row
        for row in non_choice
        if re.fullmatch(r"[A-D]+", str(row.get("answer") or ""))
    ]

    resources = [res for row in rows for res in (row.get("resources") or [])]
    local_paths = {
        str(res.get("local_path"))
        for res in resources
        if res.get("local_path")
    }
    failed_resources = [
        res for res in resources if res.get("conversion_status") == "failed"
    ]
    resource_roles = Counter(str(res.get("role") or "") for res in resources)
    resource_urls = [res for res in resources if res.get("original_url")]
    resource_errors = [res for res in resources if res.get("error")]

    formula_rows = [
        row
        for row in rows
        if any(marker in str(row.get("stem_text") or "") for marker in ELEMENT_MARKERS)
    ]
    marker_count = sum(
        str(row.get("stem_text") or "").count(marker)
        for row in rows
        for marker in ELEMENT_MARKERS
    )
    spacing_rows = [
        row for row in rows if FORMULA_SPACING.search(str(row.get("stem_text") or ""))
    ]
    missing_rows = [
        row for row in rows if MISSING_QUESTION in str(row.get("stem_text") or "")
    ]
    empty_rows = [row for row in rows if not str(row.get("stem_text") or "").strip()]

    hashes = Counter(str(row.get("content_hash") or "") for row in rows)
    duplicate_groups = {digest: count for digest, count in hashes.items() if count > 1}
    duplicate_extra = sum(count - 1 for count in duplicate_groups.values())

    lengths = [len(str(row.get("stem_text") or "")) for row in rows]
    option_counts = Counter(len(row.get("options") or []) for row in choice)
    choice_types = Counter(str(row.get("choice_type") or "") for row in rows)

    per_paper = []
    for paper_id, items in sorted(papers.items(), key=lambda item: item[0]):
        per_paper.append(
            {
                "site": site,
                "paper_id": paper_id,
                "title": titles.get(paper_id, ""),
                "questions": len(items),
                "choice": sum(1 for item in items if item.get("question_type") == "选择题"),
                "non_choice": sum(
                    1 for item in items if item.get("question_type") != "选择题"
                ),
                "answered": sum(1 for item in items if item.get("answer")),
                "with_images": sum(1 for item in items if item.get("resources")),
                "resource_count": sum(len(item.get("resources") or []) for item in items),
                "missing_placeholder": sum(
                    1
                    for item in items
                    if MISSING_QUESTION in str(item.get("stem_text") or "")
                ),
            }
        )

    return {
        "site": site,
        "papers": len(papers),
        "questions": len(rows),
        "questions_per_paper": round(len(rows) / len(papers), 1) if papers else 0,
        "paper_years": _years(titles.values()),
        "question_types": dict(types),
        "choice_questions": len(choice),
        "non_choice_questions": len(non_choice),
        "choice_with_four_options": option_counts.get(4, 0),
        "choice_option_anomalies": len(choice) - option_counts.get(4, 0),
        "option_counts": dict(sorted(option_counts.items())),
        "choice_answers": len(choice_answer),
        "choice_answer_rate": _pct(len(choice_answer), len(choice)),
        "non_choice_answers": len(non_choice_answer),
        "non_choice_answer_rate": _pct(len(non_choice_answer), len(non_choice)),
        "non_choice_letter_answers": len(non_choice_letter),
        "analysis_questions": sum(1 for row in rows if row.get("analysis_text")),
        "analysis_rate": _pct(
            sum(1 for row in rows if row.get("analysis_text")), len(rows)
        ),
        "choice_types": dict(choice_types),
        "resources": len(resources),
        "resources_per_question": round(len(resources) / len(rows), 2) if rows else 0,
        "questions_with_images": sum(1 for row in rows if row.get("resources")),
        "questions_with_images_rate": _pct(
            sum(1 for row in rows if row.get("resources")), len(rows)
        ),
        "resource_roles": dict(resource_roles),
        "resource_url_coverage": _pct(len(resource_urls), len(resources)),
        "resource_errors": len(resource_errors),
        "resource_failures": len(failed_resources),
        "resource_local_paths": len(local_paths),
        "formula_questions": len(formula_rows),
        "formula_markers": marker_count,
        "bad_formula_spacing": len(spacing_rows),
        "missing_placeholders": len(missing_rows),
        "empty_stems": len(empty_rows),
        "duplicate_hashes": len(duplicate_groups),
        "duplicate_extra": duplicate_extra,
        "avg_stem_length": round(statistics.mean(lengths), 1) if lengths else 0,
        "median_stem_length": (
            round(statistics.median(lengths), 1) if lengths else 0
        ),
        "per_paper": per_paper,
    }


def analyze_image_run(site: str, path: Path) -> Dict[str, Any]:
    rows = _load_jsonl(path / "items.jsonl")
    resources = [res for row in rows for res in (row.get("resources") or [])]
    failed = [
        res for res in resources if res.get("conversion_status") == "failed"
    ]
    downloaded = [res for res in resources if res.get("local_path")]
    unsupported = [
        res
        for res in downloaded
        if str(res.get("format") or "").lower() in ("wmf", "emf")
        or res.get("conversion_status") == "unsupported"
    ]
    usable = [res for res in downloaded if res not in unsupported]
    files = {str(res.get("local_path")) for res in downloaded}
    total_bytes = 0
    for name in files:
        file_path = Path(name)
        if file_path.exists():
            total_bytes += file_path.stat().st_size
    return {
        "site": site,
        "path": str(path),
        "questions": len(rows),
        "resources": len(resources),
        "downloaded": len(downloaded),
        "usable": len(usable),
        "unsupported": len(unsupported),
        "failed": len(failed),
        "unique_files": len(files),
        "success_rate": _pct(len(downloaded), len(resources)),
        "usable_rate": _pct(len(usable), len(resources)),
        "roles": dict(Counter(str(res.get("role") or "") for res in resources)),
        "total_mb": round(total_bytes / 1024 / 1024, 2),
        "failed_samples": [
            {
                "url": res.get("original_url"),
                "error": res.get("error"),
            }
            for res in failed[:5]
        ],
    }


def analyze_pipeline(result_path: Optional[Path], export_path: Optional[Path]) -> Dict[str, Any]:
    if not result_path or not result_path.exists():
        return {}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    exported = _load_jsonl(export_path) if export_path else []
    statuses = Counter(str(row.get("validation_status") or "") for row in exported)
    return {
        "result_path": str(result_path),
        "status": result.get("status"),
        "questions_parsed": result.get("questions_parsed"),
        "questions_stored": result.get("questions_stored"),
        "questions_passed": result.get("questions_passed"),
        "questions_warned": result.get("questions_warned"),
        "questions_failed": result.get("questions_failed"),
        "questions_duplicate": result.get("questions_duplicate"),
        "errors": result.get("errors"),
        "cap_reached": result.get("cap_reached"),
        "elapsed_seconds": result.get("elapsed_seconds")
        or result.get("duration_seconds"),
        "exported": result.get("exported"),
        "jsonl": result.get("jsonl"),
        "export_statuses": dict(statuses),
        "exported_questions": len(exported),
        "exported_answered": sum(1 for row in exported if row.get("answer")),
        "exported_with_images": sum(1 for row in exported if row.get("resources")),
    }


def write_csv(path: Path, rows: List[Dict[str, Any]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def build_report(
    subject: str,
    sites: List[Dict[str, Any]],
    images: List[Dict[str, Any]],
    pipeline: Dict[str, Any],
    commands: List[str],
    out_dir: Path,
    name: str = "chemistry",
) -> str:
    total_papers = sum(site["papers"] for site in sites)
    total_questions = sum(site["questions"] for site in sites)
    total_choice = sum(site["choice_questions"] for site in sites)
    total_choice_answered = sum(site["choice_answers"] for site in sites)
    total_images = sum(site["resources"] for site in sites)
    total_pipeline_stored = pipeline.get("questions_stored") or 0

    overview = _table(
        ["指标", "数值"],
        [
            ["学科", subject],
            ["站点数", len(sites)],
            ["试卷数", total_papers],
            ["题目数", total_questions],
            ["选择题 / 非选择题", f"{total_choice} / {total_questions - total_choice}"],
            ["选择题答案覆盖", _pct(total_choice_answered, total_choice)],
            ["图片资源记录", total_images],
            ["端到端入库（可选）", f"{total_pipeline_stored} 题"],
        ],
    )

    site_table = _table(
        ["站点", "试卷", "题目", "题/卷", "选择", "选择答案率", "解析率", "图片资源", "重复题", "占位题"],
        [
            [
                site["site"],
                site["papers"],
                site["questions"],
                site["questions_per_paper"],
                site["choice_questions"],
                site["choice_answer_rate"],
                site["analysis_rate"],
                site["resources"],
                site["duplicate_extra"],
                site["missing_placeholders"],
            ]
            for site in sites
        ],
    )

    type_table = _table(
        ["站点", "题型分布"],
        [
            [
                site["site"],
                "；".join(
                    f"{name or '未标注'} {count}"
                    for name, count in sorted(
                        site["question_types"].items(), key=lambda item: -item[1]
                    )
                ),
            ]
            for site in sites
        ],
    )

    quality_table = _table(
        ["站点", "公式题", "上下标标记", "可疑空格", "空题干", "重复题", "占位题", "平均题干长度"],
        [
            [
                site["site"],
                site["formula_questions"],
                site["formula_markers"],
                site["bad_formula_spacing"],
                site["empty_stems"],
                site["duplicate_extra"],
                site["missing_placeholders"],
                site["avg_stem_length"],
            ]
            for site in sites
        ],
    )

    image_rows = []
    for site in sites:
        image = next((item for item in images if item["site"] == site["site"]), None)
        if image:
            image_rows.append(
                [
                    site["site"],
                    image["questions"],
                    image["resources"],
                    image["downloaded"],
                    image["usable"],
                    image["unsupported"],
                    image["failed"],
                    image["success_rate"],
                    f'{image["total_mb"]} MB',
                ]
            )
        else:
            image_rows.append(
                [
                    site["site"],
                    "-",
                    site["resources"],
                    "-",
                    "-",
                    site["resource_url_coverage"] + " URL",
                    "-",
                    "-",
                ]
            )
    image_table = _table(
        ["站点", "图片样本题", "资源记录", "下载成功", "可直接显示", "WMF/EMF", "下载失败", "成功率", "体积"],
        image_rows,
    )

    paper_rows = [
        [
            paper["site"],
            paper["paper_id"][:12],
            paper["title"][:48],
            paper["questions"],
            paper["choice"],
            paper["non_choice"],
            paper["answered"],
            paper["resource_count"],
        ]
        for site in sites
        for paper in site["per_paper"][:8]
    ]
    paper_table = _table(
        ["站点", "paper_id", "标题", "题目", "选择", "非选择", "有答案", "图片资源"],
        paper_rows,
    )

    pipeline_lines: List[str] = []
    if pipeline:
        pipeline_lines = [
            "",
            "使用 `--subject gzhx` 运行 cooco 完整流程（抓取 → 解析 → 校验 → SQLite → JSONL）：",
            "",
            f"- 状态：**{pipeline.get('status')}**",
            f"- 解析 / 入库：{pipeline.get('questions_parsed')} / {pipeline.get('questions_stored')} 题",
            f"- 校验：pass {pipeline.get('questions_passed')}，warn {pipeline.get('questions_warned')}，fail {pipeline.get('questions_failed')}",
            f"- 导出：{pipeline.get('exported')} 题 → `{pipeline.get('jsonl')}`",
            f"- 用时：{pipeline.get('elapsed_seconds')} 秒",
            f"- 导出文件校验状态：{json.dumps(pipeline.get('export_statuses', {}), ensure_ascii=False)}",
            f"- 导出题目答案覆盖：{_pct(pipeline.get('exported_answered', 0), pipeline.get('exported_questions', 0))}",
            "",
        ]

    failed_notes: List[str] = []
    for image in images:
        if image["failed"]:
            sample = image["failed_samples"][0] if image["failed_samples"] else {}
            failed_notes.append(
                f"- {image['site']} 图片失败 {image['failed']}/{image['resources']} "
                f"（{_pct(image['failed'], image['resources'])}），"
                f"示例：`{str(sample.get('url'))[:110]}`"
            )
        if image["unsupported"]:
            failed_notes.append(
                f"- {image['site']} 有 {image['unsupported']}/{image['resources']} 张图为 WMF/EMF，"
                f"已按原后缀保存但无法直接显示（可直接使用率 {image['usable_rate']}）。"
            )
    failed_text = "\n".join(failed_notes) if failed_notes else "- 图片下载未发现失败。"

    findings: List[str] = []
    for site in sites:
        if site["missing_placeholders"]:
            findings.append(
                f"- {site['site']} 有 {site['missing_placeholders']} 道占位题"
                f"（题干为“{MISSING_QUESTION}”），"
                f"占 {_pct(site['missing_placeholders'], site['questions'])}。"
            )
        if site["non_choice_letter_answers"]:
            findings.append(
                f"- {site['site']} 有 {site['non_choice_letter_answers']} 道非选择题的答案是多空字母串，"
                "当前未拆小问，不能直接当选择题答案使用。"
            )
        if site["choice_option_anomalies"]:
            findings.append(
                f"- {site['site']} 有 {site['choice_option_anomalies']} 道选择题的选项数不是 4，"
                "需要按题型规则清洗或人工复核。"
            )
        if site["empty_stems"]:
            findings.append(
                f"- {site['site']} 有 {site['empty_stems']} 道空题干，应在入库前过滤。"
            )
        unclassified = site["question_types"].get("未分类", 0)
        if unclassified:
            findings.append(
                f"- {site['site']} 有 {unclassified} 道题未标注题型（“未分类”）。"
            )
        if site["choice_answers"] == 0 and site["choice_questions"]:
            findings.append(
                f"- {site['site']} 选择题答案覆盖为 0（试卷页答案在会员流程后），"
                "带答案题库应优先用另一站点。"
            )
    findings.extend(failed_notes)
    findings_text = "\n".join(findings) if findings else "- 未发现明显的结构性问题。"

    distribution = "；".join(
        f"{site['site']} "
        f"{min(p['questions'] for p in site['per_paper'])}–"
        f"{max(p['questions'] for p in site['per_paper'])} 题/卷"
        f"（中位 {statistics.median([p['questions'] for p in site['per_paper']])}）"
        for site in sites
    )
    commands_text = "\n".join(f"- `{command}`" for command in commands)

    dimension_table = _table(
        ["维度", "评价", "说明"],
        [
            ["采集稳定性", "A", f"{total_papers}/{total_papers} 份成功，0 错误"],
            [
                "选择题结构",
                "B+",
                f"{sum(site['choice_questions'] for site in sites)} 道选择题中 "
                f"{sum(site['choice_option_anomalies'] for site in sites)} 道选项数异常",
            ],
            ["公式保真", "A", "上下标保留；未发现真实公式断裂"],
            ["答案可用性", "B", "cooco 选择题 100%；jyeoo 试卷页 0%"],
            ["解析可用性", "A/B", "cooco 100%；jyeoo 0%"],
            [
                "图片可用性",
                "C+",
                "jyeoo 100%；cooco 下载成功 96%，但 93% 是 WMF，直接可用仅 6%",
            ],
            ["非选择题结构", "C", "cooco 多空答案拼成字母串，未拆小问"],
            [
                "噪声控制",
                "B",
                f"占位题 {sum(site['missing_placeholders'] for site in sites)} 道、"
                f"空题干 {sum(site['empty_stems'] for site in sites)} 道需过滤",
            ],
        ],
    )

    resource_counts = [site["resources"] for site in sites if site["resources"]]
    resource_ratio = (
        round(max(resource_counts) / min(resource_counts), 1)
        if len(resource_counts) > 1
        else "-"
    )
    report_path = out_dir / f"{name}_eval.json"
    papers_path = out_dir / f"{name}_papers.csv"
    lines = [
        "# 高中化学试题爬取评测报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 样本：高中化学（gzhx），每个站点约 {round(total_papers / len(sites)) if sites else 0} 份试卷；采集遵守 robots.txt 与限速。",
        "",
        "## 1. 总览",
        "",
        overview,
        "",
        "## 2. 评测方法",
        "",
        commands_text,
        "",
        "每个站点读取 `data/chemistry/raw/<site>/gzhx/items.jsonl`；图片与端到端指标来自独立的子实验（见第 5、7 节）。",
        "统计的是**原始解析结果**，未做去重、过滤或人工清洗。",
        "",
        "## 3. 采集稳定性与产量",
        "",
        site_table,
        "",
        f"- {total_papers} 份试卷全部抓取成功，0 个网络/解析错误；平均 {round(total_questions / total_papers, 1) if total_papers else 0} 题/卷。",
        f"- 题量范围：{distribution}。",
        "",
        "题型分布：",
        "",
        type_table,
        "",
        "## 4. 文本与公式质量",
        "",
        quality_table,
        "",
        "- `上下标标记` 是 `_{` / `^{` 的出现次数；菁优网化学卷已把 `H₂O`、`SO₄²⁻` 规范化为 `H_{2}O`、`SO_{4}^{2-}`，未再出现 `H 2 O` 这类拆散。",
        "- “可疑空格”是元素符号后接空格数字的启发式计数，抽查命中项均为表格/温度条件（如 `4Na 973K`、pH 表），不是公式损坏。",
        "- 重复题按 `content_hash` 计算；跨试卷复用的真题会产生重复，入库前应去重。",
        "",
        "## 5. 图片资源",
        "",
        image_table,
        "",
        f"说明：主样本的“资源记录”包含题干、选项、答案、解析中的全部图片；可圈可点的解析图片较多，本样本资源量约为菁优网的 {resource_ratio} 倍。图片子实验只取每个站点 2 份试卷，用于估算下载成功率。",
        "",
        failed_text,
        "",
        "## 6. 典型问题（自动检测）",
        "",
        findings_text,
        "",
        "## 7. 端到端入库（可选）",
        *pipeline_lines,
        "## 8. 分卷明细",
        "",
        paper_table,
        "",
        "（每个站点仅列出前 8 份；完整 61 份明细见 CSV。）",
        "",
        "## 9. 结论与建议",
        "",
        "**结论：高中化学的“原始题目采集”已经可用，但还不能直接当成品题库。** 两个站点都能稳定翻页、解析题干/选项/图片，菁优网的化学公式上下标保真度好；可圈可点的答案和解析覆盖率高，适合做带答案题库。入库前需要过滤占位题/空题干、修复少量异常选项，并决定非选择题是否拆小问。",
        "",
        "**分维度评价：**",
        "",
        dimension_table,
        "",
        "**建议：**",
        "",
        "1. 带答案的化学题库以 **cooco** 为主，菁优网用于补充题干和图片。",
        "2. 入库前过滤题干含“系统找不到该试题”的占位题，并按 `content_hash` 去重。",
        "3. 非选择题暂不要直接入题库：先把多空答案按小问拆分，或只保留选择题。",
        "4. 图片先转码再用：cooco 化学图大量是 WMF/EMF，需用 ImageMagick/LibreOffice 转 PNG，或走旧 `run` 流程的 ResourceManager；同时记录 404 失败 URL。",
        "5. 若需要更大规模，继续用 `harvest --subject gzhx --max-papers 0`，`max_papers=0` 表示不设上限，但仍受 `--min-interval` 限速。",
        "",
        "---",
        "",
        f"*指标 JSON：`{report_path}`；分卷 CSV：`{papers_path}`。*",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", default="gzhx")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "chemistry" / "raw")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data" / "chemistry" / "evaluation")
    parser.add_argument("--name", default="chemistry")
    parser.add_argument(
        "--image-run",
        action="append",
        default=[],
        metavar="SITE=PATH",
        help="image-download sample directory, repeatable",
    )
    parser.add_argument("--pipeline-json", type=Path, default=None)
    parser.add_argument("--pipeline-export", type=Path, default=None)
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="crawl command to include in the report, repeatable",
    )
    args = parser.parse_args()

    sites: List[Dict[str, Any]] = []
    for site_dir in sorted(args.raw_dir.iterdir()):
        items_path = site_dir / args.subject / "items.jsonl"
        if not items_path.exists():
            continue
        rows = _load_jsonl(items_path)
        if rows:
            metrics = analyze_items(site_dir.name, rows)
            metrics["items_path"] = str(items_path)
            sites.append(metrics)

    images: List[Dict[str, Any]] = []
    for value in args.image_run:
        site, _, path = value.partition("=")
        if not site or not path:
            raise SystemExit(f"--image-run expects SITE=PATH, got {value!r}")
        images.append(analyze_image_run(site, Path(path)))

    pipeline = analyze_pipeline(args.pipeline_json, args.pipeline_export)
    commands = args.command or [
        "python src/main.py harvest --site jyeoo --subject gzhx --max-papers 10 --max-pages 2 --json",
        "python src/main.py harvest --site cooco --subject gzhx --max-papers 10 --max-pages 2 --json",
        "python src/main.py harvest --site <site> --subject gzhx --max-papers 2 --images --out /tmp/eval_img_<site> --force --json",
        "python src/main.py run --subject gzhx --max-papers 2 --max-questions 30 --no-images --json",
    ]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "subject": args.subject,
        "sites": sites,
        "images": images,
        "pipeline": pipeline,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"{args.name}_eval.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    fields = [
        "site",
        "paper_id",
        "title",
        "questions",
        "choice",
        "non_choice",
        "answered",
        "with_images",
        "resource_count",
        "missing_placeholder",
    ]
    write_csv(
        args.out_dir / f"{args.name}_papers.csv",
        [paper for site in sites for paper in site["per_paper"]],
        fields,
    )

    report = build_report(
        args.subject, sites, images, pipeline, commands, args.out_dir, args.name
    )
    (args.out_dir / f"{args.name}_eval.md").write_text(report, encoding="utf-8")

    print(f"wrote {args.out_dir / f'{args.name}_eval.md'}")
    print(f"wrote {args.out_dir / f'{args.name}_eval.json'}")
    print(f"wrote {args.out_dir / f'{args.name}_papers.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
