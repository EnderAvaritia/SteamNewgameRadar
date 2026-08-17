"""state 单元测试（DESIGN.md §12.3）：INSERT/UPDATE、发售日变更、归档清理。"""

from __future__ import annotations

from datetime import date, datetime

from steam_monitor.state import State

D = date


def _upsert(s: State, appid=100, release_date=D(2026, 8, 21), status="scheduled", last_triggered=-1, **kw):
    defaults = dict(
        appid=appid,
        name=f"游戏{appid}",
        publishers=["任天堂"],
        release_date=release_date,
        release_date_raw="21 Aug, 2026",
        release_status=status,
        source="publisher",
        publisher_match="任天堂",
        last_triggered=last_triggered,
        last_seen="2026-08-18T10:00:00",
    )
    defaults.update(kw)
    s.upsert_game(**defaults)


class TestInsertUpdate:
    def test_insert_then_get(self, state):
        _upsert(state, appid=100)
        record = state.get_game(100)
        assert record is not None
        assert record.appid == 100
        assert record.name == "游戏100"
        assert record.publishers == ["任天堂"]
        assert record.release_date == D(2026, 8, 21)
        assert record.release_date_raw == "21 Aug, 2026"
        assert record.release_status == "scheduled"
        assert record.source == "publisher"
        assert record.publisher_match == "任天堂"
        assert record.last_triggered == -1

    def test_update_existing_fields(self, state):
        _upsert(state, appid=100)
        _upsert(state, appid=100, release_date=D(2026, 9, 1), status="released", last_triggered=2)
        record = state.get_game(100)
        assert record.release_date == D(2026, 9, 1)
        assert record.release_status == "released"
        assert record.last_triggered == 2

    def test_missing_game_returns_none(self, state):
        assert state.get_game(999) is None

    def test_all_games(self, state):
        _upsert(state, appid=100)
        _upsert(state, appid=200)
        assert [g.appid for g in state.all_games()] == [100, 200]

    def test_set_last_triggered(self, state):
        _upsert(state, appid=100)
        state.set_last_triggered(100, 2)
        assert state.get_game(100).last_triggered == 2


class TestReleaseDateChange:
    def test_upsert_overwrites_release_date(self, state):
        _upsert(state, appid=100, release_date=D(2026, 8, 21))
        _upsert(state, appid=100, release_date=D(2026, 12, 15))
        assert state.get_game(100).release_date == D(2026, 12, 15)

    def test_release_date_none(self, state):
        _upsert(state, appid=100, release_date=None, status="unknown", release_date_raw="Coming soon")
        assert state.get_game(100).release_date is None
        assert state.get_game(100).release_status == "unknown"


class TestPrune:
    def test_prune_released_older_than_30_days(self, state, tmp_path):
        today = D(2026, 8, 18)
        _upsert(state, appid=100, release_date=D(2026, 7, 1), status="released")   # 48 天前 → 删
        _upsert(state, appid=200, release_date=D(2026, 8, 1), status="released")   # 17 天前 → 留
        _upsert(state, appid=300, release_date=D(2026, 9, 1), status="scheduled")  # 未发售 → 留

        removed = state.prune_released(today, max_age_days=30)
        assert removed == 1
        assert state.get_game(100) is None
        assert state.get_game(200) is not None
        assert state.get_game(300) is not None

    def test_prune_exactly_30_days_kept(self, state):
        today = D(2026, 8, 18)
        _upsert(state, appid=100, release_date=D(2026, 7, 19), status="released")  # 30 天 → 留
        assert state.prune_released(today, max_age_days=30) == 0
        assert state.get_game(100) is not None

    def test_events_log_retained_after_prune(self, state):
        today = D(2026, 8, 18)
        _upsert(state, appid=100, release_date=D(2026, 7, 1), status="released")
        state.log_event(100, "checkpoint", "已发售 48 天", "2026-08-18T10:00:00")
        state.prune_released(today, max_age_days=30)
        assert state.get_game(100) is None
        events = state.recent_events(10)
        assert len(events) == 1
        assert events[0]["appid"] == 100
        assert events[0]["event_type"] == "checkpoint"


class TestEventsLog:
    def test_log_and_recent_events(self, state):
        state.log_event(100, "date_announced", "发售日公布：将于 2026-08-21 发售", "2026-08-18T10:00:00")
        state.log_event(100, "checkpoint", "距发售还有 7 天", "2026-08-18T10:00:00")
        events = state.recent_events(20)
        assert [e["event_type"] for e in events] == ["checkpoint", "date_announced"]  # 新在前
        assert events[1]["stage"] == "发售日公布：将于 2026-08-21 发售"
        assert events[1]["created_at"] == "2026-08-18T10:00:00"
