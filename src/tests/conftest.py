from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import yaml

from k12_crawler.config import load_config
from k12_crawler.subjects import resolve_subject
from k12_crawler.models import Option, Question
from k12_crawler.harvest.sites.cooco import CoocoSite

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "src" / "tests" / "fixtures" / "live"

sys.path.insert(0, str(ROOT / "src" / "tests"))

from local_site import LocalSite  # noqa: E402


@pytest.fixture(scope="session")
def cooco_source() -> CoocoSite:
    return CoocoSite(resolve_subject("cooco", "gzwl"))


@pytest.fixture(scope="session")
def zhejiang_html() -> str:
    return (LIVE / "paper_zhejiang_2022.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def shandong_html() -> str:
    return (LIVE / "paper_shandong_2022.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def detail_html() -> str:
    return (LIVE / "detail_470797.html").read_text(encoding="utf-8")


@pytest.fixture
def local_site():
    site = LocalSite()
    try:
        yield site
    finally:
        site.stop()


def write_test_config(tmp_path: Path, site: LocalSite, **overrides: Any) -> Path:
    """Write an isolated config; every path is inside tmp_path."""
    data = {
        "source": "cooco",
        "base_url": site.base_url,
        "list_url": f"{site.base_url}/exam_list/gzwl/",
        "subject_code": "gzwl",
        "subject": "物理",
        "stage": "高中",
        "question_type_filter": "选择题",
        "db_path": str(tmp_path / "k12.sqlite"),
        "raw_dir": str(tmp_path / "raw"),
        "image_dir": str(tmp_path / "images"),
        "export_dir": str(tmp_path / "exports"),
        "ledger_path": str(tmp_path / "compliance" / "ledger.jsonl"),
        "timeout": 5,
        "min_interval": 0,
        "max_retries": 2,
        "max_papers": 2,
        "max_questions": 30,
        "download_images": False,
        "authorized": False,
        # Keep the legacy compliance-cap tests meaningful; production default
        # is false (no site-wide hard cap).
        "limits_enabled": True,
        "compliance_note": "test-only local fixture; no external requests",
    }
    data.update(overrides)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def run_cli(
    config_path: Path,
    *args: str,
    timeout: float = 60,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    command = [
        sys.executable,
        str(ROOT / "src" / "main.py"),
        "--config",
        str(config_path),
        *args,
    ]
    return subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def cli_json(completed: subprocess.CompletedProcess) -> Dict[str, Any]:
    return json.loads(completed.stdout)


@pytest.fixture
def test_config(tmp_path, local_site):
    path = write_test_config(tmp_path, local_site)
    return path


@pytest.fixture
def make_question():
    def factory(
        qid: str = "q1",
        answer: str = "A",
        options: int = 4,
        stem: str = "测试题干",
        analysis: str = "测试解析",
    ) -> Question:
        labels = "ABCD"[:options]
        return Question(
            source="cooco",
            source_question_id=qid,
            paper_id="p1",
            paper_title="测试试卷",
            subject="物理",
            stage="高中",
            question_type="选择题",
            question_number=1,
            stem_html=f"<p>{stem}</p>",
            stem_text=stem,
            options=[Option(label=label, text=f"选项{label}") for label in labels],
            answer=answer,
            analysis_html=f"<p>{analysis}</p>" if analysis else None,
            analysis_text=analysis,
            knowledge_points=["测试知识点"],
            difficulty=None,
            difficulty_stars=3,
            source_url="http://example.test/q1",
            paper_url="http://example.test/p1",
            content_hash=f"hash-{qid}",
            choice_type="single" if answer and len(answer) == 1 else "multiple",
        )

    return factory
