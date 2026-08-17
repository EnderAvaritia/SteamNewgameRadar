"""date_parser 单元测试（DESIGN.md §12.1）。"""

from __future__ import annotations

from datetime import date

import pytest

from steam_monitor.date_parser import (
    FUZZY,
    RELEASED,
    SCHEDULED,
    UNKNOWN,
    parse_release_date,
)

D = date


class TestFormats:
    """各日期格式（英文月份缩写，locale 无关）。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("21 Aug, 2026", D(2026, 8, 21)),
            ("Aug 21, 2026", D(2026, 8, 21)),
            ("2026-08-21", D(2026, 8, 21)),
            ("2026/8/21", D(2026, 8, 21)),
            ("21 Aug 2026", D(2026, 8, 21)),
            ("21 August, 2026", D(2026, 8, 21)),   # 全称月份
            ("December 31, 2025", D(2025, 12, 31)),
            ("1 Jan, 2030", D(2030, 1, 1)),
        ],
    )
    def test_parses_concrete_dates(self, raw, expected):
        parsed = parse_release_date(raw, coming_soon=True)
        assert parsed.date == expected
        assert parsed.raw == raw.strip()

    def test_trailing_whitespace_stripped(self):
        parsed = parse_release_date("  21 Aug, 2026  ", coming_soon=True)
        assert parsed.date == D(2026, 8, 21)
        assert parsed.raw == "21 Aug, 2026"


class TestChineseFormats:
    """schinese 中文日期格式（l=schinese 下 Steam 返回"年/月/日"）"""

    @pytest.mark.parametrize(
        "raw",
        [
            "2026 年 8 月 21 日",
            "2026年8月21日",
            "2026 年 8月21日",
        ],
    )
    def test_parses_chinese_dates(self, raw):
        parsed = parse_release_date(raw, coming_soon=True)
        assert parsed.date == D(2026, 8, 21)
        assert parsed.status == SCHEDULED
        assert parsed.raw == raw

    def test_chinese_year_month_only_is_fuzzy(self):
        parsed = parse_release_date("2026 年 8 月", coming_soon=True)
        assert parsed.status == FUZZY
        assert parsed.date is None

    def test_chinese_year_only_is_fuzzy(self):
        parsed = parse_release_date("2026 年", coming_soon=True)
        assert parsed.status == FUZZY
        assert parsed.date is None


class TestComingSoon:
    """coming_soon 与状态的组合（DESIGN.md §6 策略 1/2）。"""

    def test_coming_soon_false_is_released(self):
        parsed = parse_release_date("21 Aug, 2026", coming_soon=False)
        assert parsed.status == RELEASED
        assert parsed.date == D(2026, 8, 21)

    def test_coming_soon_true_is_scheduled(self):
        parsed = parse_release_date("21 Aug, 2026", coming_soon=True)
        assert parsed.status == SCHEDULED
        assert parsed.date == D(2026, 8, 21)

    def test_coming_soon_missing_is_scheduled(self):
        parsed = parse_release_date("21 Aug, 2026", coming_soon=None)
        assert parsed.status == SCHEDULED
        assert parsed.date == D(2026, 8, 21)


class TestFuzzyAndUnknown:
    """模糊 / 未知（DESIGN.md §6 策略 3/4）。"""

    @pytest.mark.parametrize("raw", ["Q3 2026", "2026", "2026年", "Fall 2027"])
    def test_year_only_is_fuzzy(self, raw):
        parsed = parse_release_date(raw, coming_soon=True)
        assert parsed.status == FUZZY
        assert parsed.date is None
        assert parsed.raw == raw

    @pytest.mark.parametrize("raw", ["Coming soon", "To be announced", "TBA"])
    def test_no_year_is_unknown(self, raw):
        parsed = parse_release_date(raw, coming_soon=True)
        assert parsed.status == UNKNOWN
        assert parsed.date is None
        assert parsed.raw == raw

    def test_empty_is_unknown(self):
        parsed = parse_release_date("", coming_soon=True)
        assert parsed.status == UNKNOWN
        assert parsed.date is None

    def test_none_is_unknown(self):
        parsed = parse_release_date(None, coming_soon=True)
        assert parsed.status == UNKNOWN
        assert parsed.date is None

    def test_fuzzy_keeps_raw_string(self):
        parsed = parse_release_date("Q3 2026", coming_soon=True)
        assert parsed.raw == "Q3 2026"
