"""SteamClient 基础行为测试（DESIGN.md §5）：proxy 设置、接口请求参数。"""

from __future__ import annotations

import requests

from steam_monitor.steam_api import SteamClient


class TestSteamClientProxy:
    def test_proxies_applied_to_session(self):
        client = SteamClient(
            proxies={"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
        )
        assert client.session.proxies["http"] == "http://127.0.0.1:7890"
        assert client.session.proxies["https"] == "http://127.0.0.1:7890"

    def test_no_proxies_keeps_session_default(self):
        client = SteamClient()
        assert client.session.proxies == {}

    def test_existing_session_proxies_updated(self):
        session = requests.Session()
        session.proxies["http"] = "http://old:8080"
        client = SteamClient(session=session, proxies={"http": "http://new:7890"})
        assert client.session.proxies["http"] == "http://new:7890"
