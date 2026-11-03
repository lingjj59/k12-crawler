from __future__ import annotations

import pytest

from k12_crawler.config import Config
from k12_crawler.fetcher import AccessBlocked, Fetcher, NotFoundError


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": "text/html"}


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


def _config() -> Config:
    return Config(min_interval=0, max_retries=2, timeout=1)


def test_403_stops_immediately():
    session = FakeSession([FakeResponse(403)])
    fetcher = Fetcher(_config(), session=session)  # type: ignore[arg-type]
    with pytest.raises(AccessBlocked):
        fetcher.get_text("http://example.test/x")
    assert session.calls == 1


def test_404_is_not_retried():
    session = FakeSession([FakeResponse(404)])
    fetcher = Fetcher(_config(), session=session)  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        fetcher.get_text("http://example.test/x")
    assert session.calls == 1


def test_500_is_retried(monkeypatch):
    session = FakeSession([FakeResponse(500), FakeResponse(200, b"<html>ok</html>")])
    fetcher = Fetcher(_config(), session=session)  # type: ignore[arg-type]
    monkeypatch.setattr("k12_crawler.fetcher.time.sleep", lambda seconds: None)
    assert "ok" in fetcher.get_text("http://example.test/x")
    assert session.calls == 2


def test_captcha_content_is_blocked():
    session = FakeSession([FakeResponse(200, "请完成安全验证 验证码".encode("utf-8"))])
    fetcher = Fetcher(_config(), session=session)  # type: ignore[arg-type]
    with pytest.raises(AccessBlocked):
        fetcher.get_text("http://example.test/x")
