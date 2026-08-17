"""Steam 客户端（DESIGN.md §5）。

网络层全部集中在此模块：
- 全局请求节流器（串行、1.5~2s 间隔、429/403 指数退避、403 停止本轮）
- appdetails / search/results / storesearch 三个接口
- 连接超时 10s、读取超时 30s、浏览器风格 UA
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

import requests

__all__ = [
    "AppDetails",
    "BASE_URL",
    "RateLimiter",
    "SteamBlockedError",
    "SteamClient",
    "SteamHTTPError",
    "SteamRateLimitError",
    "SteamRequestError",
    "SteamTimeoutError",
    "USER_AGENT",
]

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

BASE_URL = "https://store.steampowered.com"

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0

#: 退避起点（秒）与上限
BACKOFF_BASE = 1.5
BACKOFF_CAP = 60.0
MAX_RETRIES = 3


class SteamRequestError(Exception):
    """Steam HTTP 请求失败（网络层错误的基类）。"""


class SteamHTTPError(SteamRequestError):
    """HTTP 状态错误（429/403 重试耗尽）。"""

    def __init__(self, message: str, status_code: int | None = None, url: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class SteamBlockedError(SteamHTTPError):
    """403 持续被封：必须停止本轮剩余 Steam 请求。"""


class SteamRateLimitError(SteamHTTPError):
    """429 持续触发：限流。"""


class SteamTimeoutError(SteamRequestError):
    """连接/读取超时。"""


@dataclass
class AppDetails:
    """appdetails 接口解析出的关键字段（§5.1）。"""

    name: str = ""
    appid: int = 0
    type: str = ""
    coming_soon: bool | None = None
    release_date_raw: str = ""
    publishers: list[str] = field(default_factory=list)
    is_free: bool | None = None
    price_final: int | None = None

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> "AppDetails":
        release = data.get("release_date") or {}
        price = data.get("price_overview") or {}
        return cls(
            name=data.get("name", ""),
            appid=data.get("steam_appid") or 0,
            type=data.get("type", ""),
            coming_soon=release.get("coming_soon"),
            release_date_raw=(release.get("date") or "").strip(),
            publishers=list(data.get("publishers") or []),
            is_free=data.get("is_free"),
            price_final=price.get("final"),
        )


class RateLimiter:
    """全局请求节流器：串行请求、1.5~2s 间隔、429/403 指数退避。

    ``sleep`` / ``monotonic`` / ``rand`` 可注入，便于测试（无真实时钟）。
    """

    def __init__(
        self,
        session: requests.Session,
        min_interval: float = 1.5,
        max_interval: float = 2.0,
        backoff_base: float = BACKOFF_BASE,
        backoff_cap: float = BACKOFF_CAP,
        max_retries: int = MAX_RETRIES,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        rand: Callable[[], float] = random.random,
    ):
        self.session = session
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.max_retries = max_retries
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.sleep = sleep
        self.monotonic = monotonic
        self.rand = rand
        self.blocked = False
        self._last_request: float | None = None

    def _wait(self) -> None:
        """保证两次请求之间间隔 1.5~2 秒（串行）。"""
        now = self.monotonic()
        if self._last_request is None:
            self._last_request = now
            return
        target = self.min_interval + self.rand() * (self.max_interval - self.min_interval)
        elapsed = now - self._last_request
        if elapsed < target:
            self.sleep(target - elapsed)
        self._last_request = self.monotonic()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """带节流与退避地发送一次请求。"""
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("User-Agent", USER_AGENT)
        timeout = kwargs.pop("timeout", (self.connect_timeout, self.read_timeout))

        self._wait()
        attempt = 0
        while True:
            try:
                resp = self.session.request(method, url, headers=headers, timeout=timeout, **kwargs)
            except requests.exceptions.Timeout as exc:
                raise SteamTimeoutError(f"请求超时：{url}") from exc
            except requests.exceptions.RequestException as exc:
                raise SteamRequestError(f"请求失败：{url}：{exc}") from exc

            if resp.status_code not in (429, 403):
                return resp

            if attempt >= self.max_retries:
                if resp.status_code == 403:
                    self.blocked = True
                    raise SteamBlockedError(
                        f"收到 HTTP 403 且重试耗尽，判定被封：{url}",
                        status_code=403,
                        url=url,
                    )
                raise SteamRateLimitError(
                    f"收到 HTTP 429 且重试耗尽：{url}",
                    status_code=429,
                    url=url,
                )

            delay = min(self.backoff_base * (2 ** attempt), self.backoff_cap)
            attempt += 1
            logger.warning(
                "HTTP %s 触发退避，%.1fs 后重试（第 %d 次）：%s",
                resp.status_code,
                delay,
                attempt,
                url,
            )
            self.sleep(delay)


class SteamClient:
    """Steam Storefront 免费接口客户端（全部 keyless）。"""

    def __init__(
        self,
        session: requests.Session | None = None,
        base_url: str = BASE_URL,
        limiter: RateLimiter | None = None,
    ):
        self.session = session if session is not None else requests.Session()
        self.base_url = base_url.rstrip("/")
        self.limiter = limiter if limiter is not None else RateLimiter(self.session)

    # ---------- §5.1 appdetails ----------

    def get_appdetails(self, appid: int) -> AppDetails | None:
        """获取单个 appid 的详情；下架/失败（success=false 或 data=null）返回 None。"""
        url = (
            f"{self.base_url}/api/appdetails"
            f"?appids={appid}&cc=cn&l=schinese"
        )
        resp = self.limiter.request("GET", url)
        if resp.status_code != 200:
            logger.warning("appdetails 非 200（%s）：appid=%s", resp.status_code, appid)
            return None
        try:
            payload = resp.json()
        except ValueError as exc:
            logger.warning("appdetails 返回非 JSON：appid=%s：%s", appid, exc)
            return None
        entry = payload.get(str(appid)) if isinstance(payload, dict) else None
        if not isinstance(entry, dict) or not entry.get("success") or entry.get("data") is None:
            logger.warning("appdetails success=false 或 data=null（下架/移除）：appid=%s", appid)
            return None
        return AppDetails.from_raw(entry["data"])

    # ---------- §5.2 search/results ----------

    def search_results(self, filter_name: str, page: int = 1, count: int = 50) -> list[dict]:
        """获取新发行/即将推出列表中的一项。失败时返回空列表。"""
        url = (
            f"{self.base_url}/search/results/?json=1"
            f"&filter={filter_name}&sort_by=Released_DESC&cc=cn&l=schinese"
            f"&count={count}&page={page}"
        )
        resp = self.limiter.request("GET", url)
        if resp.status_code != 200:
            logger.warning("search/results 非 200（%s）：filter=%s page=%s", resp.status_code, filter_name, page)
            return []
        try:
            payload = resp.json()
        except ValueError as exc:
            logger.warning("search/results 返回非 JSON：filter=%s：%s", filter_name, exc)
            return []
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    # ---------- §5.3 storesearch ----------

    def store_search(self, term: str) -> list[dict]:
        """按名称搜索商店，用于游戏名 → appid 解析。失败时返回空列表。"""
        url = (
            f"{self.base_url}/api/storesearch/?term={quote(term)}"
            f"&cc=cn&l=schinese"
        )
        resp = self.limiter.request("GET", url)
        if resp.status_code != 200:
            logger.warning("storesearch 非 200（%s）：term=%s", resp.status_code, term)
            return []
        try:
            payload = resp.json()
        except ValueError as exc:
            logger.warning("storesearch 返回非 JSON：term=%s：%s", term, exc)
            return []
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]
