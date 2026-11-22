"""SQLite persistence layer for historical lottery draw records."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.models import DrawRecord, PurchaseRecord


class SQLiteStorage:
    """Thread-safe SQLite storage for DrawRecord, PurchaseRecord, and app settings."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            env_path = os.environ.get("LOTTERY_DB_PATH", "").strip()
            if env_path:
                self.db_path = Path(env_path)
            else:
                self.db_path = Path.home() / ".lottery-helper" / "lottery.db"
        else:
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lottery_code TEXT NOT NULL,
                    target_issue TEXT NOT NULL,
                    numbers_json TEXT NOT NULL,
                    bet_count INTEGER NOT NULL DEFAULT 1,
                    cost INTEGER NOT NULL DEFAULT 2,
                    status TEXT NOT NULL DEFAULT 'pending',
                    prize_tier TEXT NOT NULL DEFAULT '',
                    prize_amount INTEGER NOT NULL DEFAULT 0,
                    draw_numbers_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
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

    def add_purchases(self, records: List[PurchaseRecord]) -> int:
        if not records:
            return 0
        rows = [
            (
                r.lottery_code,
                r.target_issue,
                json.dumps(r.numbers, ensure_ascii=False),
                r.bet_count,
                r.cost,
                r.status,
                r.prize_tier,
                r.prize_amount,
                json.dumps(r.draw_numbers, ensure_ascii=False),
            )
            for r in records
        ]
        with self._lock, self._connect() as conn:
            cursor = conn.executemany(
                """
                INSERT INTO purchases (
                    lottery_code, target_issue, numbers_json, bet_count, cost,
                    status, prize_tier, prize_amount, draw_numbers_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            return cursor.rowcount

    def get_purchases(self, lottery_code: Optional[str] = None) -> List[PurchaseRecord]:
        sql = """
            SELECT id, lottery_code, target_issue, numbers_json, bet_count, cost,
                   status, prize_tier, prize_amount, draw_numbers_json, created_at
            FROM purchases
        """
        params: list = []
        if lottery_code:
            sql += " WHERE lottery_code = ?"
            params.append(lottery_code)
        sql += " ORDER BY id DESC"

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            PurchaseRecord(
                id=row[0],
                lottery_code=row[1],
                target_issue=row[2],
                numbers=json.loads(row[3]),
                bet_count=row[4],
                cost=row[5],
                status=row[6],
                prize_tier=row[7],
                prize_amount=row[8],
                draw_numbers=json.loads(row[9]) if row[9] else {},
                created_at=str(row[10] or ""),
            )
            for row in rows
        ]

    def update_purchase_settlement(
        self,
        purchase_id: int,
        prize_tier: str,
        prize_amount: int,
        draw_numbers: Dict[str, List[int]],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE purchases
                SET status = 'settled',
                    prize_tier = ?,
                    prize_amount = ?,
                    draw_numbers_json = ?
                WHERE id = ?
                """,
                (prize_tier, prize_amount, json.dumps(draw_numbers, ensure_ascii=False), purchase_id),
            )
            conn.commit()

    def delete_purchases(self, purchase_ids: List[int]) -> int:
        if not purchase_ids:
            return 0
        placeholders = ",".join("?" for _ in purchase_ids)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM purchases WHERE id IN ({placeholders})",
                purchase_ids,
            )
            conn.commit()
            return cursor.rowcount

    def clear_purchases(self, lottery_code: str) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM purchases WHERE lottery_code = ?",
                (lottery_code,),
            )
            conn.commit()
            return cursor.rowcount

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return default

    def set_setting(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, encoded),
            )
            conn.commit()
