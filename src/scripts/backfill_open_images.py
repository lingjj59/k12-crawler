#!/usr/bin/env python3
"""Backfill images for open-dataset questions already in SQLite.

The first bulk ingest can run with ``--no-images`` so counts are fast.  This
script then downloads the recorded ``resources.original_url`` values with a
small thread pool, writes the content-addressed files, and marks failures
instead of hiding them.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from k12_crawler.config import PROJECT_ROOT, load_config
from k12_crawler.html_utils import extension_for_format, sniff_image_format
from k12_crawler.storage import Storage

REL = lambda p: str(p.resolve().relative_to(PROJECT_ROOT))


def download_one(row, image_dir: Path, session: requests.Session):
    url = row["original_url"]
    try:
        response = session.get(url, timeout=40)
        if response.status_code >= 400:
            return row["id"], None, f"HTTP {response.status_code}"
        data = response.content
        fmt = sniff_image_format(data, url)
        ext = extension_for_format(fmt)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        destination = image_dir / f"{digest}.{ext}"
        if not destination.exists():
            destination.write_bytes(data)
        width = height = None
        if fmt in ("png", "jpg", "gif", "bmp", "webp"):
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
        return row["id"], {
            "local_path": REL(destination),
            "format": fmt,
            "width": width,
            "height": height,
            "status": "not_needed",
        }, None
    except Exception as exc:  # network, decode, filesystem
        return row["id"], None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="multiphysics")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    config = load_config()
    storage = Storage(config.db_path)
    rows = storage.conn.execute(
        """
        SELECT r.id, r.original_url
        FROM resources r JOIN questions q ON q.id=r.question_id
        WHERE q.source=? AND (r.local_path IS NULL OR r.local_path='')
          AND (r.conversion_status IN ('skipped','pending','download_failed')
               OR r.conversion_status IS NULL)
        ORDER BY r.id
        """,
        (args.source,),
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    image_dir = config.image_dir / args.source / "_shared"
    image_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "K12Crawler/0.4 (open-dataset image backfill; educational)",
            "Accept": "image/*,*/*;q=0.8",
        }
    )
    ok = failed = 0
    errors = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(download_one, row, image_dir, session) for row in rows]
        for future in as_completed(futures):
            resource_id, payload, error = future.result()
            if payload:
                storage.conn.execute(
                    "UPDATE resources SET local_path=?, format=?, width=?, height=?, "
                    "conversion_status=?, error=NULL WHERE id=?",
                    (
                        payload["local_path"],
                        payload["format"],
                        payload["width"],
                        payload["height"],
                        payload["status"],
                        resource_id,
                    ),
                )
                ok += 1
            else:
                storage.conn.execute(
                    "UPDATE resources SET conversion_status='download_failed', error=? WHERE id=?",
                    (error, resource_id),
                )
                failed += 1
                if len(errors) < 20:
                    errors.append({"resource_id": resource_id, "error": error})
            storage.conn.commit()
    # Any question with a failed image is explicitly marked fail.
    storage.conn.execute(
        """
        UPDATE questions SET validation_status='fail'
        WHERE source=? AND id IN (
            SELECT q.id FROM questions q JOIN resources r ON r.question_id=q.id
            WHERE q.source=? AND r.conversion_status IN ('download_failed','missing_url')
        )
        """,
        (args.source, args.source),
    )
    storage.conn.commit()
    result = {
        "source": args.source,
        "candidates": len(rows),
        "downloaded": ok,
        "failed": failed,
        "errors": errors,
        "db_resources": storage.conn.execute(
            "SELECT COUNT(*) c FROM resources r JOIN questions q ON q.id=r.question_id WHERE q.source=?",
            (args.source,),
        ).fetchone()["c"],
        "db_resources_with_file": storage.conn.execute(
            "SELECT COUNT(*) c FROM resources r JOIN questions q ON q.id=r.question_id "
            "WHERE q.source=? AND r.local_path IS NOT NULL AND r.local_path<>''",
            (args.source,),
        ).fetchone()["c"],
        "db_questions_failed": storage.conn.execute(
            "SELECT COUNT(*) c FROM questions WHERE source=? AND validation_status='fail'",
            (args.source,),
        ).fetchone()["c"],
    }
    storage.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
