"""Basic, explicit validation.  Problems are recorded, never silently dropped."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

from .config import PROJECT_ROOT
from .html_utils import contains_html_residue, looks_mojibake
from .models import Question, ValidationIssue


def _issue(code: str, severity: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, message=message)


def _file_exists(path_value: str) -> bool:
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.exists() and path.stat().st_size > 0


def validate_question(question: Question, check_files: bool = True) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if not question.stem_text.strip():
        issues.append(_issue("STEM_EMPTY", "fail", "题干为空"))
    if len(question.options) < 2 and question.question_type == "选择题":
        issues.append(_issue("OPTIONS_MISSING", "fail", "选择题选项少于 2 个"))
    if question.question_type == "选择题" and not question.answer:
        issues.append(_issue("ANSWER_MISSING", "fail", "未提取到答案"))
    if question.answer and question.options:
        labels = {option.label for option in question.options}
        invalid = [letter for letter in question.answer if letter not in labels]
        if invalid:
            issues.append(
                _issue(
                    "ANSWER_NOT_IN_OPTIONS",
                    "fail",
                    f"答案 {question.answer} 含不在选项中的字母: {''.join(invalid)}",
                )
            )
    if question.choice_type == "unknown" and question.question_type == "选择题":
        issues.append(_issue("CHOICE_TYPE_UNKNOWN", "warn", "无法判定单选/多选"))
    if not question.analysis_text:
        issues.append(_issue("ANALYSIS_MISSING", "warn", "原网页未提供可提取的解析"))
    if contains_html_residue(question.stem_text):
        issues.append(_issue("HTML_RESIDUE_STEM", "warn", "题干文本含 HTML 残留"))
    if question.analysis_text and contains_html_residue(question.analysis_text):
        issues.append(_issue("HTML_RESIDUE_ANALYSIS", "warn", "解析文本含 HTML 残留"))
    if looks_mojibake(question.stem_text) or (
        question.analysis_text and looks_mojibake(question.analysis_text)
    ):
        issues.append(_issue("MOJIBAKE", "fail", "文本疑似乱码"))
    if question.question_type == "选择题" and question.options:
        labels = [option.label for option in question.options]
        if labels != sorted(labels):
            issues.append(_issue("OPTION_ORDER", "warn", f"选项顺序异常: {labels}"))

    placeholders = set(re.findall(r"\[\[IMG:(\d+)\]\]", question.stem_text + "".join(o.text for o in question.options) + (question.analysis_text or "")))
    resource_ordinals = {str(resource.ordinal) for resource in question.resources}
    missing_placeholders = placeholders - resource_ordinals
    if missing_placeholders:
        issues.append(
            _issue(
                "PLACEHOLDER_MISSING_RESOURCE",
                "fail",
                f"文本占位符无对应资源: {sorted(missing_placeholders)}",
            )
        )

    for resource in question.resources:
        if resource.conversion_status == "skipped":
            continue
        if resource.conversion_status in ("download_failed", "missing_url") or (
            resource.error and resource.conversion_status != "unsupported"
        ):
            issues.append(
                _issue(
                    "RESOURCE_MISSING",
                    "fail",
                    f"资源 {resource.ordinal} 获取失败: {resource.error or resource.conversion_status}",
                )
            )
            continue
        if check_files and resource.local_path and not _file_exists(resource.local_path):
            issues.append(
                _issue(
                    "RESOURCE_FILE_NOT_FOUND",
                    "fail",
                    f"资源文件不存在: {resource.local_path}",
                )
            )
        if resource.conversion_status == "unsupported":
            issues.append(
                _issue(
                    "FORMULA_CONVERSION_UNSUPPORTED",
                    "warn",
                    f"资源 {resource.ordinal} 为真实 WMF/EMF，未转换；已保留原文件",
                )
            )

    question.validation_issues = issues
    if any(issue.severity == "fail" for issue in issues):
        question.validation_status = "fail"
    elif issues:
        question.validation_status = "warn"
    else:
        question.validation_status = "pass"
    return issues


def summarize_validation(questions: Iterable[Question]) -> dict:
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for question in questions:
        counts[question.validation_status] = counts.get(question.validation_status, 0) + 1
    return counts
