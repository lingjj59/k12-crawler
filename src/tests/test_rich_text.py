from __future__ import annotations

import json

from k12_crawler.harvest.unit import Unit
from k12_crawler.models import Paper
from k12_crawler.html_utils import (
    markup_to_html,
    rich_text_to_plain,
    split_option_html,
    strip_math_markup,
)
from k12_crawler.storage import Storage
from k12_crawler.validation import validate_question

ZHEJIANG_URL = "http://gzwl.cooco.net.cn/paper/1686470481/"


def _paper(site, html: str) -> Paper:
    unit = Unit(key=ZHEJIANG_URL, url=ZHEJIANG_URL, kind="paper")
    questions = site.parse(unit, html)
    return Paper(
        source="cooco",
        paper_id=unit.document_id,
        title=unit.document_title,
        url=ZHEJIANG_URL,
        subject=unit.meta["subject"],
        stage=unit.meta["stage"],
        page_kind=unit.meta["page_kind"],
        year=unit.meta.get("year"),
        region=unit.meta.get("region"),
        exam_type=unit.meta.get("exam_type"),
        questions=questions,
    )


def test_sup_sub_survive_in_normalized_stem_text(cooco_source, zhejiang_html):
    paper = _paper(cooco_source, zhejiang_html)
    q918 = next(q for q in paper.questions if q.source_question_id == "479918")
    assert "6.9×10^{11}" in q918.stem_text
    assert "<sup>" in q918.stem_html

    q920 = next(q for q in paper.questions if q.source_question_id == "479920")
    assert "1.2×10^{-23}" in q920.stem_text
    assert "6.6×10^{-34}" in q920.stem_text
    assert "<sup>" in q920.stem_html

    q921 = next(q for q in paper.questions if q.source_question_id == "479921")
    assert "x_{0}" in q921.stem_text
    assert "<sub>" in q921.stem_html


def test_option_html_keeps_sup_sub_and_placeholders(cooco_source, zhejiang_html):
    paper = _paper(cooco_source, zhejiang_html)
    q920 = next(q for q in paper.questions if q.source_question_id == "479920")
    assert q920.options[0].text.endswith("J")
    assert "^{-15}" in q920.options[0].text
    assert "<sup>-15</sup>" in (q920.options[0].html or "")
    assert "^{-11}" in q920.options[1].text
    assert "<sup>-11</sup>" in (q920.options[1].html or "")
    assert "[[IMG:1]]" in q920.stem_text
    assert all(option.html for option in q920.options)


def test_rich_text_helpers():
    assert rich_text_to_plain("<p>10<sup>-3</sup> m</p>") == "10^{-3} m"
    assert rich_text_to_plain("<p>R<sub>0</sub></p>") == "R_{0}"
    assert strip_math_markup("10^{-3} m") == "10-3 m"
    assert markup_to_html("x^{2} & y_{1}") == "x<sup>2</sup> &amp; y<sub>1</sub>"
    parts = split_option_html('<p>A．x<sup>2</sup></p><p>B．y</p>')
    assert [part[0] for part in parts] == ["A", "B"]
    assert "x^{2}" in parts[0][1]
    assert "<sup>2</sup>" in parts[0][2]


def test_export_keeps_rich_text_and_image_links(tmp_path, cooco_source, zhejiang_html):
    paper = _paper(cooco_source, zhejiang_html)
    selected = [
        q for q in paper.questions if q.source_question_id in ("479918", "479920")
    ]
    for question in selected:
        question.raw_html_path = "data/raw/live/paper_zhejiang_2022.html"
        question.raw_question_html_path = "data/raw/cooco/questions/479920.html"
        validate_question(question, check_files=False)
        assert question.validation_status == "pass"

    storage = Storage(tmp_path / "rich.sqlite")
    try:
        storage.save_paper(paper)
        for question in selected:
            status, _, _ = storage.save_question(question)
            assert status == "stored", question.source_question_id
        out = tmp_path / "questions.jsonl"
        assert storage.export_jsonl(out) == 2
    finally:
        storage.close()

    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    q920 = next(r for r in records if r["source_question_id"] == "479920")
    assert "1.2×10^{-23}" in q920["stem_text"]
    assert "<sup>" in q920["stem_html"]
    assert "<sup>-15</sup>" in q920["options"][0]["html"]
    assert q920["raw_html_path"] is not None
    assert q920["resources"]
    placeholders = {r["placeholder"] for r in q920["resources"]}
    assert "[[IMG:1]]" in placeholders
    assert "[[IMG:1]]" in q920["stem_text"]
    assert all(r["original_url"] for r in q920["resources"])
