"""Tests for the multi-subject registry and subject-aware adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from k12_crawler.config import Config
from k12_crawler.harvest.registry import build_site, site_catalog
from k12_crawler.harvest.sites.cooco import CoocoSite
from k12_crawler.harvest.sites.jyeoo import JyeooSite
from k12_crawler.harvest.unit import Unit
from k12_crawler.subjects import (
    default_subject,
    detect_subject,
    export_basename,
    resolve_subject,
    resolve_subject_any,
    seed_url,
    site_subjects,
)

FIXTURES = Path(__file__).parent / "fixtures" / "live"


def test_sites_expose_their_own_subject_channels():
    jyeoo = {subject.code for subject in site_subjects("jyeoo")}
    cooco = {subject.code for subject in site_subjects("cooco")}

    assert {"gzwl", "gzhx", "czhx", "gzsx"} <= jyeoo
    assert {"gzwl", "gzhx", "czhx", "gzls", "gzzz"} <= cooco
    # jyeoo has no 历史/政治 channel and cooco has no AI/信息 channel.
    assert "gzls" not in jyeoo
    assert "gzaits" not in cooco
    assert default_subject("jyeoo").code == "gzwl"


def test_seed_urls_follow_each_site_template():
    jyeoo = resolve_subject("jyeoo", "gzhx")
    cooco = resolve_subject("cooco", "gzhx")
    assert seed_url("jyeoo", jyeoo) == "https://www.jyeoo.com/shijuan/gzhx/"
    assert seed_url("cooco", cooco) == (
        "http://gzhx.cooco.net.cn/exam_list_new/gzhx/"
    )


def test_resolve_subject_by_code_slug_and_full_name():
    assert resolve_subject("jyeoo", "gzhx").name == "化学"
    assert resolve_subject("jyeoo", "chemistry").name == "化学"
    assert resolve_subject("jyeoo", "高中化学").stage == "高中"
    assert resolve_subject("cooco", "初中物理").code == "czwl"
    assert resolve_subject("cooco", "gzls").name == "历史"
    with pytest.raises(ValueError, match="多个学段"):
        resolve_subject("jyeoo", "化学")
    with pytest.raises(ValueError, match="不支持"):
        resolve_subject("jyeoo", "历史")


def test_resolve_subject_any_for_database_filters():
    assert resolve_subject_any("gzhx").name == "化学"
    assert resolve_subject_any("高中化学").stage == "高中"
    with pytest.raises(ValueError, match="多个学段"):
        resolve_subject_any("化学")


def test_export_basename_uses_stage_and_slug():
    assert export_basename(resolve_subject("cooco", "gzhx")) == (
        "high_school_chemistry.jsonl"
    )
    assert export_basename(resolve_subject("cooco", "czwl")) == (
        "middle_school_physics.jsonl"
    )


def test_detect_subject_from_url_meta_and_title():
    assert detect_subject(
        "jyeoo", "https://www.jyeoo.com/shijuan/czhx/101/2"
    ).code == "czhx"
    assert detect_subject(
        "cooco", "http://gzhx.cooco.net.cn/paper/1688356417/"
    ).code == "gzhx"

    html = (
        "<html><head>"
        '<meta name="keywords" content="2022年化学试卷,高中化学试卷" />'
        "<title>2022年湖南省高考化学模拟试卷 - 菁优网</title>"
        "</head></html>"
    )
    assert detect_subject("jyeoo", "https://www.jyeoo.com/pp/uuid", html).code == "gzhx"

    physics = "<html><head><title>2025年高考物理一模试卷</title></head></html>"
    assert detect_subject("jyeoo", "https://www.jyeoo.com/pp/uuid", physics).code == (
        "gzwl"
    )


def test_detect_subject_refuses_to_guess_ambiguous_title():
    html = "<html><head><title>化学与物理综合试卷</title></head></html>"
    assert detect_subject("jyeoo", "https://www.jyeoo.com/pp/uuid", html) is None


def test_sites_are_built_for_the_requested_subject():
    site = build_site("jyeoo", "gzhx")
    assert isinstance(site, JyeooSite)
    assert site.label == "菁优网"
    assert site.seed_for(site.subject) == "https://www.jyeoo.com/shijuan/gzhx/"

    cooco = build_site("cooco", "czhx")
    assert isinstance(cooco, CoocoSite)
    assert cooco.subject.code == "czhx"
    assert cooco.seed_for(cooco.subject) == (
        "http://czhx.cooco.net.cn/exam_list_new/czhx/"
    )


def test_jyeoo_chemistry_paper_is_labelled_and_keeps_subscripts():
    site = build_site("jyeoo", "gzhx")
    url = "https://www.jyeoo.com/pp/ddb539c8-519f-40e0-a0b5-f7d9f16b29f1"
    unit = Unit(key=url, url=url, kind="paper")
    html = (FIXTURES / "jyeoo_paper_gzhx.html").read_text(encoding="utf-8")
    questions = site.parse(unit, html)

    assert site.subject.name == "化学"
    assert site.subject.stage == "高中"
    assert len(questions) == 19
    assert questions[0].subject == "化学"
    stems = " ".join(question.stem_text for question in questions)
    assert "_{2}" in stems or "^{" in stems
    assert "CuCl 2" not in stems  # subscript spacing bug must not come back


def test_cooco_chemistry_site_uses_the_chemistry_subdomain():
    site = build_site("cooco", "gzhx")
    assert site.subject.name == "化学"
    assert site.seed_for(site.subject).startswith("http://gzhx.cooco.net.cn/")
    assert site.source_name == "cooco"


def test_site_catalog_can_be_filtered_by_subject():
    catalog = site_catalog("gzhx")
    assert {entry["name"] for entry in catalog} == {"baidu", "cooco", "jyeoo"}
    for entry in catalog:
        assert entry["subject_count"] == 1
        assert entry["subjects"][0]["code"] == "gzhx"
        assert entry["subjects"][0]["subject"] == "化学"

    with_history = site_catalog("gzls")
    assert [entry["name"] for entry in with_history] == ["cooco"]


def test_apply_to_config_rewrites_site_urls_but_not_custom_mirrors():
    from k12_crawler.subjects import apply_to_config

    standard = Config()
    apply_to_config(standard, resolve_subject("cooco", "czhx"))
    assert standard.list_url == "http://czhx.cooco.net.cn/exam_list_new/czhx/"
    assert standard.base_url == "http://czhx.cooco.net.cn"

    custom = Config(
        base_url="http://127.0.0.1:9",
        list_url="http://127.0.0.1:9/exam_list/gzwl/",
    )
    apply_to_config(custom, resolve_subject("cooco", "czhx"))
    assert custom.subject_code == "czhx"
    assert custom.list_url == "http://127.0.0.1:9/exam_list/gzwl/"
    assert custom.base_url == "http://127.0.0.1:9"
