"""resolver 单元测试（DESIGN.md §12.4）：名称/URL/appid 三种输入、发行商匹配。"""

from __future__ import annotations

import pytest

from steam_monitor.resolver import (
    Resolver,
    appid_from_item,
    parse_config_entry,
)


class TestParseConfigEntry:
    def test_digits_are_appid(self):
        kind, value = parse_config_entry("1245620")
        assert (kind, value) == ("appid", "1245620")

    def test_short_url_form(self):
        kind, value = parse_config_entry("app/1245620/ELDEN_RING")
        assert (kind, value) == ("url", "1245620")

    def test_full_store_url(self):
        kind, value = parse_config_entry("https://store.steampowered.com/app/1245620/Elden_Ring/")
        assert (kind, value) == ("url", "1245620")

    def test_name(self):
        kind, value = parse_config_entry("黑神话悟空")
        assert (kind, value) == ("name", "黑神话悟空")


class TestResolveGameEntry:
    def test_resolve_by_appid(self, fake_client):
        resolver = Resolver(fake_client)
        assert resolver.resolve_game_entry("1245620") == 1245620

    def test_resolve_by_url(self, fake_client):
        resolver = Resolver(fake_client)
        assert resolver.resolve_game_entry("app/1245620/ELDEN_RING") == 1245620

    def test_resolve_by_name_via_store_search(self, fake_client):
        fake_client.add_store_search("黑神话悟空", 1245620, name="黑神话：悟空")
        resolver = Resolver(fake_client)
        assert resolver.resolve_game_entry("黑神话悟空") == 1245620

    def test_store_search_skips_non_game_first(self, fake_client):
        fake_client.add_store_search("巫师", 10001, item_type="dlc", name="巫师 DLC")
        fake_client.add_store_search("巫师", 10002, item_type="game", name="巫师")
        resolver = Resolver(fake_client)
        assert resolver.resolve_game_entry("巫师") == 10002

    def test_unresolvable_returns_none(self, fake_client):
        resolver = Resolver(fake_client)
        assert resolver.resolve_game_entry("不存在的游戏") is None

    def test_resolve_games_dict(self, fake_client):
        fake_client.add_store_search("黑神话悟空", 1245620)
        resolver = Resolver(fake_client)
        result = resolver.resolve_games(["1245620", "app/999/Some_Game", "黑神话悟空", "坏条目"])
        assert result == {"1245620": 1245620, "app/999/Some_Game": 999, "黑神话悟空": 1245620}


class TestAppidFromItem:
    def test_extract_from_logo_url(self):
        item = {"name": "X", "logo": "https://cdn.akamai.steamstatic.com/steam/apps/1245620/logo.png"}
        assert appid_from_item(item) == 1245620

    def test_extract_from_tiny_image(self):
        item = {"name": "X", "tiny_image": "https://cdn.akamai.steamstatic.com/steam/apps/42/header.jpg"}
        assert appid_from_item(item) == 42

    def test_no_url_returns_none(self):
        assert appid_from_item({"name": "X"}) is None


class TestDiscover:
    def test_polls_all_filters_and_pages_deduplicated(self, fake_client):
        fake_client.add_search_item(1, "popularnew", 1)
        fake_client.add_search_item(1, "popularnew", 2)   # 重复 → 去重
        fake_client.add_search_item(2, "comingsoon", 1)
        fake_client.add_search_item(3, "popularcomingsoon", 2)
        resolver = Resolver(fake_client)
        appids = resolver.discover_publisher_candidates()
        assert appids == [1, 2, 3]
        # 3 filters × 2 pages 都被轮询
        filters = {"popularnew", "comingsoon", "popularcomingsoon"}
        assert all(f in [c.split(":")[1] for c in fake_client.calls] for f in filters)


class TestMatchPublisher:
    """精确匹配（忽略大小写与首尾空白）。"""

    def test_exact_match(self):
        assert Resolver.match_publisher(["任天堂"], ["任天堂"]) == "任天堂"

    def test_case_insensitive(self):
        assert Resolver.match_publisher(["Nintendo"], ["nintendo"]) == "Nintendo"
        assert Resolver.match_publisher(["nintendo"], ["NINTENDO"]) == "nintendo"

    def test_whitespace_insensitive(self):
        assert Resolver.match_publisher(["  任天堂  "], ["任天堂"]) == "任天堂"
        assert Resolver.match_publisher(["任天堂"], ["  任天堂  "]) == "任天堂"

    def test_no_match_returns_none(self):
        assert Resolver.match_publisher(["Sony"], ["任天堂"]) is None
        assert Resolver.match_publisher(["任天堂株式会社"], ["任天堂"]) is None

    def test_multiple_configured(self):
        assert Resolver.match_publisher(["Nintendo"], ["Sony", "Nintendo"]) == "Nintendo"
        assert Resolver.match_publisher(["Nintendo"], ["Sony", "Ubisoft"]) is None

    def test_empty_publishers(self):
        assert Resolver.match_publisher([], ["任天堂"]) is None
