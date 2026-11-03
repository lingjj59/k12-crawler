from __future__ import annotations

from pathlib import Path

from main import build_parser

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "SKILL.md"


def test_skill_documents_agent_commands_and_paths():
    text = SKILL.read_text(encoding="utf-8")
    for token in (
        "harvest",
        "extract",
        "sites",
        "import-items",
        "items.jsonl",
        "units/<unit_key>.html",
        "data/<subject_slug>/raw/<site>/<subject_code>/",
        "[[IMG:n]]",
        "--subject",
        "--max-units",
        "--max-questions",
        "--images",
        "--json",
        "退出码",
        "Site",
    ):
        assert token in text, token


def test_documented_commands_parse_with_real_cli():
    parser = build_parser()
    examples = (
        ["sites", "--json"],
        ["sites", "--subject", "gzhx", "--json"],
        ["harvest", "--site", "jyeoo", "--subject", "gzhx", "--max-units", "2", "--json"],
        ["harvest", "--site", "cooco", "--max-pages", "1", "--json"],
        [
            "extract",
            "--site",
            "jyeoo",
            "--subject",
            "gzhx",
            "--url",
            "https://www.jyeoo.com/pp/example",
            "--json",
        ],
    )
    for args in examples:
        namespace = parser.parse_args(args)
        assert namespace.command == args[0]
