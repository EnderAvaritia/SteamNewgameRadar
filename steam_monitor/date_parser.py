"""发售日防御性解析（DESIGN.md §6）。

四层策略：released（已发售）→ scheduled（具体发售日）→ fuzzy（模糊）→ unknown（未知）。
月份名解析做 locale 无关的规范化（英文月份名 → 数字），避免中文 locale 下
``strptime("%b")`` 解析失败的问题。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

__all__ = ["ParsedDate", "parse_release_date"]

#: 英文月份名（缩写 + 全称）→ 数字，用于 locale 无关的月份规范化
_MONTHS: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_MONTH_WORD_PATTERN = re.compile(r"\b([A-Za-z]{3,9})\b")
#: 年份段检测（不使用 \b：CJK 字符也算 \w，\b 在 "6年" 之间不成立）
_YEAR_PATTERN = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")

#: 规范化后尝试的数值日期格式
_FORMATS = (
    "%Y %m %d",  # 2026-08-21 / 2026/8/21
    "%d %m %Y",  # 21 Aug, 2026 / 21 Aug 2026
    "%m %d %Y",  # Aug 21, 2026
)

#: 状态常量
RELEASED = "released"
SCHEDULED = "scheduled"
FUZZY = "fuzzy"
UNKNOWN = "unknown"
CONCRETE_STATUSES = (RELEASED, SCHEDULED)


@dataclass(frozen=True)
class ParsedDate:
    """发售日解析结果。"""

    status: str          # released | scheduled | fuzzy | unknown
    date: date | None    # 具体日期（仅 released / scheduled 时非空）
    raw: str             # Steam 返回的原文（trimmed）

    @property
    def concrete(self) -> bool:
        """是否有具体日期。"""
        return self.date is not None


def _normalize(raw: str) -> str:
    """把英文月份名替换为两位数字，统一分隔符为空格。

    同时兼容 schinese 中文日期格式：把「年/月」当分隔符、「日」当结束符
    （"2026 年 8 月 21 日" → "2026 8 21"）。
    """
    def _replace_month(match: re.Match) -> str:
        word = match.group(1).lower()
        if word in _MONTHS:
            return f"{_MONTHS[word]:02d}"
        return match.group(1)

    s = _MONTH_WORD_PATTERN.sub(_replace_month, raw)
    s = re.sub(r"[年月]", " ", s)
    s = re.sub(r"日", " ", s)
    s = s.replace(",", " ").replace("/", " ").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


def _try_parse(raw: str) -> date | None:
    numeric = _normalize(raw)
    if not numeric:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(numeric, fmt).date()
        except ValueError:
            continue
    return None


def parse_release_date(raw: str | None, coming_soon: bool | None) -> ParsedDate:
    """按 DESIGN.md §6 的四层策略解析发售日。"""
    raw_str = (raw or "").strip()
    if not raw_str:
        return ParsedDate(status=UNKNOWN, date=None, raw=raw_str)

    concrete = _try_parse(raw_str)
    if concrete is not None:
        # coming_soon 为 False → 已发售；否则（True 或缺省）→ 具体发售日
        status = RELEASED if coming_soon is False else SCHEDULED
        return ParsedDate(status=status, date=concrete, raw=raw_str)

    if _YEAR_PATTERN.search(raw_str):
        # 含年份段（如 "Q3 2026"、"2026"）→ 模糊
        return ParsedDate(status=FUZZY, date=None, raw=raw_str)

    # 其他（"Coming soon"、空等）→ 未知
    return ParsedDate(status=UNKNOWN, date=None, raw=raw_str)
