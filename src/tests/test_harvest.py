"""Tests for the agent-facing raw harvester (no network access)."""

from __future__ import annotations

import json
from pathlib import Path

from k12_crawler.harvest.registry import available_sites, build_site
from k12_crawler.harvest.runner import _rebuild_items
from k12_crawler.harvest.site import Site
from k12_crawler.harvest.unit import INDEX, LEAF, Unit
from k12_crawler.models import Resource
from k12_crawler.subjects import resolve_subject

FIXTURES = Path(__file__).parent / "fixtures" / "live"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _site(name: str, code: str):
    return build_site(name, resolve_subject(name, code))


class _RecordingSite(Site):
    """Minimal site that records which hook ``process`` called."""

    name = "recording"
    label = "recording"
    subjects = ()

    def __init__(self, subject, parse_index: bool = False):
        super().__init__(subject)
        self.parse_index = parse_index
        self.parsed = []
        self.expanded = []

    @classmethod
    def seed_for(cls, subject):
        return "https://example.test/list/"

    def seed_unit(self, seed):
        return Unit(key="list/1", url=seed, role=INDEX, kind="list")

    def parse(self, unit, raw):
        self.parsed.append(unit.key)
        return ["question"]

    def expand(self, unit, raw):
        self.expanded.append(unit.key)
        return [Unit(key="leaf", url="https://example.test/leaf", role=LEAF)]


def test_registry_contains_all_sites():
    assert available_sites() == ["baidu", "cooco", "jyeoo"]
    assert _site("jyeoo", "gzwl").name == "jyeoo"
    assert _site("cooco", "gzwl").name == "cooco"


def test_process_skips_parse_on_index_pages_by_default():
    site = _RecordingSite(resolve_subject("jyeoo", "gzwl"))
    index = site.seed_unit("https://example.test/list/")

    result = site.process(index, "<html>not json</html>")

    assert site.parsed == []
    assert site.expanded == ["list/1"]
    assert result.questions == []
    assert [child.key for child in result.children] == ["leaf"]


def test_process_parses_leaf_pages_and_returns_children():
    site = _RecordingSite(resolve_subject("jyeoo", "gzwl"))
    leaf = Unit(
        key="paper/1",
        url="https://example.test/paper/1",
        role=LEAF,
        kind="paper",
    )

    result = site.process(leaf, "<html></html>")

    assert site.parsed == ["paper/1"]
    assert result.questions == ["question"]
    assert [child.key for child in result.children] == ["leaf"]


def test_process_can_opt_in_to_parsing_index_pages():
    site = _RecordingSite(resolve_subject("jyeoo", "gzwl"), parse_index=True)
    index = site.seed_unit("https://example.test/list/")

    result = site.process(index, "<html></html>")

    assert site.parsed == ["list/1"]
    assert result.questions == ["question"]


def test_jyeoo_expands_papers_and_pagination():
    site = _site("jyeoo", "gzwl")
    unit = site.seed_unit(site.seed_for(site.subject))
    children = list(site.expand(unit, _fixture("jyeoo_list_gzwl.html")))
    papers = [child for child in children if child.kind == "paper"]
    assert len(papers) == 10
    assert papers[0].document_id
    assert papers[0].document_title.startswith("2025年河南省平顶山市")
    assert any(child.key == "list/2" for child in children)


def test_jyeoo_parses_paper_questions_and_images():
    site = _site("jyeoo", "gzwl")
    url = "https://www.jyeoo.com/pp/6e618272-89e6-4757-a293-5f8e796e258a"
    unit = Unit(key=url, url=url, kind="paper")
    questions = site.parse(unit, _fixture("jyeoo_paper_sample.html"))

    assert unit.document_id == "6e618272-89e6-4757-a293-5f8e796e258a"
    assert unit.meta["year"] == 2025
    assert len(questions) == 22

    first = questions[0]
    assert first.question_number == 1
    assert first.question_type == "选择题"
    assert len(first.options) == 4
    assert first.options[0].label == "A"
    assert first.answer is None  # paper pages do not expose answers
    assert first.content_hash
    assert len(first.resources) == 5
    assert first.resources[0].original_url.startswith("https://img.jyeoo.net/")


def test_rebuild_items_deduplicates_after_rerun(tmp_path):
    units = tmp_path / "units"
    units.mkdir()
    items = tmp_path / "items.jsonl"
    record = {
        "site": "jyeoo",
        "paper_id": "p1",
        "source_question_id": "p1-1",
        "question_number": 1,
        "stem_text": "x",
    }
    line = json.dumps(record, ensure_ascii=False)
    (units / "p1.jsonl").write_text(line + "\n" + line + "\n", encoding="utf-8")

    count, done = _rebuild_items(units, items)
    assert count == 1
    assert done == ["p1"]
    assert len(items.read_text(encoding="utf-8").splitlines()) == 1


def test_image_download_keeps_wmf_extension_and_marks_unsupported(tmp_path):
    from types import SimpleNamespace

    from k12_crawler.harvest.runner import _download_images

    class FakeFetcher:
        def get_bytes(self, url: str) -> bytes:
            return b"\xd7\xcd\xc6\x9a fake wmf"

    question = SimpleNamespace(
        resources=[
            Resource(
                ordinal=1,
                role="stem",
                original_url="https://img.example.test/formula.wmf",
                placeholder="[[IMG:1]]",
            )
        ]
    )
    downloaded, failed = _download_images(FakeFetcher(), [question], tmp_path)

    resource = question.resources[0]
    assert (downloaded, failed) == (1, 0)
    assert resource.format == "wmf"
    assert resource.local_path.endswith(".wmf")
    assert resource.conversion_status == "unsupported"
    assert (tmp_path / resource.local_path.split("/")[-1]).exists()
