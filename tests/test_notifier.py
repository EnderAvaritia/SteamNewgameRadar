"""notifier 单元测试（DESIGN.md §12.5）：模板渲染、渠道失败隔离、报告文件生成。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from steam_monitor.config import Channel
from steam_monitor.events import CHECKPOINT, DATE_ANNOUNCED, GameEvent
from steam_monitor.notifier import Notifier, price_text, safe_format
from tests.conftest import make_config

FIXED_NOW = datetime(2026, 8, 18, 10, 30, 0)


def make_event(event_type=CHECKPOINT, appid=100, **kw) -> GameEvent:
    defaults = dict(
        event_type=event_type,
        appid=appid,
        game_name="测试游戏",
        stage="距发售还有 7 天",
        publisher="任天堂",
        release_date="2026-08-21",
        release_date_raw="21 Aug, 2026",
        days_until=7,
        store_url="https://store.steampowered.com/app/100/",
        price="¥50",
    )
    defaults.update(kw)
    return GameEvent(**defaults)


def make_context(events, warnings=None, tracking=None, duration=1.5):
    from steam_monitor.engine import RunContext

    ctx = RunContext(started_at=FIXED_NOW, duration=duration)
    ctx.events = list(events)
    ctx.warnings = list(warnings or [])
    ctx.tracking = list(tracking or [])
    return ctx


class TestSafeFormat:
    def test_missing_variable_renders_empty_no_keyerror(self):
        assert safe_format("{game_name}｜{unknown_var}", {"game_name": "X"}) == "X｜"

    def test_all_variables(self):
        text = safe_format(
            "{game_name} {publisher} {stage} {release_date} {days_until}",
            {
                "game_name": "G",
                "publisher": "P",
                "stage": "S",
                "release_date": "2026-08-21",
                "days_until": "7",
            },
        )
        assert text == "G P S 2026-08-21 7"

    def test_missing_variable_with_format_spec_does_not_raise(self):
        assert safe_format("价格：{price:.2f}", {}) == "价格："

    def test_escaped_braces(self):
        assert safe_format("{{literal}} {game_name}", {"game_name": "X"}) == "{literal} X"

    def test_attribute_access_ignored(self):
        # 变量名取第一段：{game.name} 使用基础变量 game 的值，不抛 AttributeError
        assert safe_format("{game.name}", {"game": "X"}) == "X"
        assert safe_format("{game.name}", {}) == ""

    def test_no_placeholder(self):
        assert safe_format("纯文本", {}) == "纯文本"


class TestPriceText:
    def test_free(self):
        assert price_text(True, None) == "免费"

    def test_integer_price(self):
        assert price_text(False, 5000) == "¥50"

    def test_decimal_price_max_two_digits(self):
        assert price_text(False, 4999) == "¥49.99"
        assert price_text(False, 4980) == "¥49.8"

    def test_no_price(self):
        assert price_text(False, None) == ""


class TestTemplateFallback:
    def test_channel_template_overrides_global(self, tmp_path, recording_notify):
        fake_notify, calls = recording_notify
        config = make_config(
            channels=[Channel(provider="ntfy", params={"topic": "t"}, title="{game_name}｜渠道", content="自定")],
            default_template={"title": "全局{game_name}", "content": "全局内容"},
            report_dir=str(tmp_path / "reports"),
        )
        notifier = Notifier(config, notify_func=fake_notify, now=lambda: FIXED_NOW)
        notifier.send(make_context([make_event()]))
        assert calls[0]["title"] == "测试游戏｜渠道"
        assert calls[0]["content"] == "自定"

    def test_falls_back_to_global_default(self, tmp_path, recording_notify):
        fake_notify, calls = recording_notify
        config = make_config(
            channels=[Channel(provider="ntfy", params={"topic": "t"})],
            default_template={"title": "全局{game_name}", "content": "内容{stage}"},
            report_dir=str(tmp_path / "reports"),
        )
        notifier = Notifier(config, notify_func=fake_notify, now=lambda: FIXED_NOW)
        notifier.send(make_context([make_event()]))
        assert calls[0]["title"] == "全局测试游戏"
        assert calls[0]["content"] == "内容距发售还有 7 天"

    def test_falls_back_to_builtin(self, tmp_path, recording_notify):
        fake_notify, calls = recording_notify
        config = make_config(
            channels=[Channel(provider="ntfy", params={"topic": "t"})],
            report_dir=str(tmp_path / "reports"),
        )
        notifier = Notifier(config, notify_func=fake_notify, now=lambda: FIXED_NOW)
        notifier.send(make_context([make_event()]))
        assert calls[0]["title"] == "测试游戏"          # 内置 title: {game_name}
        assert calls[0]["content"] == "距发售还有 7 天\nhttps://store.steampowered.com/app/100/"


class TestDelivery:
    def test_no_events_skips_channels_but_writes_report(self, tmp_path, recording_notify):
        fake_notify, calls = recording_notify
        config = make_config(
            channels=[Channel(provider="ntfy", params={"topic": "t"})],
            report_dir=str(tmp_path / "reports"),
        )
        notifier = Notifier(config, notify_func=fake_notify, now=lambda: FIXED_NOW)
        result = notifier.send(make_context([]))
        assert calls == []
        assert result.skipped is True
        assert result.report_path is not None and result.report_path.exists()

    def test_channel_failure_isolation(self, tmp_path):
        def flaky(provider_name=None, **kwargs):
            if provider_name == "bad":
                raise RuntimeError("网络错误")
            return None

        config = make_config(
            channels=[
                Channel(provider="bad", params={}),
                Channel(provider="good", params={}),
            ],
            report_dir=str(tmp_path / "reports"),
        )
        notifier = Notifier(config, notify_func=flaky, now=lambda: FIXED_NOW)
        result = notifier.send(make_context([make_event()]))
        # bad 渠道失败不影响 good 渠道
        assert result.sent == 1
        assert result.failed == 1

    def test_provider_params_passed_through(self, tmp_path, recording_notify):
        fake_notify, calls = recording_notify
        config = make_config(
            channels=[Channel(provider="ntfy", params={"topic": "t", "priority": "high"})],
            report_dir=str(tmp_path / "reports"),
        )
        notifier = Notifier(config, notify_func=fake_notify, now=lambda: FIXED_NOW)
        notifier.send(make_context([make_event()]))
        assert calls[0]["provider"] == "ntfy"
        assert calls[0]["topic"] == "t"
        assert calls[0]["priority"] == "high"

    def test_one_notification_max_per_game_highest_priority(self, tmp_path, recording_notify):
        fake_notify, calls = recording_notify
        config = make_config(
            channels=[Channel(provider="ntfy", params={})],
            report_dir=str(tmp_path / "reports"),
        )
        notifier = Notifier(config, notify_func=fake_notify, now=lambda: FIXED_NOW)
        # 同一游戏：date_announced（4）> checkpoint（2）→ 只发 date_announced
        events = [
            make_event(CHECKPOINT, 100, stage="距发售还有 7 天"),
            make_event(DATE_ANNOUNCED, 100, stage="发售日公布：将于 2026-08-21 发售"),
            make_event(CHECKPOINT, 200, stage="已发售 3 天"),
        ]
        notifier.send(make_context(events))
        sent_appids = sorted(c["title"] for c in calls)
        assert len(calls) == 2  # 两个游戏各一条
        assert any("发售日公布" in c["content"] for c in calls)
        assert any("已发售 3 天" in c["content"] for c in calls)


class TestReport:
    def test_report_file_generated_with_utf8_content(self, tmp_path):
        config = make_config(report_dir=str(tmp_path / "reports"))
        notifier = Notifier(config, now=lambda: FIXED_NOW)
        ctx = make_context(
            [make_event()],
            warnings=["appid 300：success=false 或 data=null（游戏下架或被移除）"],
        )
        result = notifier.send(ctx)
        assert result.report_path is not None
        assert result.report_path.name == "report-2026-08-18-103000.md"
        text = result.report_path.read_text(encoding="utf-8")
        assert "# Steam 新游戏监控报告" in text
        assert "运行时间：2026-08-18 10:30:00" in text
        assert "测试游戏" in text
        assert "游戏下架或被移除" in text
        assert "错误 / 警告摘要" in text

    def test_report_lists_all_events_even_if_only_one_notified(self, tmp_path):
        config = make_config(report_dir=str(tmp_path / "reports"))
        notifier = Notifier(config, now=lambda: FIXED_NOW)
        events = [
            make_event(CHECKPOINT, 100),
            make_event(DATE_ANNOUNCED, 100, stage="发售日公布：将于 2026-08-21 发售"),
        ]
        result = notifier.send(make_context(events))
        text = result.report_path.read_text(encoding="utf-8")
        assert "距发售还有 7 天" in text     # checkpoint 事件也在报告中
        assert "发售日公布" in text

    def test_retention_keeps_last_30(self, tmp_path):
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        for i in range(35):
            (report_dir / f"report-20260101-{i:06d}.md").write_text("x", encoding="utf-8")
        config = make_config(report_dir=str(report_dir))
        notifier = Notifier(config, now=lambda: FIXED_NOW)
        notifier._retain_recent(report_dir, keep=30)
        remaining = sorted(p.name for p in report_dir.glob("report-*.md"))
        assert len(remaining) == 30
        assert remaining[0] == "report-20260101-000005.md"  # 最旧的 5 份被清理

    def test_no_events_report_still_generated(self, tmp_path):
        config = make_config(report_dir=str(tmp_path / "reports"))
        notifier = Notifier(config, now=lambda: FIXED_NOW)
        result = notifier.send(make_context([]))
        assert result.report_path is not None and result.report_path.exists()
        text = result.report_path.read_text(encoding="utf-8")
        assert "（无）" in text
