"""Unit and integration tests for the FastAPI web backend and auto-sync scheduler."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import List

from fastapi.testclient import TestClient

from src.core.models import DrawRecord
from src.data.adapters import AdapterManager
from src.data.storage import SQLiteStorage
from src.web.app import create_app


class StubAdapterManager(AdapterManager):
    """Deterministic offline stub for testing online sync and scheduler."""

    def fetch_online(self, lottery_code: str, limit: int = 100) -> List[DrawRecord]:
        if lottery_code == "ssq":
            return [
                DrawRecord(
                    lottery_code="ssq",
                    issue="2026101",
                    draw_date="2026-11-20",
                    numbers={"red": [1, 5, 10, 15, 20, 25], "blue": [8]},
                ),
                DrawRecord(
                    lottery_code="ssq",
                    issue="2026100",
                    draw_date="2026-11-18",
                    numbers={"red": [2, 6, 11, 16, 21, 26], "blue": [9]},
                ),
            ]
        if lottery_code == "dlt":
            return [
                DrawRecord(
                    lottery_code="dlt",
                    issue="2026101",
                    draw_date="2026-11-20",
                    numbers={"front": [3, 9, 15, 22, 31], "back": [2, 11]},
                )
            ]
        return [
            DrawRecord(
                lottery_code="fc3d",
                issue="2026301",
                draw_date="2026-11-20",
                numbers={"hundreds": [1], "tens": [2], "units": [3]},
            )
        ]


class TestWebAndScheduler(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_web.db"
        self.app = create_app(
            db_path=self.db_path,
            adapter_manager=StubAdapterManager(),
            start_scheduler=False,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_storage_respects_env_db_path(self) -> None:
        env_db = Path(self._tmpdir.name) / "from_env.db"
        old_val = os.environ.get("LOTTERY_DB_PATH")
        try:
            os.environ["LOTTERY_DB_PATH"] = str(env_db)
            storage = SQLiteStorage()
            self.assertEqual(storage.db_path, env_db)
            storage.set_setting("k1", {"a": 1})
            self.assertEqual(storage.get_setting("k1"), {"a": 1})
        finally:
            if old_val is None:
                os.environ.pop("LOTTERY_DB_PATH", None)
            else:
                os.environ["LOTTERY_DB_PATH"] = old_val

    def test_health_and_lotteries(self) -> None:
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        resp = self.client.get("/api/lotteries")
        self.assertEqual(resp.status_code, 200)
        codes = [item["code"] for item in resp.json()]
        self.assertEqual(codes, ["ssq", "dlt", "fc3d"])

    def test_mock_analysis_generate_evaluate_and_backtest(self) -> None:
        r_mock = self.client.post("/api/draws/ssq/mock", json={"limit": 80})
        self.assertEqual(r_mock.status_code, 200)
        self.assertEqual(r_mock.json()["generated"], 80)

        r_analysis = self.client.get("/api/analysis/ssq?window=50")
        self.assertEqual(r_analysis.status_code, 200)
        self.assertEqual(r_analysis.json()["patterns"]["total"], 50)

        r_gen = self.client.post(
            "/api/generate/ssq",
            json={"strategy": "composite", "count": 3, "window": 50, "deterministic": True},
        )
        self.assertEqual(r_gen.status_code, 200)
        tickets = r_gen.json()["tickets"]
        self.assertEqual(len(tickets), 3)

        r_eval = self.client.post(
            "/api/evaluate/ssq",
            json={"numbers": tickets[0]["numbers"], "window": 50},
        )
        self.assertEqual(r_eval.status_code, 200)
        self.assertIn("overall_fit", r_eval.json())

        r_bt = self.client.post(
            "/api/backtest/ssq",
            json={"strategy": "composite", "train_window": 40, "test_periods": 10, "tickets_per_draw": 2},
        )
        self.assertEqual(r_bt.status_code, 200)
        self.assertEqual(r_bt.json()["test_periods"], 10)

    def test_scheduler_auto_sync_and_auto_settle(self) -> None:
        # Record a simulated purchase for issue 2026101 matching the stub draw (jackpot)
        r_buy = self.client.post(
            "/api/purchases/ssq",
            json={
                "items": [
                    {
                        "target_issue": "2026101",
                        "numbers": {"red": [1, 5, 10, 15, 20, 25], "blue": [8]},
                        "bet_count": 1,
                    }
                ]
            },
        )
        self.assertEqual(r_buy.status_code, 200)
        self.assertEqual(r_buy.json()["inserted"], 1)

        # Update scheduler config
        r_cfg = self.client.put(
            "/api/scheduler",
            json={"enabled": True, "interval_minutes": 180, "sync_limit": 20, "auto_settle": True},
        )
        self.assertEqual(r_cfg.status_code, 200)
        self.assertEqual(r_cfg.json()["config"]["interval_minutes"], 180)

        # Trigger scheduler run -> should sync all 3 lotteries & auto-settle the ssq purchase
        r_run = self.client.post("/api/scheduler/run")
        self.assertEqual(r_run.status_code, 200)
        state = r_run.json()
        self.assertEqual(state["last_status"], "success")
        self.assertEqual(len(state["last_results"]), 3)

        # Verify purchase is now settled with top prize
        r_purchases = self.client.get("/api/purchases/ssq")
        self.assertEqual(r_purchases.status_code, 200)
        items = r_purchases.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "settled")
        self.assertEqual(items[0]["prize_amount"], 5_000_000)

    def test_index_html_served(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("彩票选号与历史数据分析助手", resp.text)


if __name__ == "__main__":
    unittest.main()
