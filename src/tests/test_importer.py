from __future__ import annotations

import json

import pytest

from k12_crawler.config import Config
from k12_crawler.importer import import_items, load_items
from k12_crawler.storage import Storage


def _config(tmp_path) -> Config:
    return Config(
        db_path=tmp_path / "importer.sqlite",
        raw_dir=tmp_path / "raw",
        image_dir=tmp_path / "images",
        export_dir=tmp_path / "exports",
    )


def _item(**overrides):
    item = {
        "source": "baidu_tiku",
        "paper_id": "kp-1",
        "paper_title": "第一章 运动的描述",
        "paper_url": "https://tiku.baidu.com/chapterdetail/kp-1",
        "subject": "物理",
        "stage": "高中",
        "source_question_id": "q1",
        "question_number": 1,
        "question_type": "选择题",
        "choice_type": "single",
        "stem_text": "下列说法正确的是",
        "stem_html": "<p>下列说法正确的是</p>",
        "options": [
            {"label": "A", "text": "甲"},
            {"label": "B", "text": "乙"},
        ],
        "answer": "A",
        "analysis_text": "因为甲正确。",
        "knowledge_points": ["运动的描述"],
        "difficulty": "easy",
        "difficulty_stars": 1,
        "source_url": "https://tiku.baidu.com/tikupc/singledetail/q1",
    }
    item.update(overrides)
    return item


def _write(path, items):
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
        encoding="utf-8",
    )


def test_import_stores_papers_and_questions(tmp_path):
    config = _config(tmp_path)
    path = tmp_path / "items.jsonl"
    _write(
        path,
        [
            _item(),
            _item(
                source_question_id="q2",
                question_number=2,
                stem_text="关于参考系，下列说法正确的是",
                options=[{"label": "A", "text": "丙"}, {"label": "B", "text": "丁"}],
                answer="B",
            ),
            _item(
                paper_id="kp-2",
                paper_title="第二章",
                source_question_id="q3",
                stem_text="速度的单位是",
            ),
        ],
    )

    result = import_items(config, path)

    assert result["status"] == "completed"
    assert result["papers"] == 2
    assert result["questions"] == 3
    assert result["stored"] == 3
    assert result["source_duplicate"] == 0
    assert result["content_duplicate"] == 0

    storage = Storage(config.db_path)
    try:
        assert storage.count_questions() == 3
        assert storage.count_papers() == 2
        assert storage.counts_by_source() == {"baidu_tiku": 3}
    finally:
        storage.close()


def test_reimport_is_idempotent(tmp_path):
    config = _config(tmp_path)
    path = tmp_path / "items.jsonl"
    _write(path, [_item()])

    assert import_items(config, path)["stored"] == 1
    second = import_items(config, path)

    assert second["stored"] == 0
    assert second["source_duplicate"] == 1
    storage = Storage(config.db_path)
    try:
        assert storage.count_questions() == 1
    finally:
        storage.close()


def test_content_duplicate_across_source_ids(tmp_path):
    config = _config(tmp_path)
    path = tmp_path / "items.jsonl"
    _write(
        path,
        [
            _item(source_question_id="q1"),
            _item(source_question_id="q9", question_number=9),
        ],
    )

    result = import_items(config, path)

    assert result["stored"] == 1
    assert result["content_duplicate"] == 1


def test_dry_run_does_not_write(tmp_path):
    config = _config(tmp_path)
    path = tmp_path / "items.jsonl"
    _write(path, [_item()])

    result = import_items(config, path, dry_run=True)

    assert result["dry_run"] is True
    assert result["stored"] == 1
    storage = Storage(config.db_path)
    try:
        assert storage.count_questions() == 0
    finally:
        storage.close()


def test_release_id_is_backfilled(tmp_path):
    config = _config(tmp_path)
    path = tmp_path / "items.jsonl"
    _write(path, [_item()])

    import_items(config, path, release_id="batch99")

    storage = Storage(config.db_path)
    try:
        row = storage.conn.execute(
            "SELECT release_id FROM questions WHERE source_question_id='q1'"
        ).fetchone()
        assert row["release_id"] == "batch99"
    finally:
        storage.close()


def test_missing_file_and_bad_json_are_reported(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(FileNotFoundError):
        import_items(config, tmp_path / "missing.jsonl")

    path = tmp_path / "broken.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_items(path)
