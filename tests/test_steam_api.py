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


class TestSteamClientCookie:
    def test_cookie_set_on_session_headers(self):
        client = SteamClient(cookie="sessionid=abc; steamLoginSecure=def")
        assert client.session.headers["Cookie"] == "sessionid=abc; steamLoginSecure=def"

    def test_no_cookie_keeps_headers_clean(self):
        client = SteamClient()
        assert "Cookie" not in client.session.headers

    def test_cc_normalized(self):
        assert SteamClient(cc="hk").cc == "HK"
        assert SteamClient(cc="").cc == "CN"
        assert SteamClient().cc == "CN"
