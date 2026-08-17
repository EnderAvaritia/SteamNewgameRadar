"""pytest 共享夹具：假 Steam 客户端、假 appdetails、临时状态库、假通知器。"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from steam_monitor.config import Channel, Config, Publisher  # noqa: E402
from steam_monitor.notifier import Notifier  # noqa: E402
from steam_monitor.state import State  # noqa: E402
from steam_monitor.steam_api import AppDetails  # noqa: E402

# ---------------------------------------------------------------------------
# 假 Steam 客户端
# ---------------------------------------------------------------------------


class FakeSteamClient:
    """可编程的 Steam 客户端替身：无网络、无时钟依赖。"""

    def __init__(self):
        self.appdetails: dict[int, AppDetails | None] = {}
        self.search_items: dict[tuple[str, int], list[dict]] = {}
        self.store_search_items: dict[str, list[dict]] = {}
        self.creator_appids: dict[int, list[int]] = {}
        self.publisher_clan_ids: dict[str, int] = {}
        self.publisher_gids: dict[str, str] = {}
        self.calls: list[str] = []

    # -- 接口实现 --
    def get_appdetails(self, appid: int):
        self.calls.append(f"appdetails:{appid}")
        return self.appdetails.get(appid)

    def search_results(self, filter_name: str, page: int = 1, count: int = 50):
        self.calls.append(f"search:{filter_name}:{page}")
        return self.search_items.get((filter_name, page), [])

    def store_search(self, term: str):
        self.calls.append(f"storesearch:{term}")
        return self.store_search_items.get(term, [])

    def creator_apps(
        self,
        clan_account_id: int,
        clan_announcement_gid: str,
        flavor: str = "all",
        count: int = 50,
        max_pages: int = 1,
    ):
        self.calls.append(f"creator:{clan_account_id}:{clan_announcement_gid}")
        return list(self.creator_appids.get(clan_account_id, []))

    def publisher_creator_params(self, name: str):
        self.calls.append(f"creatorpage:{name}")
        return self.publisher_clan_ids.get(name), self.publisher_gids.get(name)

    # -- 便捷构造 --
    def add_appdetails(self, appid: int, **kwargs):
        defaults = dict(
            name=f"游戏{appid}",
            appid=appid,
            type="game",
            coming_soon=True,
            release_date_raw="Coming soon",
            publishers=[],
            is_free=False,
            price_final=None,
        )
        defaults.update(kwargs)
        self.appdetails[appid] = AppDetails(**defaults)

    def add_search_item(self, appid: int, filter_name: str = "popularnew", page: int = 1, name: str | None = None):
        item = {
            "name": name or f"游戏{appid}",
            "logo": f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/logo.png",
        }
        self.search_items.setdefault((filter_name, page), []).append(item)

    def add_store_search(self, term: str, appid: int, item_type: str = "game", name: str | None = None):
        item = {"type": item_type, "id": appid, "name": name or term}
        self.store_search_items.setdefault(term, []).append(item)

    def add_creator_apps(self, clan_account_id: int, appids: list[int], flavor: str = "all"):
        self.creator_appids.setdefault(clan_account_id, []).extend(appids)

    def set_publisher_creator(self, name: str, clan_account_id: int, gid: str | None = None):
        self.publisher_clan_ids[name] = clan_account_id
        if gid is not None:
            self.publisher_gids[name] = gid


@pytest.fixture
def fake_client() -> FakeSteamClient:
    return FakeSteamClient()


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def make_config(
    publishers=None,
    games=None,
    checkpoints=None,
    interval_hours=None,
    channels=None,
    default_template=None,
    report_dir="reports",
    notify_on_first_seen=True,
) -> Config:
    if publishers is None:
        publisher_list: list[Publisher] = []
    else:
        publisher_list = [
            p if isinstance(p, Publisher) else Publisher(name=str(p))
            for p in publishers
        ]
    return Config(
        publishers=publisher_list,
        games=list(games or []),
        checkpoints=list(checkpoints if checkpoints is not None else [14, 7, -3]),
        interval_hours=float(interval_hours if interval_hours is not None else 6.0),
        channels=list(channels or []),
        default_template=default_template,
        report_dir=Path(report_dir),
        notify_on_first_seen=notify_on_first_seen,
    )


@pytest.fixture
def base_config() -> Config:
    return make_config()


# ---------------------------------------------------------------------------
# 状态库（临时 SQLite）
# ---------------------------------------------------------------------------


@pytest.fixture
def state(tmp_path) -> State:
    db = State(tmp_path / "state.db")
    yield db
    db.close()


# ---------------------------------------------------------------------------
# 假通知器
# ---------------------------------------------------------------------------


class FakeNotifier:
    """记录 send() 调用的通知器替身。"""

    def __init__(self):
        self.sent_contexts = []
        self.report_path = None

    def send(self, context) -> None:
        self.sent_contexts.append(context)


@pytest.fixture
def fake_notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def recording_notify():
    """返回 (fake_notify_func, calls) —— 记录 onepush 调用参数（provider 为位置参数）。"""

    calls: list[dict] = []

    def fake_notify(provider_name=None, **kwargs):
        calls.append({"provider": provider_name, **kwargs})

    return fake_notify, calls


# ---------------------------------------------------------------------------
# 固定时间
# ---------------------------------------------------------------------------

FIXED_TODAY = date(2026, 8, 18)
FIXED_NOW = datetime(2026, 8, 18, 10, 30, 0)


@pytest.fixture
def fixed_now() -> datetime:
    return FIXED_NOW
