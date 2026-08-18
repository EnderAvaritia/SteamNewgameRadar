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


class TestPublisherCreatorParams:
    """publisher_creator_params：从主页 HTML 解析 clanAccountID，并推导可用 GID（gidEvent + 1）。"""

    class _FakeResponse:
        def __init__(self, status_code: int, text: str):
            self.status_code = status_code
            self.text = text

    class _FakeLimiter:
        def __init__(self, response):
            self.response = response
            self.requested_url: str | None = None

        def request(self, method: str, url: str):
            self.requested_url = url
            return self.response

    def _client_with(self, response) -> SteamClient:
        return SteamClient(limiter=self._FakeLimiter(response))

    def test_parses_clan_id_and_derives_gid(self):
        html = (
            '<div data-featuretarget="creator-home-event" data-props="{'
            "&quot;clanAccountID&quot;:32398519,"
            '&quot;gidEvent&quot;:&quot;497222321068573320&quot;}"></div>'
        )
        client = self._client_with(self._FakeResponse(200, html))
        clan_id, gid = client.publisher_creator_params("Kagura")
        assert clan_id == 32398519
        assert gid == "497222321068573321"  # gidEvent + 1 = 可用 clanAnnouncementGID

    def test_non_200_returns_none(self):
        client = self._client_with(self._FakeResponse(403, "forbidden"))
        assert client.publisher_creator_params("Kagura") == (None, None)

    def test_missing_gid_event_returns_none_gid(self):
        html = '<div data-props="{&quot;clanAccountID&quot;:32398519}"></div>'
        client = self._client_with(self._FakeResponse(200, html))
        clan_id, gid = client.publisher_creator_params("Kagura")
        assert clan_id == 32398519
        assert gid is None

    def test_missing_clan_id_returns_none_clan(self):
        html = '<div data-props="{&quot;gidEvent&quot;:&quot;123&quot;}"></div>'
        client = self._client_with(self._FakeResponse(200, html))
        clan_id, gid = client.publisher_creator_params("Kagura")
        assert clan_id is None
        assert gid == "124"

    def test_requests_publisher_page_url(self):
        html = '<div data-props="{&quot;clanAccountID&quot;:1,&quot;gidEvent&quot;:&quot;1&quot;}"></div>'
        limiter = self._FakeLimiter(self._FakeResponse(200, html))
        client = SteamClient(limiter=limiter)
        client.publisher_creator_params("Kagura")
        assert "https://store.steampowered.com/publisher/Kagura" in limiter.requested_url
