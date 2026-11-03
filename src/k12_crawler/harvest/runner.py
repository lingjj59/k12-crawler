"""Resumable harvesting loop: units in, raw pages + JSONL out.

The runner knows nothing about papers, lists or APIs.  It walks a queue of
:class:`~k12_crawler.harvest.unit.Unit` objects:

    fetch(unit) -> process(unit, raw) -> PageResult(questions, children)

The adapter decides what a page contributes; the runner only persists the
questions and enqueues the children.  That is enough to model a paper list
(index expands into papers), a JSON API (index expands into paged kp units),
and anything deeper.  Politeness, robots, retries, raw storage, resume state
and item de-duplication live here once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from ..config import PROJECT_ROOT, Config
from ..fetcher import AccessBlocked, Fetcher, FetchError, NotFoundError
from ..models import Question, now_iso
from ..subjects import (
    DEFAULT_SUBJECT_CODE,
    Subject,
    apply_subject_paths,
    default_subject,
    detect_subject,
    resolve_subject,
)
from .registry import build_site, get_site
from .site import PageResult, Site
from .unit import Unit

USER_AGENT = "K12Harvester/2.0 (agent crawler; polite single-process)"


def _log(message: str) -> None:
    try:
        print(message, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stderr, "encoding", None) or "ascii"
        sys.stderr.write(
            message.encode(encoding, "backslashreplace").decode(encoding) + "\n"
        )
        sys.stderr.flush()


def _config(subject: Subject, min_interval: float, timeout: float) -> Config:
    config = Config()
    config.subject_code = subject.code
    config.subject = subject.name
    config.stage = subject.stage
    config.subject_slug = subject.slug
    apply_subject_paths(config)
    config.min_interval = max(0.0, float(min_interval))
    config.timeout = max(5.0, float(timeout))
    config.max_retries = 3
    config.user_agent = USER_AGENT
    return config


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "unknown"


def _relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _append_lines(path: Path, lines: List[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
    return records


def _rebuild_items(units_dir: Path, items_path: Path) -> Tuple[int, List[str]]:
    """Rebuild ``items.jsonl`` from per-unit files.

    Per-unit files are written atomically, so they are the source of truth
    after a crash.  Rebuilding also removes duplicates produced by a rerun.
    """
    unique: Dict[str, Dict[str, Any]] = {}
    done: List[str] = []
    for path in sorted(units_dir.glob("*.jsonl")):
        records = _read_jsonl(path)
        if records:
            done.append(str(records[0].get("unit_key") or records[0].get("paper_id") or path.stem))
        for record in records:
            key = "|".join(
                [
                    str(record.get("site") or ""),
                    str(record.get("source_question_id") or ""),
                ]
            )
            unique[key] = record

    ordered = sorted(
        unique.values(),
        key=lambda item: (
            str(item.get("paper_id") or ""),
            int(item.get("question_number") or 0),
        ),
    )
    text = "".join(
        json.dumps(item, ensure_ascii=False) + "\n" for item in ordered
    )
    _atomic_write_text(items_path, text)
    return len(ordered), done


class _Robots:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self.cache: Dict[str, Optional[RobotFileParser]] = {}

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc
        if host not in self.cache:
            try:
                text = self.fetcher.get_text(f"{parsed.scheme}://{host}/robots.txt")
                parser = RobotFileParser()
                parser.parse(text.splitlines())
                self.cache[host] = parser
            except Exception:
                # No robots.txt (or it is unreachable): do not block the run,
                # but keep the normal polite rate limit.
                self.cache[host] = None
        parser = self.cache[host]
        if parser is None:
            return True
        return parser.can_fetch(USER_AGENT, url)


def _question_records(site: Site, unit: Unit, questions: List[Question]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for question in questions:
        if unit.kind != "detail":
            question.paper_id = unit.document_id
            question.paper_title = unit.document_title
            question.paper_url = unit.document_url
        record = question.to_dict()
        record["site"] = site.name
        record["paper_id"] = unit.document_id
        record["paper_title"] = unit.document_title
        record["paper_url"] = unit.document_url
        record["unit_key"] = unit.key
        records.append(record)
    return records


def _download_images(
    fetcher: Fetcher,
    questions: List[Question],
    image_dir: Path,
) -> Tuple[int, int]:
    downloaded = 0
    failed = 0
    for question in questions:
        for resource in question.resources:
            if not resource.original_url or resource.local_path:
                continue
            if resource.original_url.startswith("data:"):
                resource.conversion_status = "skipped"
                continue
            path_part = urlparse(resource.original_url).path
            extension = path_part.rsplit(".", 1)[-1].lower() if "." in path_part else "png"
            if extension in ("wmf", "emf"):
                # Keep the original vector format; renaming it to .png would
                # produce files browsers cannot decode.  Downstream conversion
                # can pick these up via conversion_status=unsupported.
                pass
            elif extension == "jpeg":
                extension = "jpg"
            elif extension not in ("png", "jpg", "gif", "webp", "svg", "bmp"):
                extension = "png"
            digest = hashlib.sha1(resource.original_url.encode("utf-8")).hexdigest()
            target = image_dir / f"{digest}.{extension}"
            try:
                if not target.exists():
                    target.write_bytes(fetcher.get_bytes(resource.original_url))
                resource.local_path = _relpath(target)
                resource.format = extension
                if extension in ("wmf", "emf"):
                    resource.conversion_status = "unsupported"
                    resource.error = (
                        f"real {extension.upper()} content; conversion unavailable"
                    )
                else:
                    resource.conversion_status = "downloaded"
                downloaded += 1
            except AccessBlocked:
                raise
            except Exception as exc:  # a broken image must not kill the unit
                resource.conversion_status = "failed"
                resource.error = f"{type(exc).__name__}: {exc}"
                failed += 1
    return downloaded, failed


def _load_state(path: Path, site: str, subject: Subject) -> Dict[str, Any]:
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                state.setdefault("units", {})
                # 0.6 stored progress under "papers"; keys are unchanged for
                # jyeoo/cooco, so this is a pure rename.
                for key, value in (state.pop("papers", {}) or {}).items():
                    state["units"].setdefault(key, value)
                state["site"] = site
                state["subject_code"] = subject.code
                state["subject"] = subject.name
                state["stage"] = subject.stage
                return state
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "site": site,
        "subject_code": subject.code,
        "subject": subject.name,
        "stage": subject.stage,
        "units": {},
        "questions": 0,
        "updated_at": now_iso(),
    }


def _migrate_flat_layout(site: str, output: Path) -> None:
    """Move a pre-0.6 flat ``data/raw/<site>/`` harvest into ``<subject>/``."""
    flat = PROJECT_ROOT / "data" / "raw" / site
    if output == flat or output.exists():
        return
    markers = ("units", "papers", "items.jsonl", "state.json")
    if not any((flat / marker).exists() for marker in markers):
        return
    output.mkdir(parents=True, exist_ok=True)
    for name in (*markers, "images", "last_run.json"):
        source = flat / name
        target = output / name
        if source.exists() and not target.exists():
            shutil.move(str(source), str(target))
    _log(f"[migrate] {_relpath(flat)} -> {_relpath(output)}")


def _prepare_layout(output: Path) -> Tuple[Path, Path]:
    """0.6 used ``papers/``; 2.0 calls the same directory ``units/``."""
    units_dir = output / "units"
    legacy = output / "papers"
    if legacy.exists() and not units_dir.exists():
        legacy.rename(units_dir)
    units_dir.mkdir(parents=True, exist_ok=True)
    image_dir = output / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    return units_dir, image_dir


def run_harvest(
    site: str = "jyeoo",
    subject: Optional[str] = None,
    seed: Optional[str] = None,
    max_units: int = 20,
    max_pages: int = 5,
    max_questions: int = 0,
    min_interval: float = 1.5,
    out_dir: Optional[str] = None,
    download_images: bool = False,
    force: bool = False,
    timeout: float = 25.0,
) -> Dict[str, Any]:
    started = time.monotonic()
    detected = detect_subject(site, seed) if seed else None
    if subject:
        chosen = resolve_subject(site, subject)
        if detected is not None and detected.code != chosen.code:
            raise ValueError(
                f"URL 属于 {detected.display}（{detected.code}），"
                f"与 --subject {chosen.display}（{chosen.code}）不一致"
            )
    else:
        chosen = detected or default_subject(site)

    config = _config(chosen, min_interval, timeout)
    site_obj = build_site(site, chosen)
    seed_url = (seed or site_obj.seed_for(chosen)).strip()
    if not seed_url:
        raise ValueError(f"site {site} has no default seed; pass --url")

    default_output = Path(config.raw_dir) / site / chosen.code
    if out_dir:
        output = Path(out_dir).expanduser()
        if not output.is_absolute():
            output = (PROJECT_ROOT / output).resolve()
    else:
        # Pre-0.6 flat directories only ever held the default (physics)
        # channel; never migrate them into another subject.
        if chosen.code == DEFAULT_SUBJECT_CODE:
            _migrate_flat_layout(site, default_output)
        output = default_output
    output.mkdir(parents=True, exist_ok=True)
    units_dir, image_dir = _prepare_layout(output)
    items_path = output / "items.jsonl"
    state_path = output / "state.json"

    question_count, unit_files = _rebuild_items(units_dir, items_path)
    initial_questions = question_count
    state = _load_state(state_path, site, chosen)
    for key in unit_files:
        state["units"].setdefault(
            key, {"status": "done", "questions": 0, "recovered": True}
        )
    state["questions"] = question_count

    summary: Dict[str, Any] = {
        "status": "completed",
        "site": site,
        "subject_code": chosen.code,
        "subject": chosen.name,
        "stage": chosen.stage,
        "seed": seed_url,
        "out_dir": str(output),
        "items_path": _relpath(items_path),
        "state_path": _relpath(state_path),
        "units_seen": 0,
        "units_fetched": 0,
        "units_skipped": 0,
        "units_failed": 0,
        "index_pages": 0,
        "questions": question_count,
        "questions_new": 0,
        "images_downloaded": 0,
        "images_failed": 0,
        "errors": [],
        "question_limit_reached": False,
    }

    fetcher = Fetcher(config)
    robots = _Robots(fetcher)

    def record_error(kind: str, url: str, exc: BaseException) -> None:
        if len(summary["errors"]) < 50:
            summary["errors"].append(
                {
                    "kind": kind,
                    "url": url,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
            )

    queue = deque([site_obj.seed_unit(seed_url)])
    seen = set()
    leaf_fetched = 0
    limit_reached = False

    try:
        while queue:
            unit = queue.popleft()
            if unit.key in seen:
                continue
            seen.add(unit.key)

            if unit.is_index and int(unit.meta.get("page") or 1) > max(1, max_pages):
                continue
            if unit.is_leaf:
                summary["units_seen"] += 1
                if max_units and leaf_fetched >= max_units:
                    break
                if max_questions and question_count >= max_questions:
                    summary["question_limit_reached"] = True
                    limit_reached = True
                    break

            raw_path = units_dir / f"{_safe_name(unit.key)}{unit.suffix}"
            unit_jsonl = units_dir / f"{_safe_name(unit.key)}.jsonl"
            done = state["units"].get(unit.key, {}).get("status") == "done"
            cached = (
                done
                and raw_path.exists()
                and (not unit.is_leaf or unit_jsonl.exists())
                and not force
            )
            if cached:
                raw = _read_text(raw_path) or ""
                if unit.is_leaf:
                    summary["units_skipped"] += 1
            else:
                if not robots.allowed(unit.url):
                    if unit.is_leaf:
                        summary["units_skipped"] += 1
                    record_error(
                        "robots",
                        unit.url,
                        PermissionError("disallowed by robots.txt"),
                    )
                    continue
                _log(f"[{unit.kind}] {unit.url}")
                try:
                    raw = site_obj.fetch(unit, fetcher)
                except AccessBlocked:
                    raise
                except (NotFoundError, FetchError) as exc:
                    if unit.is_leaf:
                        summary["units_failed"] += 1
                    record_error("fetch", unit.url, exc)
                    continue
                _atomic_write_text(raw_path, raw)
                if unit.is_leaf:
                    leaf_fetched += 1
                    summary["units_fetched"] += 1
                else:
                    summary["index_pages"] += 1

            if cached:
                # Already parsed on an earlier run.  Only follow its links so
                # a partially expanded queue can resume without re-parsing
                # every stored question.
                try:
                    result = PageResult(children=list(site_obj.expand(unit, raw)))
                except Exception as exc:
                    if unit.is_leaf:
                        summary["units_failed"] += 1
                    record_error("expand", unit.url, exc)
                    result = PageResult()
                questions = []
            else:
                try:
                    result = site_obj.process(unit, raw)
                except Exception as exc:
                    if unit.is_leaf:
                        summary["units_failed"] += 1
                    record_error("process", unit.url, exc)
                    continue
                questions = list(result.questions)

            if (
                not cached
                and unit.is_leaf
                and not questions
                and not site_obj.allow_empty
            ):
                summary["units_failed"] += 1
                record_error(
                    "process",
                    unit.url,
                    ValueError("0 questions parsed; selector/structure changed?"),
                )
                continue

            if questions:
                if download_images:
                    downloaded, failed = _download_images(
                        fetcher, questions, image_dir
                    )
                    summary["images_downloaded"] += downloaded
                    summary["images_failed"] += failed

                records = _question_records(site_obj, unit, questions)
                payload = "".join(
                    json.dumps(record, ensure_ascii=False) + "\n"
                    for record in records
                )
                if unit.is_leaf:
                    _atomic_write_text(unit_jsonl, payload)
                _append_lines(items_path, payload.splitlines())

                question_count += len(records)
                summary["questions"] = question_count
            elif unit.is_leaf and not cached:
                _atomic_write_text(unit_jsonl, "")

            children = list(result.children)
            state_questions = len(questions)
            if cached:
                state_questions = int(
                    state["units"].get(unit.key, {}).get("questions") or 0
                )
            state["units"][unit.key] = {
                "status": "done",
                "kind": unit.kind,
                "questions": state_questions,
                "url": unit.url,
                "title": unit.document_title,
                "fetched_at": now_iso(),
            }
            state["questions"] = question_count
            state["updated_at"] = now_iso()
            _atomic_write_json(state_path, state)
            summary["questions"] = question_count
            if unit.is_leaf:
                _log(
                    f"[{unit.kind}] {unit.key} questions={state_questions} "
                    f"title={unit.document_title[:60]}"
                )
            queue.extend(children)

        if summary["units_failed"] or summary["errors"]:
            summary["status"] = "partial"
        if (
            summary["units_fetched"] == 0
            and summary["units_skipped"] == 0
            and not summary["question_limit_reached"]
            and not limit_reached
        ):
            summary["status"] = "partial"
            summary["errors"].append(
                {
                    "kind": "discovery",
                    "url": seed_url,
                    "error_type": "NoUnits",
                    "message": "no units were discovered; check --url/selectors",
                }
            )

    except AccessBlocked as exc:
        summary["status"] = "blocked"
        record_error("access", seed_url, exc)
        _log(f"[BLOCKED] {exc}")
    except Exception as exc:
        summary["status"] = "failed"
        record_error("run", seed_url, exc)
        _log(f"[FAILED] {type(exc).__name__}: {exc}")
    finally:
        fetcher.close()

    # Final rebuild: crash-safe and duplicate-free.
    question_count, _ = _rebuild_items(units_dir, items_path)
    summary["questions"] = question_count
    summary["questions_new"] = max(0, question_count - initial_questions)
    summary["updated_at"] = now_iso()
    summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
    state["questions"] = question_count
    state["updated_at"] = now_iso()
    _atomic_write_json(state_path, state)
    _atomic_write_json(output / "last_run.json", summary)
    _log(
        f"[done] site={site} status={summary['status']} "
        f"units={summary['units_fetched']} questions={summary['questions']}"
    )
    return summary


def extract_url(
    site: str,
    url: str,
    subject: Optional[str] = None,
    download_images: bool = False,
    min_interval: float = 1.5,
    timeout: float = 25.0,
) -> Dict[str, Any]:
    """Fetch and parse one leaf URL; return JSON, write nothing."""
    explicit = resolve_subject(site, subject) if subject else None
    chosen = explicit or detect_subject(site, url) or default_subject(site)
    site_obj = build_site(site, chosen)
    if not site_obj.supports(url):
        return {
            "status": "failed",
            "site": site,
            "url": url,
            "error": f"{site} site does not recognise this URL",
        }

    config = _config(chosen, min_interval, timeout)
    fetcher = Fetcher(config)
    try:
        robots = _Robots(fetcher)
        if not robots.allowed(url):
            return {
                "status": "blocked",
                "site": site,
                "url": url,
                "error": "disallowed by robots.txt",
            }

        unit = Unit(
            key="extract",
            url=url,
            kind="detail",
            suffix=getattr(site_obj, "raw_suffix", ".html"),
        )
        raw = site_obj.fetch(unit, fetcher)

        # Paper URLs (/pp/<uuid>) do not carry the subject in the path; use the
        # page's own title/meta keywords before parsing.
        detected = site_obj.detect(url, raw)
        if detected is not None and explicit is not None and detected.code != explicit.code:
            return {
                "status": "failed",
                "site": site,
                "url": url,
                "error": (
                    f"页面属于 {detected.display}（{detected.code}），"
                    f"与 --subject {explicit.display}（{explicit.code}）不一致"
                ),
            }
        if detected is not None and explicit is None:
            chosen = detected
            config = _config(chosen, min_interval, timeout)
            site_obj = build_site(site, chosen)
        elif explicit is None and detected is None:
            return {
                "status": "failed",
                "site": site,
                "url": url,
                "error": "无法从 URL/页面识别学科，请加 --subject",
            }

        questions = site_obj.parse(unit, raw)
        if download_images and questions:
            image_dir = Path(config.raw_dir) / site / chosen.code / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            _download_images(fetcher, questions, image_dir)

        return {
            "status": "ok",
            "site": site,
            "subject_code": chosen.code,
            "subject": chosen.name,
            "stage": chosen.stage,
            "url": url,
            "document": {
                "key": unit.key,
                "kind": unit.kind,
                "document_id": unit.document_id,
                "title": unit.document_title,
                "url": unit.document_url,
            },
            "questions": _question_records(site_obj, unit, questions),
        }
    except AccessBlocked as exc:
        return {
            "status": "blocked",
            "site": site,
            "url": url,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "site": site,
            "url": url,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finally:
        fetcher.close()
