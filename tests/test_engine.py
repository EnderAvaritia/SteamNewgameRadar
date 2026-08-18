"""engine 全链路集成测试（DESIGN.md §12.6）：mock Steam 响应、注入时钟。"""

from __future__ import annotations

from datetime import date, datetime

from steam_monitor.engine import run_check
from steam_monitor.events import (
    CHECKPOINT,
    DATE_ANNOUNCED,
    DATE_CHANGED,
    NEW_ANNOUNCEMENT,
)
from tests.conftest import FIXED_NOW, FIXED_TODAY, make_config

D = date


def run(client, config, state, notifier, today=FIXED_TODAY, progress=None):
    return run_check(
        client=client,
        today=today,
        config=config,
        state=state,
        notifier=notifier,
        now=lambda: FIXED_NOW,
        progress=progress,
    )


class TestProgressCallback:
    """§12.6-8：progress 回调覆盖各处理阶段。"""

    def test_progress_reports_stages(self, fake_client, state, fake_notifier):
        fake_client.set_publisher_creator("任天堂", 45479601, "GID1")
        fake_client.add_creator_apps(45479601, [100])
        fake_client.add_appdetails(
            100, name="新作", publishers=["任天堂"], release_date_raw="Coming soon"
        )
        fake_client.add_appdetails(1245620, release_date_raw="21 Aug, 2026", coming_soon=True)
        config = make_config(publishers=["任天堂"], games=["1245620"])

        messages: list[str] = []
        run(fake_client, config, state, fake_notifier, progress=messages.append)

        joined = "\n".join(messages)
        assert "开始检查" in joined
        assert "发行商「任天堂」（clan 45479601）：候选 1 个" in joined
        assert "处理游戏 1/1" in joined
        assert "检查完成" in joined

    def test_progress_without_callback_is_safe(self, fake_client, state, fake_notifier):
        fake_client.add_appdetails(1245620, release_date_raw="21 Aug, 2026", coming_soon=True)
        config = make_config(games=["1245620"])
        ctx = run(fake_client, config, state, fake_notifier, progress=None)
        assert ctx is not None


