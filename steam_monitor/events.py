"""事件类型定义与优先级（DESIGN.md §8.1）。"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "DATE_ANNOUNCED",
    "DATE_CHANGED",
    "CHECKPOINT",
    "NEW_ANNOUNCEMENT",
    "EVENT_ORDER",
    "GameEvent",
    "PRIORITY",
    "sort_by_priority",
    "top_event_per_game",
]

DATE_ANNOUNCED = "date_announced"
DATE_CHANGED = "date_changed"
CHECKPOINT = "checkpoint"
NEW_ANNOUNCEMENT = "new_announcement"

#: 优先级（数值大者优先）：date_announced > date_changed > checkpoint > new_announcement
PRIORITY: dict[str, int] = {
    DATE_ANNOUNCED: 4,
    DATE_CHANGED: 3,
    CHECKPOINT: 2,
    NEW_ANNOUNCEMENT: 1,
}

#: 报告分组顺序
EVENT_ORDER: tuple[str, ...] = (
    DATE_ANNOUNCED,
    DATE_CHANGED,
    CHECKPOINT,
    NEW_ANNOUNCEMENT,
)

_EVENT_LABELS: dict[str, str] = {
    DATE_ANNOUNCED: "发售日公布",
    DATE_CHANGED: "发售日变更",
    CHECKPOINT: "检查点",
    NEW_ANNOUNCEMENT: "新游戏公布",
}

_TEMPLATE_KEYS = (
    "game_name",
    "publisher",
    "stage",
    "release_date",
    "release_date_raw",
    "days_until",
    "store_url",
    "price",
)


@dataclass
class GameEvent:
    """一条游戏事件；模板变量见 _TEMPLATE_KEYS（§8.2）。"""

    event_type: str
    appid: int
    game_name: str
    stage: str = ""
    publisher: str = ""
    release_date: str = ""       # YYYY-MM-DD；无具体日期时为空
    release_date_raw: str = ""
    days_until: int | None = None
    store_url: str = ""
    price: str = ""
    old_date: str | None = None  # date_changed 的旧日期
    new_date: str | None = None  # date_changed 的新日期

    @property
    def label(self) -> str:
        """事件的中文标签。"""
        return _EVENT_LABELS.get(self.event_type, self.event_type)

    @property
    def variables(self) -> dict[str, str]:
        """模板渲染变量（缺失变量 → 空字符串，§8.2）。"""
        return {
            "game_name": self.game_name,
            "publisher": self.publisher,
            "stage": self.stage,
            "release_date": self.release_date,
            "release_date_raw": self.release_date_raw,
            "days_until": "" if self.days_until is None else str(self.days_until),
            "store_url": self.store_url,
            "price": self.price,
        }

    @property
    def template_keys(self) -> tuple[str, ...]:
        return _TEMPLATE_KEYS


def sort_by_priority(events: list[GameEvent]) -> list[GameEvent]:
    """按优先级降序排序（同一游戏内取最高优先级的场景）。"""
    return sorted(events, key=lambda e: PRIORITY.get(e.event_type, 0), reverse=True)


def top_event_per_game(events: list[GameEvent]) -> list[GameEvent]:
    """每个游戏每轮最多一条通知：按 appid 分组，每组取优先级最高的一个（§8.1）。

    返回顺序按优先级降序。
    """
    best: dict[int, GameEvent] = {}
    for event in events:
        current = best.get(event.appid)
        if current is None or PRIORITY.get(event.event_type, 0) > PRIORITY.get(
            current.event_type, 0
        ):
            best[event.appid] = event
    return sort_by_priority(list(best.values()))
