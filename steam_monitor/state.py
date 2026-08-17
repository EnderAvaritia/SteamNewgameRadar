"""SQLite 状态存取（DESIGN.md §9）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

__all__ = ["GameRecord", "State"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    appid INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    publishers TEXT,              -- JSON 数组
    release_date TEXT,            -- ISO 日期或 NULL
    release_date_raw TEXT,        -- 原文
    release_status TEXT,          -- 'released' | 'scheduled' | 'fuzzy' | 'unknown'
    source TEXT,                  -- 'publisher' | 'game'
    publisher_match TEXT,         -- 命中的发行商名（publisher 线）
    last_triggered INTEGER,       -- -1 未触发，0..n 已触发的检查点序号
    last_seen TEXT                -- 上次见到该游戏的时间（ISO datetime）
);

CREATE TABLE IF NOT EXISTS events_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appid INTEGER, event_type TEXT, stage TEXT, created_at TEXT
);
"""


@dataclass
class GameRecord:
    """games 表中的一条游戏记录。"""

    appid: int
    name: str
    publishers: list[str]
    release_date: date | None
    release_date_raw: str
    release_status: str
    source: str
    publisher_match: str | None
    last_triggered: int
    last_seen: str


class State:
    """state.db 的读写封装（单文件 SQLite，标准库）。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    # ---------- 基础 ----------

    def _init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- games ----------

    def get_game(self, appid: int) -> GameRecord | None:
        row = self.conn.execute(
            "SELECT * FROM games WHERE appid = ?", (appid,)
        ).fetchone()
        return self._row_to_game(row) if row is not None else None

    def all_games(self) -> list[GameRecord]:
        rows = self.conn.execute(
            "SELECT * FROM games ORDER BY appid"
        ).fetchall()
        return [self._row_to_game(r) for r in rows]

    def upsert_game(
        self,
        *,
        appid: int,
        name: str,
        publishers: list[str],
        release_date: date | None,
        release_date_raw: str,
        release_status: str,
        source: str,
        publisher_match: str | None,
        last_triggered: int,
        last_seen: str,
    ) -> None:
        """插入新游戏或更新已有游戏的全部字段。"""
        self.conn.execute(
            """
            INSERT INTO games (
                appid, name, publishers, release_date, release_date_raw,
                release_status, source, publisher_match, last_triggered, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name = excluded.name,
                publishers = excluded.publishers,
                release_date = excluded.release_date,
                release_date_raw = excluded.release_date_raw,
                release_status = excluded.release_status,
                source = excluded.source,
                publisher_match = excluded.publisher_match,
                last_triggered = excluded.last_triggered,
                last_seen = excluded.last_seen
            """,
            (
                appid,
                name,
                json.dumps(publishers, ensure_ascii=False),
                release_date.isoformat() if release_date else None,
                release_date_raw,
                release_status,
                source,
                publisher_match,
                last_triggered,
                last_seen,
            ),
        )
        self.conn.commit()

    def set_last_triggered(self, appid: int, index: int) -> None:
        self.conn.execute(
            "UPDATE games SET last_triggered = ? WHERE appid = ?", (index, appid)
        )
        self.conn.commit()

    def prune_released(self, today: date, max_age_days: int = 30) -> int:
        """归档：删除「已发售且超过 30 天」的游戏（events_log 保留，§9）。"""
        rows = self.conn.execute(
            "SELECT appid, release_date FROM games "
            "WHERE release_status = 'released' AND release_date IS NOT NULL"
        ).fetchall()
        removed = 0
        for row in rows:
            try:
                released_on = date.fromisoformat(row["release_date"])
            except ValueError:
                continue
            if (today - released_on).days > max_age_days:
                self.conn.execute(
                    "DELETE FROM games WHERE appid = ?", (row["appid"],)
                )
                removed += 1
        self.conn.commit()
        return removed

    # ---------- events_log ----------

    def log_event(
        self,
        appid: int,
        event_type: str,
        stage: str,
        created_at: str | None = None,
    ) -> None:
        created = created_at or datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO events_log (appid, event_type, stage, created_at) "
            "VALUES (?, ?, ?, ?)",
            (appid, event_type, stage, created),
        )
        self.conn.commit()

    def recent_events(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 内部 ----------

    @staticmethod
    def _row_to_game(row: sqlite3.Row) -> GameRecord:
        release_date = None
        if row["release_date"]:
            try:
                release_date = date.fromisoformat(row["release_date"])
            except ValueError:
                release_date = None
        publishers: list[str] = []
        if row["publishers"]:
            try:
                parsed = json.loads(row["publishers"])
                if isinstance(parsed, list):
                    publishers = [str(x) for x in parsed]
            except (json.JSONDecodeError, TypeError):
                publishers = []
        return GameRecord(
            appid=row["appid"],
            name=row["name"],
            publishers=publishers,
            release_date=release_date,
            release_date_raw=row["release_date_raw"] or "",
            release_status=row["release_status"] or "",
            source=row["source"] or "",
            publisher_match=row["publisher_match"],
            last_triggered=row["last_triggered"] if row["last_triggered"] is not None else -1,
            last_seen=row["last_seen"] or "",
        )
