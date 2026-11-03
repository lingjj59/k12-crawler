from __future__ import annotations

from k12_crawler.harvest.unit import Unit
from k12_crawler.html_utils import content_hash, split_options


def _parse(site, html, url):
    unit = Unit(key="test", url=url, kind="paper")
    return unit, site.parse(unit, html)


ZHEJIANG_URL = "http://gzwl.cooco.net.cn/paper/1686470481/"
SHANDONG_URL = "http://gzwl.cooco.net.cn/paper/1688525155/"
DETAIL_URL = "http://gzwl.cooco.net.cn/testdetail/470797/"


def test_paper_counts_and_metadata(cooco_source, zhejiang_html):
    paper, questions = _parse(cooco_source, zhejiang_html, ZHEJIANG_URL)
    assert paper.document_id == "1686470481"
    assert paper.meta["page_kind"] == "paper"
    assert paper.meta["subject"] == "物理"
    assert paper.meta["stage"] == "高中"
    assert paper.meta["year"] == 2022
    assert paper.meta["region"] == "浙江"
    assert len(questions) == 23
    assert sum(1 for q in questions if q.question_type == "选择题") == 16


def test_single_paragraph_options(cooco_source, zhejiang_html):
    paper, questions = _parse(cooco_source, zhejiang_html, ZHEJIANG_URL)
    question = next(q for q in questions if q.source_question_id == "479905")
    assert question.answer == "A"
    assert question.choice_type == "single"
    assert [o.label for o in question.options] == ["A", "B", "C", "D"]
    assert question.options[0].text == "力"
    assert question.options[3].text == "电场强度"
    assert "【详解】" not in (question.analysis_text or "")
    assert "故选" in (question.analysis_text or "")
    assert question.stem_text == "单位为 J/m 的物理量是（ ）"
    assert len(question.resources) == 3
    assert all(r.role == "analysis" for r in question.resources)


def test_multiple_paragraph_options(cooco_source, zhejiang_html):
    paper, questions = _parse(cooco_source, zhejiang_html, ZHEJIANG_URL)
    question = next(q for q in questions if q.source_question_id == "479906")
    assert question.answer == "B"
    assert len(question.options) == 4
    assert question.options[0].text.startswith("研究甲图中排球")
    assert "[[IMG:1]]" in question.stem_text


def test_image_options_have_placeholders_and_labels(cooco_source, zhejiang_html):
    paper, questions = _parse(cooco_source, zhejiang_html, ZHEJIANG_URL)
    question = next(q for q in questions if q.source_question_id == "479907")
    assert question.answer == "B"
    assert [o.text for o in question.options] == [
        "[[IMG:2]]",
        "[[IMG:3]]",
        "[[IMG:4]]",
        "[[IMG:5]]",
    ]
    option_resources = [r for r in question.resources if r.role == "option"]
    assert [r.option_label for r in option_resources] == ["A", "B", "C", "D"]
    assert question.resources[0].role == "stem"


def test_multiple_select_answer(cooco_source, zhejiang_html):
    paper, questions = _parse(cooco_source, zhejiang_html, ZHEJIANG_URL)
    question = next(q for q in questions if q.source_question_id == "479918")
    assert question.answer == "CD"
    assert question.choice_type == "multiple"
    assert any(r.option_label == "D" for r in question.resources)


def test_shandong_image_only_options(cooco_source, shandong_html):
    paper, questions = _parse(cooco_source, shandong_html, SHANDONG_URL)
    question = next(q for q in questions if q.source_question_id == "480081")
    assert question.answer == "B"
    assert [o.text for o in question.options] == [
        "[[IMG:2]]",
        "[[IMG:3]]",
        "[[IMG:4]]",
        "[[IMG:5]]",
    ]
    assert len(question.resources) == 8


def test_detail_page_only_main_question(cooco_source, detail_html):
    paper, questions = _parse(cooco_source, detail_html, DETAIL_URL)
    assert paper.meta["page_kind"] == "detail"
    assert len(questions) == 1
    question = questions[0]
    assert question.source_question_id == "470797"
    assert question.answer == "BCD"
    assert question.choice_type == "multiple"
    assert len(question.options) == 4
    assert "【分析】" not in (question.analysis_text or "")


def test_list_discovery(cooco_source):
    from pathlib import Path

    html = (Path(__file__).resolve().parent / "fixtures" / "live" / "list_p1.html").read_text(encoding="utf-8")
    unit = Unit(
        key="list/1",
        url="http://pro.cooco.net.cn/exam_list_new/gzwl/?page=1",
        kind="list",
        meta={"page": 1},
    )
    refs = list(cooco_source.expand(unit, html))
    papers = [ref for ref in refs if ref.kind == "paper"]
    assert len(papers) == 20
    assert papers[0].document_id == "1688525155"
    assert any(ref.key == "list/2" for ref in refs)
    assert cooco_source.list_url(unit.url, 2).endswith("?page=2")


def test_split_options_rejects_prose():
    assert split_options("A、B 两点中，A 点电势较高") == []


def test_content_hash_stable():
    options = [("A", "1"), ("B", "2")]
    assert content_hash("题干", options, "A") == content_hash("题干", options, "A")
    assert content_hash("题干", options, "A") != content_hash("题干", options, "B")
