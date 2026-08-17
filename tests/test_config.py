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
        assert config.publishers == ["任天堂"]
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
