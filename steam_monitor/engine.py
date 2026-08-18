"""run_check 编排（DESIGN.md §3/§5/§7/§8/§9/§10）。

run_check 是纯编排函数：Steam 客户端、today()、配置、状态、通知器全部可注入，
便于测试确定性驱动（无真实网络、无真实时钟）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .checkpoints import highest_pending_checkpoint
from .config import Config
from .date_parser import parse_release_date
from .events import (
    CHECKPOINT,
    DATE_ANNOUNCED,
    DATE_CHANGED,
    NEW_ANNOUNCEMENT,
    GameEvent,
)
from .notifier import Notifier, price_text
from .resolver import Resolver
from .state import GameRecord, State
from .steam_api import AppDetails, SteamBlockedError, SteamRequestError

__all__ = ["RunContext", "TrackedGame", "run_check"]

logger = logging.getLogger(__name__)


@dataclass
class TrackedGame:
    """报告用的跟踪状态摘要条目。"""

    appid: int
    name: str
    source: str
    publisher: str = ""
    release_status: str = ""
    release_date_raw: str = ""
    last_triggered: int = -1


@dataclass
class RunContext:
    """一次 run_check 的上下文与结果。"""

    started_at: datetime = field(default_factory=datetime.now)
    duration: float = 0.0
    events: list[GameEvent] = field(default_factory=list)
    tracking: list[TrackedGame] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_check(
    *,
    client: Any,
    today: date | Callable[[], date],
    config: Config,
    state: State,
    notifier: Notifier,
    now: Callable[[], datetime] | None = None,
    progress: Callable[[str], None] | None = None,
) -> RunContext:
    """执行一轮完整检查：两条监控线 → 事件 → 通知 + 报告。

    - ``today`` 可传 date 或返回 date 的可调用对象（测试注入时钟）。
    - ``now`` 默认 ``datetime.now``，可注入固定时间。
    - ``progress`` 可选进度回调，每处理一个阶段调用一次（CLI 接入打印输出）。
    - 403 封禁（SteamBlockedError）→ 停止本轮剩余 Steam 请求，仍生成报告。
    """
    _today = today() if callable(today) else today
    _now = now if now is not None else datetime.now
    started = _now()
    ctx = RunContext(started_at=started)
    resolver = Resolver(client)
    blocked = False

    def _log(msg: str) -> None:
        if progress is not None:
            progress(msg)

    _log(
        f"开始检查：发行商 {len(config.publishers)} 个，游戏 {len(config.games)} 个，"
        f"检查点 {config.checkpoints}"
    )

    # ---------- §5.2 发行商监控线（creator 精准查询） ----------
    for publisher in config.publishers:
        if blocked:
            break
        try:
            appids, clan_id, gid = resolver.discover_creator_appids(publisher)
        except SteamBlockedError as exc:
            ctx.warnings.append(f"Steam 请求被限制（403），停止本轮剩余请求：{exc}")
            blocked = True
            break
        except SteamRequestError as exc:
            ctx.warnings.append(f"发行商「{publisher.name}」候选获取失败：{exc}")
            continue
        if clan_id is None or gid is None:
            ctx.warnings.append(
                f"发行商「{publisher.name}」：creator 查询参数解析失败"
                f"（clan_account_id={'有' if clan_id else '无'}，"
                f"clan_announcement_gid={'有' if gid else '无'}；"
                f"主页未解析到对应字段，请检查发行商名是否正确，"
                f"或显式配置 clan_account_id / clan_announcement_gid）"
            )
            continue
        _log(f"发行商「{publisher.name}」（clan {clan_id}）：候选 {len(appids)} 个")
        total = len(appids)
        for i, appid in enumerate(appids, start=1):
            if blocked:
                break
            if i % 10 == 0 or i == total:
                _log(f"发行商「{publisher.name}」候选处理 {i}/{total}")
            try:
                details = client.get_appdetails(appid)
            except SteamBlockedError as exc:
                ctx.warnings.append(f"Steam 请求被限制（403），停止本轮剩余请求：{exc}")
                blocked = True
                break
            except SteamRequestError as exc:
                ctx.warnings.append(f"appid {appid} 请求失败：{exc}")
                continue
            if details is None:
                ctx.warnings.append(f"appid {appid}：success=false 或 data=null（游戏下架或被移除）")
                continue
            if details.type != "game":
                continue
            _process_game(
                ctx=ctx,
                appid=appid,
                details=details,
                source="publisher",
                publisher_match=publisher.name,
                config=config,
                state=state,
                today=_today,
                now_iso=_now().isoformat(timespec="seconds"),
            )

    # ---------- §5.3 游戏监控线 ----------
    if not blocked:
        total_games = len(config.games)
        for i, entry in enumerate(config.games, start=1):
            _log(f"处理游戏 {i}/{total_games}：{entry}")
            try:
                appid = resolver.resolve_game_entry(entry)
            except SteamBlockedError as exc:
                ctx.warnings.append(f"Steam 请求被限制（403），停止本轮剩余请求：{exc}")
                blocked = True
                break
            except SteamRequestError as exc:
                ctx.warnings.append(f"解析游戏「{entry}」失败：{exc}")
                continue
            if appid is None:
                ctx.warnings.append(f"无法解析游戏（名称/URL/appid）：{entry}")
                continue
            try:
                details = client.get_appdetails(appid)
            except SteamBlockedError as exc:
                ctx.warnings.append(f"Steam 请求被限制（403），停止本轮剩余请求：{exc}")
                blocked = True
                break
            except SteamRequestError as exc:
                ctx.warnings.append(f"appid {appid}（{entry}）请求失败：{exc}")
                continue
            if details is None:
                ctx.warnings.append(f"appid {appid}（{entry}）：success=false 或 data=null（游戏下架或被移除）")
                continue
            if details.type != "game":
                ctx.warnings.append(f"appid {appid}（{entry}）：类型为 {details.type}，跳过（只跟踪 game 类型）")
                continue
            _process_game(
                ctx=ctx,
                appid=appid,
                details=details,
                source="game",
                publisher_match=None,
                config=config,
                state=state,
                today=_today,
                now_iso=_now().isoformat(timespec="seconds"),
            )

    # ---------- §9 归档清理 ----------
    pruned = state.prune_released(_today)
    if pruned:
        logger.info("已归档清理 %d 个发售超过 30 天的游戏", pruned)
        _log(f"已清理 {pruned} 个发售超过 30 天的游戏")

    ctx.duration = (_now() - started).total_seconds()
    _log(
        f"检查完成：事件 {len(ctx.events)} 条，警告 {len(ctx.warnings)} 条，"
        f"耗时 {ctx.duration:.1f} 秒"
    )
    notifier.send(ctx)
    return ctx


# ---------------------------------------------------------------------------
# 单游戏处理
# ---------------------------------------------------------------------------


def _process_game(
    *,
    ctx: RunContext,
    appid: int,
    details: AppDetails,
    source: str,
    publisher_match: str | None,
    config: Config,
    state: State,
    today: date,
    now_iso: str,
) -> None:
    """解析 → 状态更新 → 事件判定（公布/变更/检查点）→ 记录。"""
    parsed = parse_release_date(details.release_date_raw, details.coming_soon)
    prev = state.get_game(appid)
    is_new = prev is None
    last_triggered = -1 if is_new else prev.last_triggered

    game_events: list[GameEvent] = []

    if is_new:
        if source == "publisher" and config.notify_on_first_seen:
            game_events.append(
                _new_announcement_event(appid, details, publisher_match or "", parsed, today)
            )
        # notify_on_first_seen=false：首次看到静默入库（建基线），
        # 之后的发售日公布/变更/检查点通知照常（§10）
    else:
        _handle_date_change(prev, parsed, appid, details, publisher_match, today, game_events)
        # 日期变动 → 检查点按新日期重新计算
        if prev.release_date != parsed.date:
            last_triggered = -1

    # ---------- §7 检查点触发 ----------
    if parsed.date is not None:
        pending = highest_pending_checkpoint(
            parsed.date, config.checkpoints, today, last_triggered
        )
        if pending is not None:
            last_triggered = pending.index
            game_events.append(
                _checkpoint_event(appid, details, publisher_match or "", parsed.date, today, pending.index)
            )

    # ---------- 状态落库 ----------
    state.upsert_game(
        appid=appid,
        name=details.name,
        publishers=details.publishers,
        release_date=parsed.date,
        release_date_raw=parsed.raw,
        release_status=parsed.status,
        source=source,
        publisher_match=publisher_match,
        last_triggered=last_triggered,
        last_seen=now_iso,
    )

    # ---------- 事件记录 ----------
    for event in game_events:
        state.log_event(appid, event.event_type, event.stage, now_iso)
    ctx.events.extend(game_events)
    ctx.tracking.append(
        TrackedGame(
            appid=appid,
            name=details.name,
            source=source,
            publisher=publisher_match or "",
            release_status=parsed.status,
            release_date_raw=parsed.raw,
            last_triggered=last_triggered,
        )
    )


def _handle_date_change(
    prev: GameRecord,
    parsed: Any,
    appid: int,
    details: AppDetails,
    publisher_match: str | None,
    today: date,
    game_events: list[GameEvent],
) -> None:
    """发售日变化的事件判定（§7/§8.1）。"""
    if prev.release_date == parsed.date:
        return
    if prev.release_date is not None and parsed.date is not None:
        # 具体日期 A → 具体日期 B：发售日变更（若新日期为具体值）
        game_events.append(
            _date_changed_event(appid, details, publisher_match or "", prev.release_date, parsed.date, today)
        )
    elif prev.release_date is None and parsed.date is not None:
        # 无具体日期 → 有具体日期：发售日公布
        game_events.append(
            _date_announced_event(appid, details, publisher_match or "", parsed.date, today)
        )
    # 具体日期 → 无具体日期：按最小实现处理，仅重置检查点，不产生事件


# ---------------------------------------------------------------------------
# 事件构造
# ---------------------------------------------------------------------------


def _store_url(appid: int) -> str:
    return f"https://store.steampowered.com/app/{appid}/"


def _date_str(d: date) -> str:
    return d.isoformat()


def _new_announcement_event(
    appid: int, details: AppDetails, publisher: str, parsed: Any, today: date
) -> GameEvent:
    if parsed.date is not None:
        stage = f"新游戏公布：将于 {_date_str(parsed.date)} 发售"
    else:
        stage = "新游戏公布（发售日未定）"
    return GameEvent(
        event_type=NEW_ANNOUNCEMENT,
        appid=appid,
        game_name=details.name,
        stage=stage,
        publisher=publisher,
        release_date=_date_str(parsed.date) if parsed.date else "",
        release_date_raw=parsed.raw,
        days_until=(parsed.date - today).days if parsed.date else None,
        store_url=_store_url(appid),
        price=price_text(details.is_free, details.price_final),
    )


def _date_announced_event(
    appid: int, details: AppDetails, publisher: str, release_date: date, today: date
) -> GameEvent:
    return GameEvent(
        event_type=DATE_ANNOUNCED,
        appid=appid,
        game_name=details.name,
        stage=f"发售日公布：将于 {_date_str(release_date)} 发售",
        publisher=publisher,
        release_date=_date_str(release_date),
        release_date_raw=details.release_date_raw,
        days_until=(release_date - today).days,
        store_url=_store_url(appid),
        price=price_text(details.is_free, details.price_final),
    )


def _date_changed_event(
    appid: int,
    details: AppDetails,
    publisher: str,
    old_date: date,
    new_date: date,
    today: date,
) -> GameEvent:
    if new_date > old_date:
        suffix = "（跳票）"
    else:
        suffix = "（提前）"
    return GameEvent(
        event_type=DATE_CHANGED,
        appid=appid,
        game_name=details.name,
        stage=f"发售日变更：{_date_str(old_date)} → {_date_str(new_date)}{suffix}",
        publisher=publisher,
        release_date=_date_str(new_date),
        release_date_raw=details.release_date_raw,
        days_until=(new_date - today).days,
        store_url=_store_url(appid),
        price=price_text(details.is_free, details.price_final),
        old_date=_date_str(old_date),
        new_date=_date_str(new_date),
    )


def _checkpoint_event(
    appid: int,
    details: AppDetails,
    publisher: str,
    release_date: date,
    today: date,
    index: int,
) -> GameEvent:
    days_until = (release_date - today).days
    if days_until >= 0:
        stage = f"距发售还有 {days_until} 天"
    else:
        stage = f"已发售 {-days_until} 天"
    return GameEvent(
        event_type=CHECKPOINT,
        appid=appid,
        game_name=details.name,
        stage=stage,
        publisher=publisher,
        release_date=_date_str(release_date),
        release_date_raw=details.release_date_raw,
        days_until=days_until,
        store_url=_store_url(appid),
        price=price_text(details.is_free, details.price_final),
    )
