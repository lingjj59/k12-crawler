#!/usr/bin/env python3
"""Download images for the selected Baidu chemistry image questions.

Downloads are resumable: a file already present under
``images/baidu/<qid>/<ordinal>.<ext>`` is reused even when the candidate JSONL
does not yet carry ``local_path``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from k12_crawler.html_utils import extension_for_format, sniff_image_format  # noqa: E402

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://tiku.baidu.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


def _load(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _https(url: str) -> str:
    if not url.startswith("http://"):
        return url
    host = urlparse(url).netloc.lower()
    if host.endswith("baidu.com") or host.endswith("bcebos.com"):
        return "https://" + url[len("http://") :]
    return url


def _existing(qdir: Path, ordinal: int) -> Optional[Path]:
    for path in qdir.glob(f"{ordinal:03d}.*"):
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _download_one(
    qid: str,
    resource: Dict[str, Any],
    image_root: Path,
    interval: float,
) -> Tuple[Dict[str, Any], Optional[str], Optional[str], int, bool]:
    """Return (resource, local_path, format, bytes, skipped)."""
    ordinal = int(resource.get("ordinal") or 0)
    qdir = image_root / qid
    existing = _existing(qdir, ordinal)
    if existing is not None:
        return resource, str(existing.relative_to(PROJECT_ROOT)), None, existing.stat().st_size, True

    url = _https(str(resource.get("original_url") or ""))
    if not url:
        return resource, None, "missing_url", 0, False
    data: Optional[bytes] = None
    error: Optional[Exception] = None
    for attempt in range(1, 5):
        time.sleep(max(0.05, interval * random.uniform(0.8, 1.2)))
        try:
            response = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
            if response.status_code in (403, 429, 451):
                raise RuntimeError(f"access blocked: HTTP {response.status_code}")
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}")
            data = response.content
            if not data:
                raise RuntimeError("empty response")
            fmt = sniff_image_format(data, url)
            if fmt == "bin":
                raise RuntimeError("response does not look like an image")
            break
        except (requests.RequestException, RuntimeError) as exc:
            error = exc
            data = None
            time.sleep(min(2 ** attempt, 8))
    if data is None:
        return resource, None, f"download_failed: {error}", 0, False

    fmt = sniff_image_format(data, url)
    ext = extension_for_format(fmt)
    qdir.mkdir(parents=True, exist_ok=True)
    path = qdir / f"{ordinal:03d}.{ext}"
    path.write_bytes(data)
    return resource, str(path.relative_to(PROJECT_ROOT)), fmt, len(data), False


def main() -> int:
    parser = argparse.ArgumentParser(description="Download batch2 question images")
    parser.add_argument("--input", default="data/chemistry/work/batch02_baidu_2000/candidates.jsonl")
    parser.add_argument("--out", default="data/chemistry/work/batch02_baidu_2000/images/baidu")
    parser.add_argument("--interval", type=float, default=0.8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--slots",
        choices=("image", "no_image", "all"),
        default="image",
        help="which selection slots to download; physics also localizes the "
        "formula fragments kept inside no-image questions",
    )
    args = parser.parse_args()

    input_path = PROJECT_ROOT / args.input
    image_root = PROJECT_ROOT / args.out
    image_root.mkdir(parents=True, exist_ok=True)
    records = _load(input_path)

    tasks: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for record in records:
        slot = record.get("selection_slot")
        if args.slots == "image" and slot != "image":
            continue
        if args.slots == "no_image" and slot != "no_image":
            continue
        for resource in record.get("resources") or []:
            tasks.append((record, resource))
    if args.limit:
        tasks = tasks[: args.limit]

    total = len(tasks)
    downloaded = failed = skipped = 0
    errors: List[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(
                _download_one,
                str(record.get("source_question_id") or ""),
                resource,
                image_root,
                args.interval,
            ): (record, resource)
            for record, resource in tasks
        }
        for future in as_completed(future_map):
            resource, local_path, fmt, size, was_skipped = future.result()
            if local_path and (fmt or was_skipped):
                resource["local_path"] = local_path
                resource["format"] = fmt or resource.get("format")
                resource["original_extension"] = (
                    Path(urlparse(str(resource.get("original_url") or "")).path).suffix.lstrip(".").lower()
                    or None
                )
                resource["conversion_status"] = "not_needed"
                resource["error"] = None
                if was_skipped:
                    skipped += 1
                else:
                    downloaded += 1
                    print(
                        f"[{downloaded}] {local_path} ({size} B)",
                        flush=True,
                    )
            else:
                resource["conversion_status"] = "download_failed"
                resource["error"] = fmt or "unknown error"
                failed += 1
                errors.append(f"{resource.get('ordinal')} {resource.get('original_url')}: {fmt}")
                print(f"FAIL {resource.get('original_url')}: {fmt}", flush=True)

    output = input_path.with_name("candidates_downloaded.jsonl")
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "input": str(input_path.relative_to(PROJECT_ROOT)),
        "output": str(output.relative_to(PROJECT_ROOT)),
        "resources_total": total,
        "downloaded": downloaded,
        "failed": failed,
        "skipped": skipped,
        "errors": errors[:20],
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
