#!/usr/bin/env python3
"""Command-line interface for the K12 crawler.

The CLI only parses arguments and prints results; raw website harvesting lives
in ``k12_crawler.harvest``, processing in ``k12_crawler.workflow``.

Installed entry points (``k12-crawler`` or ``python -m k12_crawler``) call
:func:`main`; ``src/main.py`` is kept as a compatibility shim for tests.

Exit codes:
  0  success
  1  runtime failure / partial run
  2  invalid arguments (argparse)
  3  site access blocked (403/429/captcha/WAF)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from k12_crawler import workflow
from k12_crawler.config import default_config_path, load_config
from k12_crawler.fetcher import AccessBlocked
from k12_crawler.harvest import available_sites, site_catalog
from k12_crawler.subjects import (
    apply_to_config,
    detect_subject,
    export_basename,
    resolve_subject,
    resolve_subject_any,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3


def _configure_stdio() -> None:
    """Keep legacy Windows code pages from crashing on Chinese output.

    ``json.dumps(..., ensure_ascii=False)`` is friendlier on UTF-8 terminals,
    but a piped Windows process may use cp1252.  Escaping unencodable
    characters (``\\u4e2d``) keeps the JSON valid instead of raising
    ``UnicodeEncodeError``.
    """

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass



class _Parser(argparse.ArgumentParser):
    """Exit 2 for usage errors; emit JSON when --json was requested."""

    json_requested = False

    def error(self, message: str) -> None:  # type: ignore[override]
        if _Parser.json_requested:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error_type": "argument",
                        "message": message,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            self.print_usage(sys.stderr)
            print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出机器可读 JSON（多数命令默认已是 JSON）",
    )


def _add_subject_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subject",
        default=None,
        help="学科代码/名称，如 gzhx、化学、高中化学；默认 gzwl（高中物理）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="k12-crawler",
        description=(
            "K12 多学科试题爬虫：站点用 Site/Unit 模型描述，"
            "抓取列表、试卷、详情或 JSON API 分页，输出原始响应与 items.jsonl；"
            "支持高中/初中物理、化学、数学、生物、语文、英语、地理、历史、政治等学科；"
            "供 Agent 直接调用；保留既有校验/SQLite/导出框架。"
        ),
        epilog=(
            "示例：\n"
            "  k12-crawler sites --json\n"
            "  k12-crawler sites --subject gzhx --json\n"
            "  k12-crawler harvest --site jyeoo --subject gzhx --max-units 20 --json\n"
            "  k12-crawler harvest --site baidu --subject gzwl --max-units 10 --json\n"
            "  k12-crawler extract --site jyeoo --subject gzhx --url <paper_url> --json\n"
            "  k12-crawler stats --json\n"
            "  k12-crawler export --subject gzhx --output data/chemistry/releases/batch01_cooco_2000/high_school_chemistry.jsonl\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="YAML 配置文件路径（默认 K12_CONFIG，或数据根目录下的 config/config.yaml）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_global",
        help="输出机器可读 JSON",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    harvest = sub.add_parser(
        "harvest",
        help="从网站批量抓取原始题目（原始页 + JSONL），不写数据库",
        description=(
            "面向 Agent 的原始采集命令：从一个种子出发，按站点的 expand() 递归展开"
            "（列表页→试卷、章节目录→知识点→API 分页），保存原始页，并把题目写成 "
            "items.jsonl。断点、限速、去重由 runner 统一处理。"
        ),
    )
    harvest.add_argument(
        "--site", choices=available_sites(), default="jyeoo", help="站点（baidu/cooco/jyeoo）"
    )
    _add_subject_option(harvest)
    harvest.add_argument(
        "--url", default=None, help="自定义种子 URL；默认用站点内置入口"
    )
    harvest.add_argument(
        "--max-units",
        "--max-papers",
        dest="max_units",
        type=int,
        default=20,
        help="本次最多抓取多少个叶子单元（试卷/知识点分页）",
    )
    harvest.add_argument("--max-pages", type=int, default=5, help="最多展开多少层/页列表")
    harvest.add_argument(
        "--max-questions", type=int, default=0, help="本次最多新增多少题；0 表示不设上限"
    )
    harvest.add_argument(
        "--min-interval", type=float, default=1.5, help="同一站点两次请求的最小间隔秒数"
    )
    harvest.add_argument("--out", type=Path, default=None, help="输出目录")
    harvest.add_argument("--images", action="store_true", help="同时下载题目图片")
    harvest.add_argument("--force", action="store_true", help="忽略已有进度，重新抓取")
    _add_json_option(harvest)

    extract = sub.add_parser(
        "extract",
        help="抓取并解析单个试卷/题目 URL，直接输出 JSON",
        description="Agent 单页调用入口：给一个 URL，返回结构化题目，不落盘。",
    )
    extract.add_argument(
        "--site", choices=available_sites(), default="jyeoo", help="站点（baidu/cooco/jyeoo）"
    )
    _add_subject_option(extract)
    extract.add_argument("--url", required=True, help="试卷页或单题页 URL")
    extract.add_argument("--images", action="store_true", help="同时下载图片")
    extract.add_argument("--min-interval", type=float, default=1.5)
    _add_json_option(extract)

    sites_parser = sub.add_parser(
        "sites",
        help="列出可用的站点及学科频道",
        description="列出站点名、支持的学科、默认入口；可用 --subject 过滤。",
    )
    _add_subject_option(sites_parser)
    _add_json_option(sites_parser)

    stats = sub.add_parser(
        "stats",
        help="查看数据库统计、任务状态和最近错误",
        description="只读 SQLite，输出试卷/题目/资源/任务状态等统计。",
    )
    _add_json_option(stats)

    export = sub.add_parser(
        "export",
        help="按条件导出题目 JSONL",
        description="从 SQLite 读取题目，按来源、试卷、校验状态筛选后写 JSONL。",
    )
    export.add_argument("--output", type=Path, default=None, help="输出 JSONL 路径")
    export.add_argument("--paper-id", action="append", default=None)
    export.add_argument(
        "--source",
        default=None,
        help="按来源筛选（任意已入库的 source，如 cooco/jyeoo/baidu_tiku）",
    )
    export.add_argument(
        "--status",
        choices=["pass", "warn", "fail"],
        default=None,
        help="按校验状态筛选",
    )
    export.add_argument(
        "--stage",
        choices=["高中", "初中", "大学基础"],
        default=None,
        help="按学段筛选（开放数据集阶段新增）",
    )
    _add_subject_option(export)
    export.add_argument("--limit", type=int, default=None)
    _add_json_option(export)

    listing = sub.add_parser(
        "list",
        help="查看已保存的题目或试卷",
        description="默认列出题目；用 --type 查看试卷、采集任务或任务记录。",
    )
    listing.add_argument(
        "--type",
        dest="list_type",
        choices=["questions", "papers"],
        default="questions",
        help="查看对象类型（默认 questions）",
    )
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--paper-id", action="append", default=None)
    listing.add_argument(
        "--source",
        default=None,
        help="按来源筛选（任意已入库的 source，如 cooco/jyeoo/baidu_tiku）",
    )
    _add_subject_option(listing)
    listing.add_argument(
        "--status",
        default=None,
        help="questions 用 pass/warn/fail；tasks/jobs 用 pending/running/completed/failed/skipped",
    )
    _add_json_option(listing)

    import_items = sub.add_parser(
        "import-items",
        help="把 harvest 产出的 items.jsonl 导入 SQLite",
        description="读取 items.jsonl，按来源/内容哈希去重后写入共享数据库。",
    )
    import_items.add_argument(
        "--items", type=Path, required=True, help="items.jsonl 路径"
    )
    import_items.add_argument(
        "--release-id", default=None, help="把本次新入库的题目登记到该批次"
    )
    import_items.add_argument(
        "--dry-run", action="store_true", help="只统计去重结果，不写数据库"
    )
    import_items.add_argument(
        "--revalidate", action="store_true", help="忽略文件里的校验状态，重新校验"
    )
    import_items.add_argument(
        "--no-check-files",
        action="store_true",
        help="校验时不检查图片文件是否存在",
    )
    _add_json_option(import_items)

    return parser


def _validate_values(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    for name in ("max_units", "max_questions", "max_pages", "limit"):
        value = getattr(args, name, None)
        if value is not None and value < 0:
            parser.error(f"--{name.replace('_', '-')} must be >= 0")
    min_interval = getattr(args, "min_interval", None)
    if min_interval is not None and min_interval < 0:
        parser.error("--min-interval must be >= 0")

    subject = getattr(args, "subject", None)
    if not subject:
        return
    try:
        if args.command in ("harvest", "extract"):
            chosen = resolve_subject(args.site, subject)
            url = getattr(args, "url", None)
            if url:
                detected = detect_subject(args.site, url)
                if detected is not None and detected.code != chosen.code:
                    parser.error(
                        f"--subject {chosen.display}（{chosen.code}）"
                        f"与 URL 学科 {detected.display}（{detected.code}）不一致"
                    )
        elif args.command == "sites":
            resolve_subject_any(subject)
        else:  # export / list: filter by the subject recorded in SQLite
            resolved = resolve_subject_any(subject)
            stage = getattr(args, "stage", None)
            if stage and stage != resolved.stage:
                parser.error(
                    f"--subject {resolved.display} 与 --stage {stage} 不一致"
                )
    except ValueError as exc:
        parser.error(str(exc))


def _validate_export_source(parser: argparse.ArgumentParser, args, config) -> None:
    """Reject --source values that are not present in the database.

    ``export``/``list`` used to offer a hard-coded choice list that drifted from
    what was actually stored (e.g. baidu_tiku could not be exported).  Validate
    against the database instead so any imported source works.
    """
    if args.command not in ("export", "list"):
        return
    source = getattr(args, "source", None)
    if not source:
        return
    from k12_crawler.storage import Storage

    storage = Storage(config.db_path)
    try:
        known = sorted(storage.counts_by_source())
    finally:
        storage.close()
    if source not in known:
        parser.error(
            f"unknown --source {source!r}; known sources: {', '.join(known) or '(none)'}"
        )


def _config_from_args(args: argparse.Namespace):
    overrides: Dict[str, Any] = {}
    if getattr(args, "min_interval", None) is not None:
        overrides["min_interval"] = args.min_interval
    return load_config(args.config, overrides)


def _dispatch(args: argparse.Namespace, config) -> Dict[str, Any]:
    if args.command == "harvest":
        return workflow.harvest(
            config,
            site=args.site,
            subject=args.subject,
            seed=args.url,
            max_units=args.max_units,
            max_pages=args.max_pages,
            max_questions=args.max_questions,
            min_interval=args.min_interval,
            out_dir=args.out,
            download_images=args.images,
            force=args.force,
        )

    if args.command == "extract":
        return workflow.extract(
            config,
            site=args.site,
            url=args.url,
            subject=args.subject,
            download_images=args.images,
            min_interval=args.min_interval,
        )

    if args.command == "sites":
        catalog = site_catalog(args.subject)
        return {"status": "ok", "sites": catalog, "count": len(catalog)}

    if args.command == "stats":
        result = workflow.stats(config)
        result.setdefault("status", "ok")
        return result

    if args.command == "export":
        subject = resolve_subject_any(args.subject) if args.subject else None
        if subject is not None:
            apply_to_config(config, subject)
        output = args.output
        if output is None and subject is not None:
            output = config.export_dir / "exports" / export_basename(subject)
        result = workflow.export(
            config,
            output=output,
            paper_ids=args.paper_id,
            source=args.source,
            validation_status=args.status,
            stage=args.stage or (subject.stage if subject else None),
            subject=subject.name if subject else None,
            limit=args.limit,
        )
        result.setdefault("status", "ok")
        return result

    if args.command == "list":
        subject = resolve_subject_any(args.subject) if args.subject else None
        subject_name = subject.name if subject else None
        stage = subject.stage if subject else None
        if args.list_type == "questions":
            items = workflow.list_questions(
                config,
                limit=args.limit,
                paper_id=(args.paper_id[0] if args.paper_id else None),
                source=args.source,
                validation_status=args.status,
                stage=stage,
                subject=subject_name,
            )
        else:
            items = workflow.list_papers(config, limit=args.limit, source=args.source)
            if subject_name:
                items = [
                    item for item in items if item.get("subject") == subject_name
                ]
        return {
            "status": "ok",
            "type": args.list_type,
            "count": len(items),
            "items": items,
        }

    if args.command == "import-items":
        return workflow.import_items(
            config,
            items=args.items,
            release_id=args.release_id,
            dry_run=args.dry_run,
            revalidate=args.revalidate,
            check_files=not args.no_check_files,
        )

    raise ValueError(f"unknown command: {args.command}")


def _format_list_item(item_type: str, item: Dict[str, Any]) -> str:
    if item_type == "questions":
        return (
            f"{item.get('source')}:{item.get('source_question_id')} "
            f"{item.get('question_type')} {item.get('choice_type')} "
            f"answer={item.get('answer')} status={item.get('validation_status')} "
            f"stem={str(item.get('stem_text'))[:70]}"
        )
    if item_type == "papers":
        return (
            f"{item.get('source')}:{item.get('source_paper_id')} "
            f"year={item.get('year')} region={item.get('region')} "
            f"stored={item.get('stored_questions')}/{item.get('question_count')} "
            f"title={str(item.get('title'))[:70]}"
        )
    if item_type == "tasks":
        return (
            f"job={item.get('job_id')} {item.get('task_key')} "
            f"{item.get('kind')} status={item.get('status')} "
            f"attempts={item.get('attempts')}/{item.get('max_attempts')} "
            f"error={str(item.get('error_message') or '')[:80]}"
        )
    return (
        f"job={item.get('id')} {item.get('command')} status={item.get('status')} "
        f"papers={item.get('papers_completed')} questions={item.get('questions_stored')} "
        f"cap_reached={item.get('cap_reached')} error={str(item.get('error') or '')[:80]}"
    )


def _print_result(result: Dict[str, Any], json_output: bool, command: str) -> None:
    if (
        command == "list"
        and not json_output
        and result.get("status") in (None, "ok")
    ):
        for item in result.get("items", []):
            print(_format_list_item(str(result.get("type")), item))
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _exit_code(status: Optional[str]) -> int:
    if status == "blocked":
        return EXIT_BLOCKED
    if status in ("failed", "partial", "interrupted", "error"):
        return EXIT_FAILURE
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdio()
    args_list = list(argv) if argv is not None else sys.argv[1:]
    _Parser.json_requested = "--json" in args_list
    parser = build_parser()
    args = parser.parse_args(args_list)
    json_output = bool(
        getattr(args, "json_global", False) or getattr(args, "json", False)
    )

    try:
        _validate_values(parser, args)
        config = _config_from_args(args)
        _validate_export_source(parser, args, config)
        result = _dispatch(args, config)
    except SystemExit:
        raise
    except AccessBlocked as exc:
        result = {
            "status": "blocked",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _print_result(result, json_output or args.command in ("run", "crawl", "parse"), args.command)
        return EXIT_BLOCKED
    except KeyboardInterrupt:
        result = {
            "status": "interrupted",
            "error": "interrupted by user; use --resume to continue",
        }
        _print_result(result, json_output or args.command in ("run", "crawl", "parse"), args.command)
        return EXIT_FAILURE
    except Exception as exc:  # configuration/IO errors, unexpected bugs
        result = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _print_result(result, json_output or args.command in ("run", "crawl", "parse"), args.command)
        return EXIT_FAILURE

    _print_result(result, json_output, args.command)
    return _exit_code(result.get("status"))


if __name__ == "__main__":
    sys.exit(main())
