"""Tests for the offline subject evaluation helper."""

from __future__ import annotations

from scripts.evaluate_subject import analyze_items, analyze_pipeline


def _row(**overrides):
    row = {
        "site": "cooco",
        "paper_id": "p1",
        "paper_title": "2022年测试化学试卷",
        "question_type": "选择题",
        "choice_type": "single",
        "stem_text": "H_{2}O 的摩尔质量是（ ）",
        "stem_html": "<p>H<sub>2</sub>O 的摩尔质量是（ ）</p>",
        "options": [
            {"label": "A", "text": "18", "html": "<label>A．18</label>"},
            {"label": "B", "text": "20", "html": "<label>B．20</label>"},
            {"label": "C", "text": "22", "html": "<label>C．22</label>"},
            {"label": "D", "text": "24", "html": "<label>D．24</label>"},
        ],
        "answer": "A",
        "analysis_text": "解析",
        "content_hash": "h1",
        "resources": [],
    }
    row.update(overrides)
    return row


def test_analyze_items_computes_chemistry_metrics():
    rows = [
        _row(),
        _row(
            question_type="实验,探究题",
            choice_type="unknown",
            answer="ADCB",
            content_hash="h2",
            paper_id="p2",
            paper_title="2022年测试化学试卷二",
            resources=[
                {
                    "ordinal": 1,
                    "role": "stem",
                    "original_url": "http://example.test/a.png",
                    "local_path": None,
                    "conversion_status": "pending",
                }
            ],
        ),
        _row(
            stem_text="系统找不到该试题",
            answer="",
            content_hash="h3",
            paper_id="p2",
            paper_title="2022年测试化学试卷二",
        ),
    ]

    metrics = analyze_items("cooco", rows)
    assert metrics["papers"] == 2
    assert metrics["questions"] == 3
    assert metrics["choice_questions"] == 2
    assert metrics["choice_answers"] == 1
    assert metrics["choice_answer_rate"] == "50.0%"
    assert metrics["non_choice_questions"] == 1
    assert metrics["non_choice_letter_answers"] == 1
    assert metrics["missing_placeholders"] == 1
    assert metrics["formula_questions"] == 2
    assert metrics["resources"] == 1
    assert metrics["questions_with_images"] == 1
    assert metrics["per_paper"][0]["questions"] == 1


def test_analyze_pipeline_reports_validation_and_export(tmp_path):
    result = tmp_path / "run.json"
    result.write_text(
        '{"status":"completed","questions_parsed":10,"questions_stored":8,'
        '"questions_passed":7,"questions_warned":1,"questions_failed":0,'
        '"exported":8,"duration_seconds":3.2}',
        encoding="utf-8",
    )
    export = tmp_path / "export.jsonl"
    export.write_text(
        "\n".join(
            [
                '{"validation_status":"pass","answer":"A","resources":[]}',
                '{"validation_status":"warn","answer":"B","resources":[{"ordinal":1}]}',
            ]
        ),
        encoding="utf-8",
    )

    metrics = analyze_pipeline(result, export)
    assert metrics["status"] == "completed"
    assert metrics["elapsed_seconds"] == 3.2
    assert metrics["exported_questions"] == 2
    assert metrics["export_statuses"] == {"pass": 1, "warn": 1}
    assert metrics["exported_answered"] == 2
