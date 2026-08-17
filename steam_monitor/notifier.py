"""模板渲染 + onepush 投递 + 报告文件生成（DESIGN.md §8.2~§8.4）。"""

from __future__ import annotations

import logging
import string
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import BUILTIN_CONTENT, BUILTIN_TITLE, Channel, Config
from .events import EVENT_ORDER, top_event_per_game

__all__ = ["Notifier", "SendResult", "price_text", "safe_format"]

logger = logging.getLogger(__name__)

#: 报告文件保留份数（§8.4）
REPORT_KEEP = 30


def safe_format(template: str, variables: dict[str, str]) -> str:
    """按 str.format 语法渲染模板；缺失变量渲染为空字符串、永不抛 KeyError。

    变量名仅取字段名的第一段（忽略属性/下标访问）；空值跳过格式说明符，
    避免 ``{price:.2f}`` 遇到空字符串时抛 ValueError。
    """
    formatter = string.Formatter()
    parts: list[str] = []
    for literal, field_name, format_spec, conversion in formatter.parse(template):
        parts.append(literal)
        if field_name is None:
            continue
        base = field_name.split(".")[0].split("[")[0]
        value = variables.get(base, "")
        if conversion == "r":
            value = repr(value)
        elif conversion == "s":
            value = str(value)
        elif conversion == "a":
            value = ascii(value)
        if format_spec and value != "":
            value = format(value, format_spec)
        parts.append(str(value))
    return "".join(parts)


def price_text(is_free: bool | None, price_final: int | None) -> str:
    """价格文本：免费→免费；有价格→¥xx（final/100，最多 2 位小数）；无→空（§8.2）。"""
    if is_free:
        return "免费"
    if price_final is None:
        return ""
    amount = price_final / 100.0
    if amount == int(amount):
        return f"¥{int(amount)}"
    return f"¥{amount:.2f}".rstrip("0").rstrip(".")


@dataclass
class SendResult:
    """一次 send() 的结果统计。"""

    report_path: Path | None = None
    sent: int = 0
    failed: int = 0
    skipped: bool = False  # 本轮无事件 → 渠道全部跳过（报告仍生成）


