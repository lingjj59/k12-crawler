"""Tiny local HTTP fixture for CLI/end-to-end tests.

It serves the phase-2 *saved* list/paper HTML on 127.0.0.1.  No request ever
leaves the machine, so resume/CLI tests do not expand the site sample.
"""

from __future__ import annotations

import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
LIVE = Path(__file__).resolve().parent / "fixtures" / "live"

PAPERS = {
    "1688525155": "paper_shandong_2022.html",
    "1686470481": "paper_zhejiang_2022.html",
}


class LocalSite:
    def __init__(self, delay_seconds: float = 0.0, delay_paper: Optional[str] = None):
        self.delay_seconds = delay_seconds
        self.delay_paper = delay_paper
        self.fail_paper: Optional[str] = None
        self.fail_count = 0
        self.fail_status = 500
        self.requests: List[Dict[str, object]] = []
        self._lock = threading.Lock()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                with site._lock:
                    site.requests.append(
                        {"path": path, "query": parsed.query, "time": time.monotonic()}
                    )
                if path.startswith("/exam_list"):
                    body = site.list_html().encode("utf-8")
                    content_type = "text/html; charset=utf-8"
                elif path.startswith("/paper/"):
                    paper_id = path.strip("/").split("/")[1]
                    if site.delay_paper == paper_id and site.delay_seconds > 0:
                        time.sleep(site.delay_seconds)
                    should_fail = False
                    with site._lock:
                        if site.fail_paper == paper_id and site.fail_count > 0:
                            site.fail_count -= 1
                            should_fail = True
                    if should_fail:
                        body = b"temporary server error"
                        self.send_response(site.fail_status)
                        self.send_header("Content-Type", "text/plain")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    body = site.paper_html(paper_id).encode("utf-8")
                    content_type = "text/html; charset=utf-8"
                elif path == "/robots.txt":
                    body = b"User-agent: *\nDisallow: /testdetail_old/\n"
                    content_type = "text/plain; charset=utf-8"
                else:
                    body = b"not found"
                    content_type = "text/plain; charset=utf-8"
                    self.send_response(404)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def list_html(self) -> str:
        soup = BeautifulSoup(
            (LIVE / "list_p1.html").read_text(encoding="utf-8"), "lxml"
        )
        items = soup.select("div.m-examList div.item")
        for item in items:
            link = item.select_one('a[href*="/paper/"]')
            match = re.search(r"/paper/(\d+)/", (link.get("href") if link else "") or "")
            if not match or match.group(1) not in PAPERS:
                item.decompose()
        items = soup.select("div.m-examList div.item")
        for item in items[2:]:
            item.decompose()
        for item in soup.select("div.m-examList div.item"):
            link = item.select_one('a[href*="/paper/"]')
            if not link:
                continue
            match = re.search(r"/paper/(\d+)/", link.get("href") or "")
            if match:
                link["href"] = f"/paper/{match.group(1)}/"
        page_input = soup.select_one("#pageIpt")
        if page_input:
            page_input["data-total"] = "1"
        return str(soup)

    def paper_html(self, paper_id: str) -> str:
        filename = PAPERS.get(paper_id)
        if not filename:
            return "<html><head><title>missing</title></head><body></body></html>"
        return (LIVE / filename).read_text(encoding="utf-8")

    def count_path(self, path: str) -> int:
        with self._lock:
            return sum(1 for item in self.requests if item["path"] == path)

    def set_delay(self, seconds: float, paper_id: Optional[str] = None) -> None:
        with self._lock:
            self.delay_seconds = seconds
            self.delay_paper = paper_id

    def set_failure(
        self, paper_id: Optional[str], count: int = 1, status: int = 500
    ) -> None:
        with self._lock:
            self.fail_paper = paper_id
            self.fail_count = count
            self.fail_status = status

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