class TestPublisherLine:
    """§12.6-1：发行商新游戏出现 → 新游戏公布事件（creator 精准查询）。"""

    def _setup_publisher(self, fake_client, clan_id=45479601, gid="GID1", name="任天堂"):
        fake_client.set_publisher_creator(name, clan_id, gid)
        return clan_id

    def test_new_publisher_game_fires_new_announcement(self, fake_client, state, fake_notifier):
        self._setup_publisher(fake_client)
        fake_client.add_creator_apps(45479601, [1245620])
        fake_client.add_appdetails(
            1245620,
            name="测试新作",
            publishers=["任天堂"],
            release_date_raw="Coming soon",
            coming_soon=True,
        )
        config = make_config(publishers=["任天堂"])
        ctx = run(fake_client, config, state, fake_notifier)

        types = [e.event_type for e in ctx.events]
        assert NEW_ANNOUNCEMENT in types
        event = next(e for e in ctx.events if e.event_type == NEW_ANNOUNCEMENT)
        assert event.appid == 1245620
        assert event.game_name == "测试新作"
        assert event.publisher == "任天堂"
        assert event.stage == "新游戏公布（发售日未定）"

        record = state.get_game(1245620)
        assert record is not None
        assert record.source == "publisher"
        assert record.publisher_match == "任天堂"
        assert record.release_status == "unknown"

        # 通知器被调用
        assert len(fake_notifier.sent_contexts) == 1

    def test_new_publisher_game_with_date_enters_checkpoint_flow(self, fake_client, state, fake_notifier):
        today = D(2026, 8, 10)  # 距 8/21 还有 11 天 → 只有 +14 检查点跨越
        self._setup_publisher(fake_client)
        fake_client.add_creator_apps(45479601, [100])
        fake_client.add_appdetails(100, name="新作", publishers=["任天堂"],
                                   release_date_raw="21 Aug, 2026", coming_soon=True)
        config = make_config(publishers=["任天堂"])
        ctx = run(fake_client, config, state, fake_notifier, today=today)

        types = {e.event_type for e in ctx.events}
        assert NEW_ANNOUNCEMENT in types
        assert CHECKPOINT in types
        # 通知只发优先级最高的 new_announcement（§8.1 示例）
        checkpoint = next(e for e in ctx.events if e.event_type == CHECKPOINT)
        assert checkpoint.stage == "距发售还有 11 天"
        record = state.get_game(100)
        assert record.last_triggered == 0  # +14 检查点已触发

    def test_explicit_clan_account_id_no_page_lookup(self, fake_client, state, fake_notifier):
        # 配置显式给 clan_account_id + clan_announcement_gid → 不请求发行商主页
        fake_client.add_creator_apps(999, [100])
        fake_client.add_appdetails(100, name="新作", release_date_raw="21 Aug, 2026", coming_soon=True)
        config = make_config(publishers=["任天堂"])
        config.publishers[0].clan_account_id = 999
        config.publishers[0].clan_announcement_gid = "GID1"
        run(fake_client, config, state, fake_notifier)
        assert "creatorpage:任天堂" not in fake_client.calls  # 未解析主页
        assert state.get_game(100) is not None

    def test_missing_gid_warns(self, fake_client, state, fake_notifier):
        # 主页只能解析 clan_id，gid 缺失 → 警告提示参数解析失败
        fake_client.set_publisher_creator("任天堂", 45479601)  # 无 gid
        config = make_config(publishers=["任天堂"])
        ctx = run(fake_client, config, state, fake_notifier)
        assert ctx.events == []
        assert any("creator 查询参数解析失败" in w for w in ctx.warnings)

    def test_non_game_type_skipped(self, fake_client, state, fake_notifier):
        self._setup_publisher(fake_client)
        fake_client.add_creator_apps(45479601, [100])
        fake_client.add_appdetails(100, name="某 DLC", type="dlc", publishers=["任天堂"])
        config = make_config(publishers=["任天堂"])
        ctx = run(fake_client, config, state, fake_notifier)
        assert ctx.events == []
        assert state.get_game(100) is None

    def test_publisher_game_not_reannounced_on_second_run(self, fake_client, state, fake_notifier):
        self._setup_publisher(fake_client)
        fake_client.add_creator_apps(45479601, [100])
        fake_client.add_appdetails(100, name="新作", publishers=["任天堂"], release_date_raw="Coming soon")
        config = make_config(publishers=["任天堂"])
        run(fake_client, config, state, fake_notifier)
        ctx2 = run(fake_client, config, state, fake_notifier)
        assert not any(e.event_type == NEW_ANNOUNCEMENT for e in ctx2.events)

    def test_first_seen_silent_when_notify_on_first_seen_false(self, fake_client, state, fake_notifier):
        # notify_on_first_seen=false：首次看到静默入库（建基线），不通知
        self._setup_publisher(fake_client)
        fake_client.add_creator_apps(45479601, [100])
        fake_client.add_appdetails(100, name="新作", release_date_raw="Coming soon")
        config = make_config(publishers=["任天堂"], notify_on_first_seen=False)
        ctx = run(fake_client, config, state, fake_notifier)
        assert ctx.events == []                     # 首次静默
        assert state.get_game(100) is not None      # 但入库了

        # 第二次运行仍不产生 new_announcement
        ctx2 = run(fake_client, config, state, fake_notifier)
        assert not any(e.event_type == NEW_ANNOUNCEMENT for e in ctx2.events)

    def test_first_seen_silent_but_checkpoints_still_fire(self, fake_client, state, fake_notifier):
        # 首次静默只影响"新游戏公布"，窗口内检查点仍正常触发
        today = D(2026, 8, 14)
        self._setup_publisher(fake_client)
        fake_client.add_creator_apps(45479601, [100])
        fake_client.add_appdetails(100, name="新作", release_date_raw="21 Aug, 2026", coming_soon=True)
        config = make_config(publishers=["任天堂"], notify_on_first_seen=False)
        ctx = run(fake_client, config, state, fake_notifier, today=today)
        types = {e.event_type for e in ctx.events}
        assert NEW_ANNOUNCEMENT not in types       # 无公布通知
        assert CHECKPOINT in types                  # 但检查点照常
        assert state.get_game(100).last_triggered == 1


class TestGameLine:
    """§12.6-2：无日期跟踪 → 公布日期 → date_announced。"""

    def test_no_date_game_later_gets_date_fires_date_announced(self, fake_client, state, fake_notifier):
        config = make_config(games=["app/100/Game"])
        # 第一次：无日期
        fake_client.add_appdetails(100, name="游戏", release_date_raw="Coming soon", coming_soon=True)
        ctx1 = run(fake_client, config, state, fake_notifier)
        assert ctx1.events == []
        record = state.get_game(100)
        assert record is not None
        assert record.release_status == "unknown"
        assert record.release_date is None

        # 第二次：公布具体发售日
        fake_client.add_appdetails(100, name="游戏", release_date_raw="21 Aug, 2026", coming_soon=True)
        today = D(2026, 8, 1)
        ctx2 = run(fake_client, config, state, fake_notifier, today=today)
        types = [e.event_type for e in ctx2.events]
        assert DATE_ANNOUNCED in types
        announced = next(e for e in ctx2.events if e.event_type == DATE_ANNOUNCED)
        assert announced.stage == "发售日公布：将于 2026-08-21 发售"
        assert announced.days_until == 20
        assert state.get_game(100).release_status == "scheduled"

    def test_first_run_triggers_in_window_checkpoints(self, fake_client, state, fake_notifier):
        # §10：首次运行无特殊基线，正常触发窗口内检查点
        config = make_config(games=["100"])
        # 距发售还有 7 天（8/14 检查点当天）
        fake_client.add_appdetails(100, name="游戏", release_date_raw="21 Aug, 2026", coming_soon=True)
        ctx = run(fake_client, config, state, fake_notifier, today=D(2026, 8, 14))
        checkpoint = next(e for e in ctx.events if e.event_type == CHECKPOINT)
        assert checkpoint.stage == "距发售还有 7 天"
        assert state.get_game(100).last_triggered == 1  # +7 是序号 1

    def test_already_released_game_fires_after_release_checkpoint(self, fake_client, state, fake_notifier):
        config = make_config(games=["100"])
        fake_client.add_appdetails(100, name="游戏", release_date_raw="21 Aug, 2026",
                                   coming_soon=False)
        ctx = run(fake_client, config, state, fake_notifier, today=D(2026, 8, 24))
        checkpoint = next(e for e in ctx.events if e.event_type == CHECKPOINT)
        assert checkpoint.stage == "已发售 3 天"
        assert state.get_game(100).last_triggered == 2

    def test_no_checkpoint_before_window(self, fake_client, state, fake_notifier):
        config = make_config(games=["100"])
        fake_client.add_appdetails(100, name="游戏", release_date_raw="21 Aug, 2026", coming_soon=True)
        ctx = run(fake_client, config, state, fake_notifier, today=D(2026, 7, 1))
        assert ctx.events == []
        assert state.get_game(100).last_triggered == -1


