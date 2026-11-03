from __future__ import annotations

from k12_crawler.models import Resource
from k12_crawler.validation import summarize_validation, validate_question


def test_valid_question_passes(make_question):
    question = make_question()
    issues = validate_question(question, check_files=False)
    assert issues == []
    assert question.validation_status == "pass"


def test_missing_answer_fails(make_question):
    question = make_question(answer="")
    issues = validate_question(question, check_files=False)
    assert any(issue.code == "ANSWER_MISSING" for issue in issues)
    assert question.validation_status == "fail"


def test_answer_must_match_options(make_question):
    question = make_question(answer="Z")
    issues = validate_question(question, check_files=False)
    assert any(issue.code == "ANSWER_NOT_IN_OPTIONS" for issue in issues)
    assert question.validation_status == "fail"


def test_missing_analysis_is_warning(make_question):
    question = make_question(analysis="")
    issues = validate_question(question, check_files=False)
    assert any(issue.code == "ANALYSIS_MISSING" for issue in issues)
    assert question.validation_status == "warn"


def test_empty_stem_fails(make_question):
    question = make_question(stem="")
    issues = validate_question(question, check_files=False)
    assert any(issue.code == "STEM_EMPTY" for issue in issues)
    assert question.validation_status == "fail"


def test_resource_failure_fails(make_question):
    question = make_question()
    question.resources = [
        Resource(
            ordinal=1,
            role="stem",
            original_url="http://example.test/x.png",
            placeholder="[[IMG:1]]",
            conversion_status="download_failed",
            error="timeout",
        )
    ]
    issues = validate_question(question, check_files=False)
    assert any(issue.code == "RESOURCE_MISSING" for issue in issues)
    assert question.validation_status == "fail"


def test_placeholder_without_resource_fails(make_question):
    question = make_question(stem="看图 [[IMG:9]]")
    issues = validate_question(question, check_files=False)
    assert any(issue.code == "PLACEHOLDER_MISSING_RESOURCE" for issue in issues)


def test_summary_counts(make_question):
    a = make_question(qid="a")
    b = make_question(qid="b", answer="")
    for q in (a, b):
        validate_question(q, check_files=False)
    assert summarize_validation([a, b]) == {"pass": 1, "warn": 0, "fail": 1}


def test_non_choice_answer_is_not_checked_against_options(make_question):
    question = make_question(answer="ADCB")
    question.question_type = "实验,探究题"
    question.options = []
    question.choice_type = "unknown"
    issues = validate_question(question, check_files=False)
    codes = {issue.code for issue in issues}
    assert "ANSWER_NOT_IN_OPTIONS" not in codes
    assert "ANSWER_MISSING" not in codes


def test_chemistry_less_than_is_not_html_residue(make_question):
    question = make_question(analysis="原子半径：P<S<O，另一组 P>S<O。")
    issues = validate_question(question, check_files=False)
    assert not any(issue.code.startswith("HTML_RESIDUE") for issue in issues)
