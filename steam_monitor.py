#!/usr/bin/env python3
"""Steam 新游戏监控 — CLI 入口（DESIGN.md §3）。

用法：
    python steam_monitor.py once     # 单次检查：检查 → 提醒 → 退出
    python steam_monitor.py daemon   # 常驻循环：每 interval_hours 小时检查一次
    python steam_monitor.py status   # 查看 SQLite 中的跟踪状态
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime

from steam_monitor.config import ConfigError, load_config
from steam_monitor.engine import run_check
from steam_monitor.state import State
from steam_monitor.steam_api import SteamClient

DEFAULT_CONFIG = "config.yaml"
DEFAULT_DB = "state.db"

logger = logging.getLogger("steam_monitor")


def _setup_stdout_utf8() -> None:
    """Windows 下安全地把 stdout 切换为 UTF-8（避免中文输出乱码）。"""
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steam_monitor", description="Steam 新游戏监控脚本")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"配置文件路径（默认 {DEFAULT_CONFIG}）")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"状态数据库路径（默认 {DEFAULT_DB}）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("once", help="单次检查：检查 → 提醒 → 退出")
    sub.add_parser("daemon", help="常驻循环：每隔 interval_hours 小时检查一次")
    sub.add_parser("status", help="查看 SQLite 中当前跟踪的游戏与已触发阶段")
    return parser


def _make_runtime(args: argparse.Namespace):
    config = load_config(args.config)
    state = State(args.db)
    notifier = _make_notifier(config)
    client = SteamClient()
    return config, state, notifier, client


def _make_notifier(config):
    from steam_monitor.notifier import Notifier

    return Notifier(config)


def _print_summary(ctx) -> None:
    print(f"本轮完成，耗时 {ctx.duration:.1f} 秒，触发事件 {len(ctx.events)} 条，警告 {len(ctx.warnings)} 条")
    for event in ctx.events:
        print(f"  [{event.label}] #{event.appid} {event.game_name}：{event.stage}")
    for warning in ctx.warnings:
        print(f"  [警告] {warning}")


def cmd_once(args: argparse.Namespace) -> int:
    config, state, notifier, client = _make_runtime(args)
    try:
        print(f"开始单次检查（配置：{config.source_path}）...")
        ctx = run_check(
            client=client,
            today=date.today(),
            config=config,
            state=state,
            notifier=notifier,
        )
        _print_summary(ctx)
        return 0
    except ConfigError as exc:
        logger.error("配置错误：%s", exc)
        return 1
    except Exception as exc:
        logger.exception("单次检查失败：%s", exc)
        return 1
    finally:
        state.close()


def cmd_daemon(args: argparse.Namespace) -> int:
    config, state, notifier, client = _make_runtime(args)
    interval_seconds = config.interval_hours * 3600.0
    print(f"开始常驻监控，每 {config.interval_hours:.1f} 小时检查一次（Ctrl+C 退出）...")
    try:
        while True:
            try:
                ctx = run_check(
                    client=client,
                    today=date.today(),
                    config=config,
                    state=state,
                    notifier=notifier,
                )
                _print_summary(ctx)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                # §3：循环内异常不得导致进程退出（捕获、记录、继续）
                logger.exception("本次检查失败：%s", exc)
            print(f"休眠 {interval_seconds / 3600.0:.1f} 小时后进行下次检查...")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("收到中断信号，优雅退出。")
        return 0
    finally:
        state.close()


def cmd_status(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.error("配置错误：%s", exc)
        config = None
    state = State(args.db)
    try:
        games = state.all_games()
        print(f"=== 当前跟踪游戏（{len(games)} 个）===")
        for game in games:
            publisher = game.publisher_match or "—"
            release_date = game.release_date.isoformat() if game.release_date else "—"
            print(
                f"#{game.appid} {game.name} | 来源:{game.source} | 发行商:{publisher} "
                f"| 状态:{game.release_status} | 发售日:{release_date} "
                f"| 原文:{game.release_date_raw or '—'} | 已触发:{game.last_triggered}"
            )
        print()
        print("=== 最近事件（20 条）===")
        events = state.recent_events(20)
        if not events:
            print("（无）")
        for event in events:
            print(
                f"#{event['appid']} [{event['event_type']}] {event['stage']} @ {event['created_at']}"
            )
        return 0
    finally:
        state.close()


def main(argv: list[str] | None = None) -> int:
    _setup_stdout_utf8()
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.command == "once":
        return cmd_once(args)
    if args.command == "daemon":
        return cmd_daemon(args)
    if args.command == "status":
        return cmd_status(args)
    parser.error(f"未知命令：{args.command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