class TestDateChange:
    """发售日变动自适应（§7）：重置 last_triggered 并触发 date_changed。"""

    def test_date_changed_resets_last_triggered(self, fake_client, state, fake_notifier):
        config = make_config(games=["100"])
        # run1：8/21 发售，今天 8/7 → +14 检查点触发
        fake_client.add_appdetails(100, name="游戏", release_date_raw="21 Aug, 2026", coming_soon=True)
        run(fake_client, config, state, fake_notifier, today=D(2026, 8, 7))
        assert state.get_game(100).last_triggered == 0

        # run2：跳票到 12/15 → date_changed + last_triggered 重置为 -1
        fake_client.add_appdetails(100, name="游戏", release_date_raw="15 Dec, 2026", coming_soon=True)
        ctx2 = run(fake_client, config, state, fake_notifier, today=D(2026, 8, 7))
        changed = next(e for e in ctx2.events if e.event_type == DATE_CHANGED)
        assert changed.stage == "发售日变更：2026-08-21 → 2026-12-15（跳票）"
        assert changed.old_date == "2026-08-21"
        assert changed.new_date == "2026-12-15"
        assert state.get_game(100).last_triggered == -1

        # run3：新日期下今天仍在窗口外 → 无检查点
        ctx3 = run(fake_client, config, state, fake_notifier, today=D(2026, 8, 7))
        assert not any(e.event_type == CHECKPOINT for e in ctx3.events)

    def test_earlier_date_is_yichu_not_tiaopiao(self, fake_client, state, fake_notifier):
        config = make_config(games=["100"])
        fake_client.add_appdetails(100, name="游戏", release_date_raw="21 Aug, 2026", coming_soon=True)
        run(fake_client, config, state, fake_notifier, today=D(2026, 8, 7))
        fake_client.add_appdetails(100, name="游戏", release_date_raw="10 Aug, 2026", coming_soon=True)
        ctx = run(fake_client, config, state, fake_notifier, today=D(2026, 8, 7))
        changed = next(e for e in ctx.events if e.event_type == DATE_CHANGED)
        assert "（提前）" in changed.stage


class TestUnresolvable:
    def test_name_resolution_failure_logged_not_in_db(self, fake_client, state, fake_notifier):
        config = make_config(games=["不存在的游戏"])
        ctx = run(fake_client, config, state, fake_notifier)
        assert ctx.events == []
        assert any("无法解析游戏" in w for w in ctx.warnings)
        assert state.all_games() == []

    def test_removed_game_warning(self, fake_client, state, fake_notifier):
        config = make_config(games=["100"])
        fake_client.appdetails[100] = None  # success=false / data=null
        ctx = run(fake_client, config, state, fake_notifier)
        assert ctx.events == []
        assert any("下架或被移除" in w for w in ctx.warnings)


class TestBlocked:
    """403 封禁：停止本轮剩余 Steam 请求（§5.4）。"""

    def test_blocked_stops_remaining_requests_but_still_reports(self, fake_client, state, fake_notifier):
        from steam_monitor.steam_api import SteamBlockedError

        class BlockingClient:
            def __init__(self):
                self.details_calls = []

            def get_appdetails(self, appid):
                self.details_calls.append(appid)
                raise SteamBlockedError("收到 HTTP 403 且重试耗尽，判定被封")

            def store_search(self, term):
                return []

        client = BlockingClient()
        config = make_config(games=["300", "301"])
        ctx = run(client, config, state, fake_notifier)
        # 403 后立即停止：300 请求失败后，301 不再请求
        assert client.details_calls == [300]
        assert any("403" in w or "被限制" in w for w in ctx.warnings)
        assert fake_notifier.sent_contexts == [ctx]  # 报告仍生成

