"""检查点计算与触发判定（DESIGN.md §7）。

检查点配置形如 [+14, +7, -3]：``+N`` 表示发售前 N 天触发（日期更早），
``-N`` 表示发售后 N 天触发（日期更晚）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

__all__ = [
    "PendingCheckpoint",
    "checkpoint_date_for",
    "highest_pending_checkpoint",
    "pending_checkpoints",
]


@dataclass(frozen=True)
class PendingCheckpoint:
    """一个满足触发条件的检查点。"""

    index: int             # 配置列表中的序号（0 起）
    config_value: int      # 配置值，如 +14 / -3
    checkpoint_date: date  # 该检查点的具体日期
    days_until: int        # 距发售天数（正=未发售，负=已发售）


def checkpoint_date_for(release_date: date, config_value: int) -> date:
    """检查点日期（DESIGN.md §7 注释语义）。

    ``+N`` → release_date 前 N 天（发售前 N 天触发）；``-N`` → release_date 后 N 天。
    例：``+14`` → release_date - 14 天；``-3`` → release_date + 3 天。
    """
    return release_date - timedelta(days=config_value)


def pending_checkpoints(
    release_date: date,
    checkpoints: list[int],
    today: date,
    last_triggered: int,
) -> list[PendingCheckpoint]:
    """返回所有满足「今天 >= 检查点日期 且 序号 > last_triggered」的检查点。"""
    pend: list[PendingCheckpoint] = []
    days_until = (release_date - today).days
    for index, value in enumerate(checkpoints):
        if index <= last_triggered:
            continue
        cp_date = checkpoint_date_for(release_date, value)
        if today >= cp_date:
            pend.append(PendingCheckpoint(index, value, cp_date, days_until))
    return pend


def highest_pending_checkpoint(
    release_date: date,
    checkpoints: list[int],
    today: date,
    last_triggered: int,
) -> PendingCheckpoint | None:
    """同一天跨越多个检查点时只取序号最大的（§7）。"""
    pend = pending_checkpoints(release_date, checkpoints, today, last_triggered)
    if not pend:
        return None
    return max(pend, key=lambda p: p.index)
