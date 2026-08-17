"""config 加载与校验测试。"""

from __future__ import annotations

import pytest

from steam_monitor.config import ConfigError, load_config


def write_config(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadConfig:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(tmp_path / "nope.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        path = write_config(tmp_path, "publishers: [unclosed")
        with pytest.raises(ConfigError):
            load_config(path)

    def test_full_config(self, tmp_path):
        path = write_config(
            tmp_path,
            """
publishers:
  - 任天堂
games:
  - 黑神话悟空
  - app/1245620/ELDEN_RING
  - 1245620
checkpoints: [+14, +7, -3]
interval_hours: 6
notify:
  default:
    title: "🎮 {game_name}"
    content: "{stage}\\n发售日：{release_date}\\n{store_url}"
  channels:
    - provider: ntfy
      topic: my-game-topic
      priority: high
      title: "{game_name}｜{stage}"
    - provider: serverchanturbo
      sendkey: SCTxxx
report_dir: reports
""",
        )
        config = load_config(path)
        assert [p.name for p in config.publishers] == ["任天堂"]
        assert all(p.clan_account_id is None for p in config.publishers)
        assert config.games == ["黑神话悟空", "app/1245620/ELDEN_RING", "1245620"]
        assert config.checkpoints == [14, 7, -3]
        assert config.interval_hours == 6.0
        assert config.default_template == {"title": "🎮 {game_name}", "content": "{stage}\n发售日：{release_date}\n{store_url}"}
        assert len(config.channels) == 2
        assert config.channels[0].provider == "ntfy"
        assert config.channels[0].params == {"topic": "my-game-topic", "priority": "high"}
        assert config.channels[0].title == "{game_name}｜{stage}"
        assert config.channels[0].content is None
        assert config.channels[1].provider == "serverchanturbo"
        assert config.channels[1].params == {"sendkey": "SCTxxx"}
        assert str(config.report_dir) == "reports"

    def test_defaults_when_empty(self, tmp_path):
        path = write_config(tmp_path, "publishers: []\ngames: []\n")
        config = load_config(path)
        assert config.publishers == []
        assert config.games == []
        assert config.checkpoints == [14, 7, -3]
        assert config.interval_hours == 6.0
        assert config.channels == []
        assert config.default_template is None
        assert str(config.report_dir) == "reports"

    def test_notify_as_plain_list(self, tmp_path):
        path = write_config(
            tmp_path,
            """
notify:
  - provider: pushplus
    token: abc
""",
        )
        config = load_config(path)
        assert config.default_template is None
        assert config.channels[0].provider == "pushplus"
        assert config.channels[0].params == {"token": "abc"}

    def test_invalid_checkpoints_raises(self, tmp_path):
        path = write_config(tmp_path, "checkpoints: [abc]\n")
        with pytest.raises(ConfigError):
            load_config(path)

    def test_invalid_interval_raises(self, tmp_path):
        path = write_config(tmp_path, "interval_hours: -1\n")
        with pytest.raises(ConfigError):
            load_config(path)

    def test_channel_without_provider_raises(self, tmp_path):
        path = write_config(tmp_path, "notify:\n  channels:\n    - topic: x\n")
        with pytest.raises(ConfigError):
            load_config(path)

    def test_template_property_fallback(self, tmp_path):
        path = write_config(tmp_path, "")
        config = load_config(path)
        assert config.template_title == "{game_name}"
        assert config.template_content == "{stage}\n{store_url}"


class TestPublishers:
    def test_string_form_auto_resolve(self, tmp_path):
        path = write_config(tmp_path, "publishers:\n  - 任天堂\n")
        config = load_config(path)
        assert len(config.publishers) == 1
        assert config.publishers[0].name == "任天堂"
        assert config.publishers[0].clan_account_id is None

    def test_mapping_form_with_clan_id(self, tmp_path):
        path = write_config(
            tmp_path,
            "publishers:\n  - name: 072projectx\n    clan_account_id: 45479601\n",
        )
        config = load_config(path)
        assert config.publishers[0].name == "072projectx"
        assert config.publishers[0].clan_account_id == 45479601

    def test_mixed_forms(self, tmp_path):
        path = write_config(
            tmp_path,
            "publishers:\n  - 任天堂\n  - name: X\n    clan_account_id: 123\n",
        )
        config = load_config(path)
        assert config.publishers[0].name == "任天堂"
        assert config.publishers[1].name == "X"
        assert config.publishers[1].clan_account_id == 123

    def test_mapping_without_name_raises(self, tmp_path):
        path = write_config(tmp_path, "publishers:\n  - clan_account_id: 123\n")
        with pytest.raises(ConfigError):
            load_config(path)

    def test_gid_as_yaml_number(self, tmp_path):
        # YAML 纯数字字面量解析为 int，应自动转字符串
        path = write_config(
            tmp_path,
            "publishers:\n  - name: X\n    clan_account_id: 123\n"
            "    clan_announcement_gid: 509607220045941405\n",
        )
        config = load_config(path)
        assert config.publishers[0].clan_account_id == 123
        assert config.publishers[0].clan_announcement_gid == "509607220045941405"

    def test_invalid_clan_id_raises(self, tmp_path):
        path = write_config(
            tmp_path, "publishers:\n  - name: X\n    clan_account_id: -5\n"
        )
        with pytest.raises(ConfigError):
            load_config(path)


class TestProxy:
    def test_proxy_string_expands_to_both(self, tmp_path):
        path = write_config(tmp_path, "proxy: http://127.0.0.1:7890\n")
        config = load_config(path)
        assert config.proxy == {
            "http": "http://127.0.0.1:7890",
            "https": "http://127.0.0.1:7890",
        }

    def test_proxy_mapping(self, tmp_path):
        path = write_config(
            tmp_path,
            "proxy:\n  http: http://127.0.0.1:7890\n  https: socks5h://127.0.0.1:7891\n",
        )
        config = load_config(path)
        assert config.proxy == {
            "http": "http://127.0.0.1:7890",
            "https": "socks5h://127.0.0.1:7891",
        }

    def test_proxy_missing_is_none(self, tmp_path):
        path = write_config(tmp_path, "")
        config = load_config(path)
        assert config.proxy is None

    def test_proxy_empty_string_is_none(self, tmp_path):
        path = write_config(tmp_path, "proxy: ''\n")
        config = load_config(path)
        assert config.proxy is None

    def test_proxy_invalid_raises(self, tmp_path):
        path = write_config(tmp_path, "proxy: [1, 2]\n")
        with pytest.raises(ConfigError):
            load_config(path)
