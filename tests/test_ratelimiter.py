"""限流器单元测试（DESIGN.md §12.7）：请求间隔、429 退避、403 停止。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from steam_monitor.steam_api import (
    RateLimiter,
    SteamBlockedError,
    SteamRateLimitError,
)


class FakeClock:
    """可注入的假时钟：sleep 会推进内部时间，并记录每次 sleep 的时长。"""

    def __init__(self, start: float = 1000.0):
        self._time = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._time += seconds


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeSession:
    """按顺序返回预设响应；记录每次请求的 headers / timeout。"""

    def __init__(self, statuses: list[int]):
        self.statuses = list(statuses)
        self.calls: list[dict] = []

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, "timeout": timeout})
        status = self.statuses.pop(0)
        if status is None:
            raise requests.exceptions.ConnectionError("网络不可达")
        return FakeResponse(status)


def make_limiter(session: FakeSession, clock: FakeClock, **kw) -> RateLimiter:
    """间隔置零（仅测退避时），rand 固定 0.5。"""
    defaults = dict(min_interval=0.0, max_interval=0.0, sleep=clock.sleep, monotonic=clock.monotonic, rand=lambda: 0.5)
    defaults.update(kw)
    return RateLimiter(session, **defaults)


class TestRequestSpacing:
    def test_first_request_no_wait_then_interval_applied(self):
        clock = FakeClock()
        session = FakeSession([200, 200])
        limiter = make_limiter(session, clock, min_interval=1.5, max_interval=2.0, rand=lambda: 0.5)
        limiter.request("GET", "http://x/1")
        assert clock.sleeps == []  # 首次请求不等
        limiter.request("GET", "http://x/2")
        # 间隔 1.5 + 0.5*(2.0-1.5) = 1.75s
        assert clock.sleeps == [1.75]

    def test_interval_within_1_5_2_seconds(self):
        clock = FakeClock()
        session = FakeSession([200, 200])
        limiter = make_limiter(session, clock, min_interval=1.5, max_interval=2.0, rand=lambda: 0.0)
        limiter.request("GET", "http://x/1")
        limiter.request("GET", "http://x/2")
        assert clock.sleeps == [1.5]
        clock2 = FakeClock()
        session2 = FakeSession([200, 200])
        limiter2 = make_limiter(session2, clock2, min_interval=1.5, max_interval=2.0, rand=lambda: 1.0)
        limiter2.request("GET", "http://x/1")
        limiter2.request("GET", "http://x/2")
        assert clock2.sleeps == [2.0]

    def test_ua_header_and_timeout(self):
        clock = FakeClock()
        session = FakeSession([200])
        limiter = make_limiter(session, clock)
        limiter.request("GET", "http://x/1")
        assert session.calls[0]["headers"]["User-Agent"].startswith("Mozilla/5.0")
        assert session.calls[0]["timeout"] == (10.0, 30.0)


class TestBackoff:
    def test_429_retries_with_exponential_backoff_then_succeeds(self):
        clock = FakeClock()
        # 429, 429, 429, 200 → 重试 3 次后成功
        session = FakeSession([429, 429, 429, 200])
        limiter = make_limiter(session, clock)
        resp = limiter.request("GET", "http://x/1")
        assert resp.status_code == 200
        assert clock.sleeps == [1.5, 3.0, 6.0]  # 1.5s×2 指数退避

    def test_429_exhausted_raises_rate_limit_error(self):
        clock = FakeClock()
        session = FakeSession([429, 429, 429, 429])
        limiter = make_limiter(session, clock)
        with pytest.raises(SteamRateLimitError):
            limiter.request("GET", "http://x/1")
        assert clock.sleeps == [1.5, 3.0, 6.0]
        assert limiter.blocked is False  # 只有 403 才算被封

    def test_backoff_capped_at_60s(self):
        clock = FakeClock()
        # 用 max_retries=6 验证上限；429 ×7 全部失败
        session = FakeSession([429] * 7)
        limiter = make_limiter(session, clock, max_retries=6)
        with pytest.raises(SteamRateLimitError):
            limiter.request("GET", "http://x/1")
        assert clock.sleeps == [1.5, 3.0, 6.0, 12.0, 24.0, 48.0]  # 6 次退避均 < 60
        # 1.5×2^6 = 96 → 应封顶 60；构造一次超上限的退避
        clock2 = FakeClock()
        session2 = FakeSession([429] * 8)
        limiter2 = make_limiter(session2, clock2, max_retries=7)
        with pytest.raises(SteamRateLimitError):
            limiter2.request("GET", "http://x/1")
        assert clock2.sleeps[-1] == 60.0  # 第 7 次退避封顶

    def test_403_sets_blocked_and_raises(self):
        clock = FakeClock()
        session = FakeSession([403, 403, 403, 403])
        limiter = make_limiter(session, clock)
        with pytest.raises(SteamBlockedError):
            limiter.request("GET", "http://x/1")
        assert limiter.blocked is True
        assert clock.sleeps == [1.5, 3.0, 6.0]

    def test_403_retried_until_success(self):
        clock = FakeClock()
        session = FakeSession([403, 200])
        limiter = make_limiter(session, clock)
        resp = limiter.request("GET", "http://x/1")
        assert resp.status_code == 200
        assert limiter.blocked is False
        assert clock.sleeps == [1.5]


class TestNetworkErrors:
    def test_connection_error_raises_steam_request_error(self):
        clock = FakeClock()
        session = FakeSession([None])
        limiter = make_limiter(session, clock)
        with pytest.raises(Exception) as exc_info:
            limiter.request("GET", "http://x/1")
        from steam_monitor.steam_api import SteamRequestError

        assert isinstance(exc_info.value, SteamRequestError)
        assert not isinstance(exc_info.value, SteamBlockedError)
