from __future__ import annotations

import json
from pathlib import Path

from conftest import cli_json, run_cli


def _harvest(test_config, local_site, out: Path):
    return run_cli(
        test_config,
        "harvest",
        "--site",
        "cooco",
        "--subject",
        "gzwl",
        "--url",
        f"{local_site.base_url}/exam_list/gzwl/",
        "--max-units",
        "2",
        "--min-interval",
        "0",
        "--out",
        str(out),
        "--json",
    )


def _seed_database(test_config, local_site, tmp_path: Path):
    out = tmp_path / "harvest"
    completed = _harvest(test_config, local_site, out)
    assert completed.returncode == 0, completed.stderr
    payload = cli_json(completed)
    assert payload["status"] == "completed"
    assert payload["questions"] == 41
    items = out / "items.jsonl"
    assert items.exists()

    imported = run_cli(
        test_config, "import-items", "--items", str(items), "--json"
    )
    assert imported.returncode == 0, imported.stderr
    result = cli_json(imported)
    assert result["stored"] == 41
    return out


def test_all_commands_have_help(test_config):
    for command in (
        "harvest",
        "extract",
        "sites",
        "stats",
        "export",
        "list",
        "import-items",
    ):
        completed = run_cli(test_config, command, "--help")
        assert completed.returncode == 0, completed.stderr
        assert "usage:" in completed.stdout


def test_argument_errors_exit_2_and_json(test_config):
    completed = run_cli(test_config, "harvest", "--max-units", "-1", "--json")
    assert completed.returncode == 2
    payload = cli_json(completed)
    assert payload["status"] == "error"
    assert payload["error_type"] == "argument"

    completed = run_cli(test_config, "harvest", "--site", "not-a-site")
    assert completed.returncode == 2


def test_json_output_survives_legacy_windows_code_page(test_config):
    completed = run_cli(
        test_config,
        "sites",
        "--json",
        env={"PYTHONIOENCODING": "cp1252"},
    )
    assert completed.returncode == 0, completed.stderr
    payload = cli_json(completed)
    assert payload["status"] == "ok"
    assert {site["name"] for site in payload["sites"]} == {"baidu", "cooco", "jyeoo"}


def test_stats_json_schema(test_config):
    completed = run_cli(test_config, "stats", "--json")
    assert completed.returncode == 0, completed.stderr
    payload = cli_json(completed)
    assert payload["status"] == "ok"
    for key in (
        "papers",
        "questions",
        "resources",
        "task_status",
        "job_status",
        "unfinished_jobs",
        "recent_errors",
        "ledger",
        "db_path",
        "papers_detail",
    ):
        assert key in payload, key
    assert payload["questions"] == 0
    assert Path(payload["db_path"]).exists()


def test_harvest_import_export_and_list(test_config, local_site, tmp_path):
    _seed_database(test_config, local_site, tmp_path)

    stats = cli_json(run_cli(test_config, "stats", "--json"))
    assert stats["questions"] == 41
    assert stats["papers"] == 2

    listing = cli_json(
        run_cli(test_config, "list", "--type", "questions", "--limit", "5", "--json")
    )
    assert listing["status"] == "ok"
    assert listing["count"] == 5
    assert listing["items"][0]["source_question_id"]

    papers = cli_json(run_cli(test_config, "list", "--type", "papers", "--json"))
    assert papers["count"] == 2
    assert papers["items"][0]["stored_questions"] > 0



def test_export_filters(test_config, local_site, tmp_path):
    _seed_database(test_config, local_site, tmp_path)

    output = tmp_path / "shan_dong.jsonl"
    filtered = cli_json(
        run_cli(
            test_config,
            "export",
            "--paper-id",
            "1688525155",
            "--status",
            "pass",
            "--output",
            str(output),
            "--json",
        )
    )
    assert filtered["status"] == "ok"
    assert filtered["exported"] == 18
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 18
    assert {json.loads(line)["paper_id"] for line in lines} == {"1688525155"}

    limited = cli_json(
        run_cli(
            test_config,
            "export",
            "--limit",
            "5",
            "--output",
            str(tmp_path / "five.jsonl"),
            "--json",
        )
    )
    assert limited["exported"] == 5


def test_import_items_is_idempotent(test_config, local_site, tmp_path):
    out = _seed_database(test_config, local_site, tmp_path)
    second = cli_json(
        run_cli(
            test_config,
            "import-items",
            "--items",
            str(out / "items.jsonl"),
            "--json",
        )
    )
    assert second["stored"] == 0
    assert second["source_duplicate"] == 41


def test_sites_lists_subject_channels(test_config):
    payload = cli_json(run_cli(test_config, "sites", "--json"))
    assert payload["status"] == "ok"
    assert {site["name"] for site in payload["sites"]} == {"baidu", "cooco", "jyeoo"}
    assert all(site["subjects"] for site in payload["sites"])

    chemistry = cli_json(
        run_cli(test_config, "sites", "--subject", "gzhx", "--json")
    )
    assert chemistry["count"] == 3
    for site in chemistry["sites"]:
        assert site["subjects"][0]["subject"] == "化学"


def test_ambiguous_subject_is_an_argument_error(test_config):
    completed = run_cli(test_config, "sites", "--subject", "化学", "--json")
    assert completed.returncode == 2
    payload = cli_json(completed)
    assert payload["error_type"] == "argument"
    assert "多个学段" in payload["message"]


def test_subject_must_match_seed_url(test_config):
    completed = run_cli(
        test_config,
        "harvest",
        "--site",
        "jyeoo",
        "--subject",
        "gzwl",
        "--url",
        "https://www.jyeoo.com/shijuan/gzhx/",
        "--json",
    )
    assert completed.returncode == 2
    payload = cli_json(completed)
    assert payload["error_type"] == "argument"
    assert "不一致" in payload["message"]
