"""checkpoints 单元测试（DESIGN.md §12.2）。"""

from __future__ import annotations

from datetime import date

import pytest

from steam_monitor.checkpoints import (
    checkpoint_date_for,
    highest_pending_checkpoint,
    pending_checkpoints,
)

D = date

RELEASE = D(2026, 8, 21)
CHECKPOINTS = [14, 7, -3]


class TestSignSemantics:
    """+/- 符号语义（§7：+N = 发售前 N 天，-N = 发售后 N 天）。"""

    def test_plus_is_before_release(self):
        assert checkpoint_date_for(RELEASE, 14) == D(2026, 8, 7)

    def test_minus_is_after_release(self):
        assert checkpoint_date_for(RELEASE, -3) == D(2026, 8, 24)

    def test_checkpoint_dates_full_list(self):
        dates = [checkpoint_date_for(RELEASE, v) for v in CHECKPOINTS]
        assert dates == [D(2026, 8, 7), D(2026, 8, 14), D(2026, 8, 24)]


class TestTrigger:
    """触发判定：今天 >= 检查点日期 且 序号 > last_triggered。"""

    def test_none_before_first_checkpoint(self):
        pend = pending_checkpoints(RELEASE, CHECKPOINTS, D(2026, 8, 1), last_triggered=-1)
        assert pend == []

    def test_only_highest_when_multiple_crossed(self):
        # 停机 3 天后三个检查点全部跨越 → 只触发序号最大的（index 2 = -3）
        today = D(2026, 8, 25)
        pend = pending_checkpoints(RELEASE, CHECKPOINTS, today, last_triggered=-1)
        assert [p.index for p in pend] == [0, 1, 2]
        highest = highest_pending_checkpoint(RELEASE, CHECKPOINTS, today, last_triggered=-1)
        assert highest is not None
        assert highest.index == 2
        assert highest.config_value == -3
        assert highest.checkpoint_date == D(2026, 8, 24)

    def test_index_must_exceed_last_triggered(self):
        today = D(2026, 8, 25)
        pend = pending_checkpoints(RELEASE, CHECKPOINTS, today, last_triggered=2)
        assert pend == []
        assert highest_pending_checkpoint(RELEASE, CHECKPOINTS, today, last_triggered=2) is None

    def test_reset_to_minus_one_refires(self):
        # 发售日变更重置 last_triggered = -1 → 窗口内检查点重新全部可触发
        today = D(2026, 8, 25)
        highest = highest_pending_checkpoint(RELEASE, CHECKPOINTS, today, last_triggered=-1)
        assert highest is not None and highest.index == 2

    def test_single_checkpoint_inside_window(self):
        today = D(2026, 8, 10)  # 只跨过 +14（8/7），未到 +7（8/14）
        pend = pending_checkpoints(RELEASE, CHECKPOINTS, today, last_triggered=-1)
        assert [p.index for p in pend] == [0]
        highest = highest_pending_checkpoint(RELEASE, CHECKPOINTS, today, last_triggered=-1)
        assert highest is not None
        assert highest.index == 0
        assert highest.days_until == 11  # 2026-08-21 - 2026-08-10

    def test_days_until_negative_after_release(self):
        today = D(2026, 8, 24)
        highest = highest_pending_checkpoint(RELEASE, CHECKPOINTS, today, last_triggered=-1)
        assert highest is not None
        assert highest.index == 2
        assert highest.days_until == -3

    def test_exact_checkpoint_day_triggers(self):
        # 今天 == 检查点日期 → 触发（>=）
        today = D(2026, 8, 7)
        highest = highest_pending_checkpoint(RELEASE, CHECKPOINTS, today, last_triggered=-1)
        assert highest is not None
        assert highest.index == 0
