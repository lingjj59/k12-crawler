#!/usr/bin/env python3
"""Download PNG question images for the chemistry dataset.

The chemistry image host (``service.cooco.net.cn``) publishes
``User-agent: * / Disallow: /`` in robots.txt.  This script therefore refuses
to run unless the operator explicitly confirms authorization with
``--authorized``; it also keeps a serial, rate-limited request pattern.

It updates both delivery artifacts after a successful download:

* ``data/chemistry/releases/batch01_cooco_2000/high_school_chemistry.jsonl`` (``resources[].local_path``)
* ``data/shared/k12_high_school.sqlite`` (``resources`` table)

Only ``original_extension == "png"`` resources are touched.  WMF/EMF formula
images remain external links.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JSONL = PROJECT_ROOT / "data" / "chemistry" / "releases" / "batch01_cooco_2000" / "high_school_chemistry.jsonl"
IMAGE_ROOT = PROJECT_ROOT / "data" / "chemistry" / "releases" / "batch01_cooco_2000" / "images" / "cooco"
DB = PROJECT_ROOT / "data" / "shared" / "k12_high_school.sqlite"
USER_AGENT = "K12Crawler/0.6 (course project; image cache; polite single-process)"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _load_records() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with JSONL.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _png_urls(records: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """Return ``url -> role`` for PNG resources, preserving first-seen order."""
    urls: Dict[str, str] = {}
    for record in records:
        for resource in record.get("resources") or []:
            url = resource.get("original_url") or ""
            extension = (resource.get("original_extension") or "").lower()
            if url and extension == "png" and url not in urls:
                urls[url] = resource.get("role") or "image"
    return urls


def _relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_write_jsonl(records: List[Dict[str, Any]]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        dir=str(JSONL.parent), prefix=JSONL.name, suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, JSONL)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _robots_disallows(session: requests.Session) -> bool:
    host = "http://service.cooco.net.cn"
    try:
        response = session.get(host + "/robots.txt", timeout=15)
        response.raise_for_status()
        text = response.text
    except Exception as exc:
        print(f"[warn] could not read robots.txt: {exc}", file=sys.stderr)
        return True
    disallow_root = any(
        line.strip().lower().startswith("disallow:") and line.split(":", 1)[1].strip() == "/"
        for line in text.splitlines()
    )
    return disallow_root


def _download(
    session: requests.Session,
    url: str,
    destination: Path,
    interval: float,
) -> Tuple[str, int]:
    """Return (status, bytes). status is downloaded / skipped / failed."""
    if destination.exists() and destination.stat().st_size > 0:
        if destination.read_bytes()[:8] == PNG_MAGIC:
            return "skipped", destination.stat().st_size
    try:
        response = session.get(
            url,
            timeout=25,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "http://gzhx.cooco.net.cn/",
            },
        )
    except requests.RequestException as exc:
        print(f"[error] {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return "failed", 0
    if response.status_code in (403, 429):
        raise SystemExit(
            f"site blocked the image request (HTTP {response.status_code}); stopping"
        )
    if response.status_code != 200:
        print(f"[error] {url}: HTTP {response.status_code}", file=sys.stderr)
        return "failed", 0
    content = response.content
    if content[:8] != PNG_MAGIC:
        print(f"[error] {url}: response is not a PNG", file=sys.stderr)
        return "failed", 0
    _atomic_write_bytes(destination, content)
    time.sleep(max(0.0, interval))
    return "downloaded", len(content)


def _update_jsonl(records: List[Dict[str, Any]], mapping: Dict[str, str]) -> int:
    changed = 0
    for record in records:
        for resource in record.get("resources") or []:
            local_path = mapping.get(resource.get("original_url") or "")
            if not local_path:
                continue
            resource["local_path"] = local_path
            resource["conversion_status"] = "not_needed"
            resource["format"] = "png"
            resource["error"] = None
            changed += 1
    _atomic_write_jsonl(records)
    return changed


def _update_db(mapping: Dict[str, str]) -> int:
    import sqlite3

    connection = sqlite3.connect(str(DB))
    updated = 0
    try:
        for url, local_path in mapping.items():
            cursor = connection.execute(
                """
                UPDATE resources
                   SET local_path=?, conversion_status='not_needed', format='png', error=NULL
                 WHERE original_url=?
                   AND question_id IN (SELECT id FROM questions WHERE subject='化学')
                """,
                (local_path, url),
            )
            updated += cursor.rowcount
        connection.commit()
    finally:
        connection.close()
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized", action="store_true", help="confirm site authorization")
    parser.add_argument("--dry-run", action="store_true", help="only count and check robots")
    parser.add_argument("--limit", type=int, default=0, help="download at most N PNGs; 0 = all")
    parser.add_argument("--interval", type=float, default=1.2, help="seconds between requests")
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()

    records = _load_records()
    urls = _png_urls(records)
    total = len(urls)
    limit = total if not args.limit else min(total, args.limit)
    print(f"PNG resources: {total} unique URLs; planned: {limit}")

    session = requests.Session()
    disallow = _robots_disallows(session)
    if disallow:
        print(
            "robots.txt for service.cooco.net.cn says Disallow: /. "
            "Refusing to bulk-download without --authorized.",
            file=sys.stderr,
        )
        if not args.authorized:
            return 2
        print("[authorized] continuing after explicit operator confirmation", file=sys.stderr)
    if args.dry_run:
        return 0

    IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
    mapping: Dict[str, str] = {}
    stats = {
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "bytes": 0,
        "failed_urls": [],
    }
    started = time.monotonic()
    for index, url in enumerate(urls, start=1):
        if index > limit:
            break
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        destination = IMAGE_ROOT / f"{digest}.png"
        status, size = _download(session, url, destination, args.interval)
        if status == "failed":
            stats["failed"] += 1
            stats["failed_urls"].append(url)
            continue
        stats[status] += 1
        stats["bytes"] += size
        mapping[url] = _relpath(destination)
        if index % 25 == 0 or index == limit:
            print(
                f"[{index}/{limit}] downloaded={stats['downloaded']} "
                f"skipped={stats['skipped']} failed={stats['failed']} "
                f"{(time.monotonic() - started):.0f}s",
                flush=True,
            )

    session.close()
    jsonl_changed = _update_jsonl(records, mapping)
    db_changed = _update_db(mapping)
    report = {
        "unique_png_urls": total,
        "planned": limit,
        "downloaded": stats["downloaded"],
        "skipped_existing": stats["skipped"],
        "failed": stats["failed"],
        "failed_urls": stats["failed_urls"],
        "bytes": stats["bytes"],
        "jsonl_resources_updated": jsonl_changed,
        "db_resources_updated": db_changed,
        "image_root": _relpath(IMAGE_ROOT),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "robots_disallow": disallow,
        "authorized": args.authorized,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
