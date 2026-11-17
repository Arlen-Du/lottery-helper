"""SQLite persistence layer for historical lottery draw records."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional

from src.core.models import DrawRecord


class SQLiteStorage:
    """Thread-safe SQLite storage for DrawRecord objects."""

    def __init__(self, db_path: str | Path = "data/lottery.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS draws (
                    lottery_code TEXT NOT NULL,
                    issue TEXT NOT NULL,
                    draw_date TEXT NOT NULL DEFAULT '',
                    numbers_json TEXT NOT NULL,
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (lottery_code, issue)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_draws_code_issue
                ON draws (lottery_code, issue DESC)
                """
            )
            conn.commit()

    def save_draws(self, records: List[DrawRecord]) -> int:
        """Upsert draw records and return the number of newly inserted or updated rows."""
        if not records:
            return 0
        rows = [
            (
                r.lottery_code,
                r.issue,
                r.draw_date,
                json.dumps(r.numbers, ensure_ascii=False),
                json.dumps(r.extra, ensure_ascii=False),
            )
            for r in records
        ]
        with self._lock, self._connect() as conn:
            cursor = conn.executemany(
                """
                INSERT INTO draws (lottery_code, issue, draw_date, numbers_json, extra_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(lottery_code, issue) DO UPDATE SET
                    draw_date = excluded.draw_date,
                    numbers_json = excluded.numbers_json,
                    extra_json = excluded.extra_json
                """,
                rows,
            )
            conn.commit()
            return cursor.rowcount

    def get_draws(self, lottery_code: str, limit: Optional[int] = None) -> List[DrawRecord]:
        """Fetch draw records ordered by issue descending (newest first)."""
        sql = """
            SELECT lottery_code, issue, draw_date, numbers_json, extra_json
            FROM draws
            WHERE lottery_code = ?
            ORDER BY issue DESC
        """
        params: list = [lottery_code]
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            DrawRecord(
                lottery_code=row[0],
                issue=row[1],
                draw_date=row[2],
                numbers=json.loads(row[3]),
                extra=json.loads(row[4]) if row[4] else {},
            )
            for row in rows
        ]

    def count_draws(self, lottery_code: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM draws WHERE lottery_code = ?",
                (lottery_code,),
            ).fetchone()
            return int(row[0]) if row else 0

    def clear_draws(self, lottery_code: str) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM draws WHERE lottery_code = ?",
                (lottery_code,),
            )
            conn.commit()
            return cursor.rowcount
