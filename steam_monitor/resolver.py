"""游戏名/URL/appid → appid；发行商新游戏列表抓取（DESIGN.md §5.2/§5.3）。

发行商监控线使用「creator 精准查询」：对每个被监控的发行商，按其 clan 账号 ID
拉取其全部游戏列表（flavor=all = 即将发行 + 最新已发售），无需轮询商店全局列表。
"""

from __future__ import annotations

import re
from typing import Any

from .config import Publisher

__all__ = [
    "Resolver",
    "appid_from_item",
    "parse_config_entry",
]

#: 从商店 URL / 配置条目中提取 appid
_APP_URL_PATTERN = re.compile(r"app/(\d+)")

#: 从 search/results items 的图片 URL 中提取 appid（§5.2：steam/apps/(\d+)）
_APPS_URL_PATTERN = re.compile(r"steam/apps/(\d+)")

#: item 中可能包含 appid 的 URL 字段
_ITEM_URL_KEYS = ("logo", "tiny_image", "header_image", "url")


def parse_config_entry(entry: str) -> tuple[str, str]:
    """把配置中的游戏条目归类。

    返回 ``("appid", "1245620")`` / ``("url", ...)`` / ``("name", ...)``。
    """
    s = (entry or "").strip()
    if not s:
        return "name", ""
    if s.isdigit():
        return "appid", s
    if _APP_URL_PATTERN.search(s):
        return "url", _APP_URL_PATTERN.search(s).group(1)
    return "name", s


def appid_from_item(item: dict[str, Any]) -> int | None:
    """从 search/results item 的 URL 字段提取 appid。"""
    for key in _ITEM_URL_KEYS:
        value = item.get(key)
        if isinstance(value, str):
            match = _APPS_URL_PATTERN.search(value)
            if match:
                return int(match.group(1))
    return None


class Resolver:
    """配置条目解析与发行商候选抓取。"""

    def __init__(self, client: Any):
        self.client = client

    # ---------- §5.3 游戏监控线 ----------

    def resolve_game_entry(self, entry: str) -> int | None:
        """名称/URL/appid 三种输入 → appid；解析失败返回 None。"""
        kind, value = parse_config_entry(entry)
        if kind == "appid":
            return int(value)
        if kind == "url":
            return int(value)
        # 名称 → storesearch，取第一个 game 类型结果（§5.3）
        items = self.client.store_search(value)
        for item in items:
            if item.get("type") == "game" and item.get("id"):
                return int(item["id"])
        return None

    def resolve_games(self, entries: list[str]) -> dict[str, int]:
        """解析全部配置游戏条目；失败的条目不入结果（由调用方记日志）。"""
        resolved: dict[str, int] = {}
        for entry in entries:
            appid = self.resolve_game_entry(entry)
            if appid is not None:
                resolved[entry] = appid
        return resolved

    # ---------- §5.2 发行商监控线 ----------

    def discover_creator_appids(
        self, publisher: Publisher
    ) -> tuple[list[int], int | None, str | None]:
        """按发行商拉取其游戏 appid 列表（§5.2 精准查询）。

        - clan_account_id / clan_announcement_gid 显式配置则直接用；
          clan_account_id 缺失时从发行商主页自动解析（gid 主页无法可靠解析，
          建议显式配置）。
        - 返回 ``(appids, clan_id, gid)``；clan_id 或 gid 为 None 表示缺参数
          （调用方记警告）。
        """
        clan_id = publisher.clan_account_id
        gid = publisher.clan_announcement_gid
        if clan_id is None or gid is None:
            page_clan, page_gid = self.client.publisher_creator_params(publisher.name)
            if clan_id is None:
                clan_id = page_clan
            if gid is None:
                gid = page_gid
        if clan_id is None or gid is None:
            return [], clan_id, gid
        # flavor=all_upcoming：只取「即将发售」栏目（§5.2），不含已发售
        appids = self.client.creator_apps(clan_id, gid, flavor="all_upcoming")
        return appids, clan_id, gid

    # ---------- 发行商匹配 ----------

    @staticmethod
    def match_publisher(publishers: list[str], configured: list[str]) -> str | None:
        """精确匹配（忽略大小写与首尾空白），返回命中的发行商名（去空白）。"""
        normalized_publishers = [p.strip() for p in publishers]
        for name in configured:
            target = name.strip()
            for pub in normalized_publishers:
                if pub.lower() == target.lower():
                    return pub
        return None