class Notifier:
    """通知投递与报告文件生成。

    ``notify_func`` 可注入（默认 ``onepush.notify``），``now`` 可注入（默认
    ``datetime.now``），便于测试。
    """

    def __init__(
        self,
        config: Config,
        notify_func: Callable[..., Any] | None = None,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.config = config
        self._notify_func = notify_func if notify_func is not None else self._load_onepush()
        self._now = now

    @staticmethod
    def _load_onepush() -> Callable[..., Any]:
        try:
            import onepush  # noqa: PLC0415

            return onepush.notify
        except ImportError:
            logger.warning("未安装 onepush，通知投递将被跳过（仅生成报告）")

            def _noop_notify(*args: Any, **kwargs: Any) -> None:  # type: ignore[no-untyped-def]
                raise RuntimeError("onepush 未安装，无法投递通知")

            return _noop_notify

    # ---------- 模板 ----------

    def _channel_template(self, channel: Channel) -> tuple[str, str]:
        """模板回退链：渠道模板 → 全局默认模板 → 内置默认（§8.2）。"""
        title = channel.title or self.config.template_title or BUILTIN_TITLE
        content = channel.content or self.config.template_content or BUILTIN_CONTENT
        return title, content

    # ---------- 投递 ----------

    def send(self, context: Any) -> SendResult:
        """生成报告文件（始终），并按渠道投递事件通知（有事件时）。"""
        result = SendResult()
        result.report_path = self._write_report(context)
        events = list(getattr(context, "events", []) or [])
        if not events:
            result.skipped = True
            return result

        top_events = top_event_per_game(events)
        for channel in self.config.channels:
            title_template, content_template = self._channel_template(channel)
            for event in top_events:
                variables = event.variables
                try:
                    title = safe_format(title_template, variables)
                    content = safe_format(content_template, variables)
                    kwargs: dict[str, Any] = dict(
                        provider=channel.provider,
                        title=title,
                        content=content,
                        **channel.params,
                    )
                    if self.config.proxy:
                        kwargs["proxies"] = self.config.proxy
                    self._notify_func(**kwargs)
                    result.sent += 1
                except Exception as exc:
                    result.failed += 1
                    logger.error(
                        "渠道 %s 投递失败（appid=%s）：%s", channel.provider, event.appid, exc
                    )
        return result

    # ---------- 报告文件（§8.4） ----------

    def _write_report(self, context: Any) -> Path:
        report_dir = Path(self.config.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = self._now()
        path = report_dir / f"report-{ts:%Y-%m-%d-%H%M%S}.md"
        text = build_report_text(context, ts)
        path.write_text(text, encoding="utf-8")
        self._retain_recent(report_dir, REPORT_KEEP)
        return path

    def _retain_recent(self, report_dir: Path, keep: int) -> None:
        """保留最近 keep 份报告，旧的自动清理（§8.4）。"""
        try:
            reports = sorted(report_dir.glob("report-*.md"))
        except OSError:
            return
        for old in reports[:-keep] if len(reports) > keep else []:
            try:
                old.unlink()
            except OSError as exc:
                logger.warning("清理旧报告失败：%s：%s", old, exc)


def build_report_text(context: Any, run_at: datetime) -> str:
    """生成报告文件内容（中文，DESIGN.md §8.4）。"""
    events = list(getattr(context, "events", []) or [])
    warnings = list(getattr(context, "warnings", []) or [])
    tracking = list(getattr(context, "tracking", []) or [])
    duration = getattr(context, "duration", 0.0) or 0.0

    lines: list[str] = []
    lines.append("# Steam 新游戏监控报告")
    lines.append("")
    lines.append(f"- 运行时间：{run_at:%Y-%m-%d %H:%M:%S}")
    lines.append(f"- 耗时：{duration:.1f} 秒")
    lines.append(f"- 本轮事件总数：{len(events)}")
    lines.append("")

    # 按事件类型分组
    lines.append("## 本轮事件（按事件类型分组，含全部变量值）")
    if not events:
        lines.append("")
        lines.append("（无）")
    else:
        for event_type in EVENT_ORDER:
            group = [e for e in events if e.event_type == event_type]
            if not group:
                continue
            lines.append("")
            lines.append(f"### {group[0].label}")
            lines.append("")
            lines.append("| appid | 游戏名 | 发行商 | stage | 发售日 | 原文 | 距发售 | 商店 | 价格 |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for event in group:
                lines.append(
                    "| {appid} | {game_name} | {publisher} | {stage} | {release_date} | "
                    "{release_date_raw} | {days_until} | {store_url} | {price} |".format(
                        appid=event.appid,
                        **event.variables,
                    )
                )
    lines.append("")

    # 跟踪状态摘要
    lines.append("## 跟踪状态摘要")
    lines.append("")
    if not tracking:
        lines.append("- 本轮未处理任何游戏")
    else:
        publisher_games = [t for t in tracking if getattr(t, "source", "") == "publisher"]
        no_date_games = [t for t in tracking if not getattr(t, "release_date_raw", "")]
        lines.append(f"- 本轮处理的游戏数：{len(tracking)}")
        lines.append(f"- 发行商旗下跟踪游戏：{len(publisher_games)} 个")
        if no_date_games:
            lines.append("- 无具体发售日游戏：")
            for t in no_date_games:
                lines.append(
                    f"  - #{getattr(t, 'appid', '')} {getattr(t, 'name', '')}"
                    f"（原文：{getattr(t, 'release_date_raw', '') or '空'}）"
                )
        else:
            lines.append("- 无具体发售日游戏：无")
    lines.append("")

    # 错误 / 警告摘要
    lines.append("## 错误 / 警告摘要")
    lines.append("")
    if not warnings:
        lines.append("- 无")
    else:
        for warning in warnings:
            lines.append(f"- {warning}")
    lines.append("")
    return "\n".join(lines)
