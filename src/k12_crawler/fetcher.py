"""Polite HTTP client with retries and hard stops for access restrictions."""

from __future__ import annotations

import time
import warnings
from typing import Optional

import requests

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

from .config import Config


class FetchError(RuntimeError):
    """A page/resource could not be fetched after retries."""


class NotFoundError(FetchError):
    """A 404: retrying will not help."""


class AccessBlocked(FetchError):
    """The site explicitly denied access or returned a captcha/WAF challenge."""


_CAPTCHA_MARKERS = (
    "验证码",
    "访问过于频繁",
    "安全验证",
    "aliyun_waf",
    "acw_sc__v2",
    "请完成验证",
)


class Fetcher:
    def __init__(self, config: Config, session: Optional[requests.Session] = None):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            }
        )
        self._last_request_at = 0.0
        self.request_count = 0
        self.bytes_downloaded = 0
        self.errors = 0

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.config.min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _request(self, url: str, binary: bool = False) -> requests.Response:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.config.max_retries + 1):
            self._wait()
            try:
                response = self.session.get(url, timeout=self.config.timeout)
                self._last_request_at = time.monotonic()
                self.request_count += 1
                self.bytes_downloaded += len(response.content)

                if response.status_code in (403, 429, 451):
                    raise AccessBlocked(
                        f"access blocked: HTTP {response.status_code} for {url}"
                    )
                if response.status_code == 404:
                    raise NotFoundError(f"not found: HTTP 404 for {url}")
                if 500 <= response.status_code < 600:
                    raise FetchError(f"server error: HTTP {response.status_code} for {url}")
                if response.status_code >= 400:
                    raise FetchError(f"HTTP {response.status_code} for {url}")

                if not binary:
                    text = response.content.decode("utf-8", errors="replace")
                    lowered = text.lower()
                    if any(marker.lower() in lowered for marker in _CAPTCHA_MARKERS):
                        raise AccessBlocked(f"captcha/WAF challenge detected for {url}")
                return response
            except AccessBlocked:
                self.errors += 1
                raise
            except NotFoundError:
                self.errors += 1
                raise
            except (requests.RequestException, FetchError) as exc:
                last_error = exc
                self.errors += 1
                if attempt >= self.config.max_retries:
                    break
                # Backoff before the next attempt; the rate limiter still applies.
                time.sleep(min(2 ** attempt, 8))
        raise FetchError(f"failed after {self.config.max_retries} attempts: {url}: {last_error}")

    def get(self, url: str) -> requests.Response:
        return self._request(url, binary=False)

    def get_text(self, url: str) -> str:
        response = self._request(url, binary=False)
        content = response.content
        # cooco is UTF-8; fall back to GB18030 for other adapters/templates.
        text = content.decode("utf-8", errors="replace")
        if text.count("\ufffd") > 20:
            try:
                alt = content.decode("gb18030")
                if alt.count("\ufffd") < text.count("\ufffd"):
                    text = alt
            except UnicodeDecodeError:
                pass
        return text

    def get_bytes(self, url: str) -> bytes:
        return self._request(url, binary=True).content

    def close(self) -> None:
        self.session.close()
