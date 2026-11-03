from __future__ import annotations

import json

from k12_crawler.harvest.registry import build_site
from k12_crawler.harvest.sites.baidu import api_url, parse_chapterlist
from k12_crawler.harvest.unit import Unit
from k12_crawler.subjects import detect_subject, resolve_subject, seed_url

CHAPTERLIST = """
<html><body>
<div class="detail-chapter">
  <div class="detail-chapter-title"><h3>第一章 力学</h3></div>
  <div class="detail-kpoint-1">
    <h4>1.1 机械运动</h4>
    <a href="/tikupc/chapterdetail/aaaa1111-1-15-knowpoint">机械运动</a>
    <a href="/tikupc/chapterdetail/bbbb2222-1-15-knowpoint">参考系</a>
  </div>
</div>
</body></html>
"""


def _bdjson(**overrides):
    data = {
        "que_stem": [{"t": "p", "c": "关于参考系，下列说法正确的是"}],
        "que_options": [
            [
                {"ret": [{"t": "p", "c": "甲"}], "yes": False},
                {"ret": [{"t": "p", "c": "乙"}], "yes": True},
                {"ret": [{"t": "p", "c": "丙"}], "yes": False},
                {"ret": [{"t": "p", "c": "丁"}], "yes": False},
            ]
        ],
        "que_info": {"type": "sc", "analysis": "乙正确。", "know_point": ["参考系"]},
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def _payload(questions):
    return json.dumps(
        {
            "status": {"code": 0, "msg": ""},
            "data": {
                "question": questions,
                "kp_info": {"que_num": len(questions), "kp_id": "aaaa1111"},
            },
        },
        ensure_ascii=False,
    )


def _site():
    return build_site("baidu", resolve_subject("baidu", "gzwl"))


def test_seed_and_subject_detection():
    subject = resolve_subject("baidu", "gzhx")
    assert subject.name == "化学"
    assert "-19-knowpoint-11" in seed_url("baidu", subject)
    assert detect_subject(
        "baidu", "https://tiku.baidu.com/tikupc/chapterlist/x-19-knowpoint-11"
    ).code == "gzhx"


def test_chapterlist_is_scoped_to_chapters():
    rows = parse_chapterlist(CHAPTERLIST)
    assert [row["kp_id"] for row in rows] == ["aaaa1111", "bbbb2222"]
    assert rows[0]["kp_name"] == "机械运动"
    assert rows[0]["chapter"] == "第一章 力学"


def test_chapterlist_expands_to_api_pages():
    site = _site()
    seed = site.seed_unit(site.seed_for(site.subject))
    children = list(site.expand(seed, CHAPTERLIST))
    assert [child.kind for child in children] == ["api_page", "api_page"]
    assert children[0].key == "kp/aaaa1111/start=1"
    assert children[0].document_title == "机械运动"
    assert children[0].suffix == ".json"
    assert "start=1" in children[0].url


def test_process_chapterlist_never_parses_html():
    site = _site()
    seed = site.seed_unit(site.seed_for(site.subject))

    result = site.process(seed, CHAPTERLIST)

    assert result.questions == []
    assert [child.kind for child in result.children] == ["api_page", "api_page"]


def test_process_api_page_returns_questions_and_next_page():
    site = _site()
    unit = Unit(
        key="kp/aaaa1111/start=1",
        url=api_url("aaaa1111", 1),
        kind="api_page",
        document_id="aaaa1111",
        document_title="机械运动",
        meta={"kp_id": "aaaa1111", "kp_name": "机械运动", "start": 1, "page_index": 1},
    )
    page = json.dumps(
        {
            "status": {"code": 0},
            "data": {
                "question": [
                    {"qid": f"q{i}", "bdjson": _bdjson()} for i in range(20)
                ],
                "kp_info": {"que_num": 40, "kp_id": "aaaa1111"},
            },
        },
        ensure_ascii=False,
    )

    result = site.process(unit, page)

    assert len(result.questions) == 20
    assert [child.key for child in result.children] == ["kp/aaaa1111/start=21"]


def test_parse_builds_questions():
    site = _site()
    unit = Unit(
        key="kp/aaaa1111/start=1",
        url=api_url("aaaa1111", 1),
        kind="api_page",
        document_id="aaaa1111",
        document_title="机械运动",
        meta={"kp_id": "aaaa1111", "kp_name": "机械运动", "start": 1},
    )
    html = _payload(
        [
            {"qid": "q1", "bdjson": _bdjson()},
            {"qid": "q2", "bdjson": _bdjson(que_info={"type": "jd"})},
        ]
    )
    questions = site.parse(unit, html)

    assert len(questions) == 1
    question = questions[0]
    assert question.source == "baidu_tiku"
    assert question.subject == "物理" and question.stage == "高中"
    assert question.source_question_id == "q1"
    assert question.answer == "B"
    assert question.choice_type == "single"
    assert [option.label for option in question.options] == ["A", "B", "C", "D"]
    assert "第1页" not in question.knowledge_points[0]


def test_empty_page_is_allowed_and_next_page_stops():
    site = _site()
    unit = Unit(
        key="kp/aaaa1111/start=1",
        url=api_url("aaaa1111", 1),
        kind="api_page",
        document_id="aaaa1111",
        meta={"kp_id": "aaaa1111", "start": 1, "page_index": 1},
    )
    assert site.allow_empty is True
    assert site.raw_suffix == ".json"
    assert list(site.expand(unit, _payload([]))) == []


def test_next_page_uses_que_num():
    site = _site()
    unit = Unit(
        key="kp/aaaa1111/start=1",
        url=api_url("aaaa1111", 1),
        kind="api_page",
        document_id="aaaa1111",
        meta={"kp_id": "aaaa1111", "start": 1, "page_index": 1},
    )
    page = json.dumps(
        {
            "status": {"code": 0},
            "data": {
                "question": [
                    {"qid": f"q{i}", "bdjson": _bdjson()} for i in range(20)
                ],
                "kp_info": {"que_num": 40, "kp_id": "aaaa1111"},
            },
        },
        ensure_ascii=False,
    )
    children = list(site.expand(unit, page))
    assert [child.key for child in children] == ["kp/aaaa1111/start=21"]


def test_supports_chapterlist_and_api_urls():
    site = _site()
    assert site.supports("https://tiku.baidu.com/tikupc/chapterlist/x-18-knowpoint-11")
    assert site.supports("https://tiku.baidu.com/bigque/interface/getqueinfos?kp_id=x")
    assert not site.supports("https://www.jyeoo.com/pp/123")
