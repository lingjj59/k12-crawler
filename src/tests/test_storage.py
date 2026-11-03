from __future__ import annotations

import json

from k12_crawler.models import Paper, Resource
from k12_crawler.storage import Storage


def test_paper_and_question_storage(tmp_path, make_question):
    db = tmp_path / "test.sqlite"
    storage = Storage(db)
    paper = Paper(
        source="cooco",
        paper_id="p1",
        title="测试试卷",
        url="http://example.test/p1",
        subject="物理",
        stage="高中",
        year=2022,
        region="浙江",
        exam_type="高考真题",
        raw_html_path="data/raw/cooco/p1.html",
    )
    assert storage.save_paper(paper) > 0
    assert storage.save_paper(paper) > 0  # upsert, still one paper

    question = make_question(qid="q1")
    question.resources = [
        Resource(
            ordinal=1,
            role="analysis",
            original_url="http://example.test/a.wmf",
            placeholder="[[IMG:1]]",
            format="png",
            local_path="data/images/cooco/_shared/a.png",
            conversion_status="renamed_png",
            width=91,
            height=19,
        )
    ]
    status, duplicate_of, internal_id = storage.save_question(question)
    assert status == "stored"
    assert duplicate_of is None
    assert internal_id is not None

    status, duplicate_of, _ = storage.save_question(question)
    assert status == "source_duplicate"
    assert duplicate_of == "q1"

    same_content = make_question(qid="q2")
    same_content.content_hash = question.content_hash
    status, duplicate_of, _ = storage.save_question(same_content)
    assert status == "content_duplicate"
    assert duplicate_of == "q1"

    stats = storage.stats()
    assert stats["papers"] == 1
    assert stats["questions"] == 1
    assert stats["resources"] == 1
    assert stats["duplicates"] == 2  # one source duplicate + one content duplicate

    record = storage.get_question("cooco", "q1")
    assert record is not None
    assert record["answer"] == "A"
    assert len(record["resources"]) == 1
    assert record["resources"][0]["conversion_status"] == "renamed_png"

    out = tmp_path / "questions.jsonl"
    assert storage.export_jsonl(out) == 1
    line = out.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["source_question_id"] == "q1"
    assert parsed["options"][0]["label"] == "A"
    storage.close()
