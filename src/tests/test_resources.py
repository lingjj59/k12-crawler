from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from k12_crawler.config import Config
from k12_crawler.html_utils import extension_for_format, sniff_image_format
from k12_crawler.models import Question, Resource
from k12_crawler.resources import ResourceManager


class FakeFetcher:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = 0

    def get_bytes(self, url: str) -> bytes:
        self.calls += 1
        return self.payload

    def close(self) -> None:
        pass


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 3), (255, 0, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _question_with_resource(url: str) -> Question:
    return Question(
        source="cooco",
        source_question_id="q1",
        paper_id="p1",
        paper_title="p",
        subject="物理",
        stage="高中",
        question_type="选择题",
        question_number=1,
        stem_html="[[IMG:1]]",
        stem_text="[[IMG:1]]",
        options=[],
        answer="A",
        analysis_html=None,
        analysis_text=None,
        knowledge_points=[],
        difficulty=None,
        difficulty_stars=None,
        source_url="http://example.test/q1",
        paper_url="http://example.test/p1",
        resources=[
            Resource(
                ordinal=1,
                role="stem",
                original_url=url,
                original_extension=Path(url).suffix.lstrip("."),
                placeholder="[[IMG:1]]",
            )
        ],
    )


def test_sniff_formats():
    assert sniff_image_format(_png_bytes(), "x.wmf") == "png"
    assert sniff_image_format(b"\xff\xd8\xff\xe0" + b"\x00" * 10, "x.jpg") == "jpg"
    assert sniff_image_format(b"\xd7\xcd\xc6\x9a" + b"\x00" * 10, "x.wmf") == "wmf"
    assert extension_for_format("png") == "png"


def test_wmf_url_that_returns_png_is_saved_as_png(tmp_path):
    cfg = Config(
        image_dir=tmp_path / "images",
        raw_dir=tmp_path / "raw",
        db_path=tmp_path / "db.sqlite",
        download_images=True,
        min_interval=0,
    )
    cfg.ensure_dirs()
    fetcher = FakeFetcher(_png_bytes())
    manager = ResourceManager(cfg, fetcher)  # type: ignore[arg-type]
    question = _question_with_resource("http://example.test/a.wmf")
    downloaded, failed = manager.materialize(question)
    resource = question.resources[0]
    assert downloaded == 1
    assert failed == 0
    assert resource.format == "png"
    assert resource.conversion_status == "renamed_png"
    assert resource.local_path and resource.local_path.endswith(".png")
    assert Path(resource.local_path).exists()
    assert (resource.width, resource.height) == (4, 3)


def test_real_wmf_without_converter_is_preserved_and_marked(tmp_path, monkeypatch):
    cfg = Config(
        image_dir=tmp_path / "images",
        raw_dir=tmp_path / "raw",
        db_path=tmp_path / "db.sqlite",
        download_images=True,
        min_interval=0,
    )
    cfg.ensure_dirs()
    fetcher = FakeFetcher(b"\xd7\xcd\xc6\x9a" + b"\x00" * 100)
    manager = ResourceManager(cfg, fetcher)  # type: ignore[arg-type]
    monkeypatch.setattr(manager, "_convert_to_png", lambda source: None)
    question = _question_with_resource("http://example.test/a.wmf")
    downloaded, failed = manager.materialize(question)
    resource = question.resources[0]
    assert downloaded == 1
    assert failed == 1  # conversion unsupported is surfaced as a resource error
    assert resource.format == "wmf"
    assert resource.conversion_status == "unsupported"
    assert resource.local_path and resource.local_path.endswith(".wmf")
    assert Path(resource.local_path).exists()


def test_cached_duplicate_url_keeps_final_status(tmp_path):
    cfg = Config(
        image_dir=tmp_path / "images",
        raw_dir=tmp_path / "raw",
        db_path=tmp_path / "db.sqlite",
        download_images=True,
        min_interval=0,
    )
    cfg.ensure_dirs()
    fetcher = FakeFetcher(_png_bytes())
    manager = ResourceManager(cfg, fetcher)  # type: ignore[arg-type]
    question = _question_with_resource("http://example.test/a.wmf")
    question.resources.append(
        Resource(
            ordinal=2,
            role="analysis",
            original_url="http://example.test/a.wmf",
            original_extension="wmf",
            placeholder="[[IMG:2]]",
        )
    )
    downloaded, failed = manager.materialize(question)
    assert downloaded == 1
    assert failed == 0
    assert fetcher.calls == 1
    assert question.resources[0].conversion_status == "renamed_png"
    assert question.resources[1].conversion_status == "renamed_png"
