"""Unit tests for lottery rules, storage, adapters, analysis, generation, backtest, and PyQt6 UI."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.analysis.engine import AnalysisEngine
from src.core.registry import get_lottery_rule
from src.data.adapters import CsvExcelAdapter, MockSampleAdapter
from src.data.storage import SQLiteStorage
from src.strategy.backtest import BacktestEngine
from src.strategy.generator import FilterConfig, StrategyType, TicketGenerator


class LotteryHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AnalysisEngine()
        self.generator = TicketGenerator(seed=123)
        self.backtest = BacktestEngine()

    def test_all_lotteries_mock_analysis_and_generation(self) -> None:
        mock = MockSampleAdapter(seed=42)
        for code in ("ssq", "dlt", "fc3d"):
            rule = get_lottery_rule(code)
            draws = mock.fetch(code, limit=80)
            self.assertEqual(len(draws), 80)

            stats = self.engine.analyze_all_zones(draws, rule, window=80)
            for z in rule.zones:
                self.assertEqual(len(stats[z.key]), z.pool_size)

            patterns = self.engine.analyze_patterns(draws, rule, window=80)
            self.assertEqual(patterns["total"], 80)

            tickets = self.generator.generate(
                rule=rule,
                draws=draws,
                strategy=StrategyType.COMPOSITE,
                count=5,
                window=80,
                deterministic=True,
            )
            tickets_repeat = self.generator.generate(
                rule=rule,
                draws=draws,
                strategy=StrategyType.COMPOSITE,
                count=5,
                window=80,
                deterministic=True,
            )
            self.assertEqual(len(tickets), 5)
            self.assertEqual([t.numbers for t in tickets], [t.numbers for t in tickets_repeat])
            self.assertEqual(
                [t.score for t in tickets],
                sorted([t.score for t in tickets], reverse=True),
            )
            for tk in tickets:
                rule.validate_numbers(tk.numbers)

    def test_sqlite_storage_and_csv_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            storage = SQLiteStorage(db_path)

            csv_path = Path(tmpdir) / "ssq.csv"
            df = pd.DataFrame(
                [
                    {"期号": "2026001", "开奖日期": "2026-01-01", "红球": "01,05,12,18,24,33", "蓝球": "08"},
                    {"期号": "2026002", "开奖日期": "2026-01-04", "红球": "02,09,14,20,27,31", "蓝球": "12"},
                ]
            )
            df.to_csv(csv_path, index=False)

            records = CsvExcelAdapter(csv_path).fetch("ssq")
            self.assertEqual(len(records), 2)
            storage.save_draws(records)
            self.assertEqual(storage.count_draws("ssq"), 2)

            loaded = storage.get_draws("ssq")
            self.assertEqual(loaded[0].issue, "2026002")
            self.assertEqual(storage.clear_draws("ssq"), 2)
            self.assertEqual(storage.count_draws("ssq"), 0)

    def test_filter_and_backtest(self) -> None:
        rule = get_lottery_rule("ssq")
        draws = MockSampleAdapter(seed=7).fetch("ssq", limit=75)
        tickets = self.generator.generate(
            rule=rule,
            draws=draws,
            strategy=StrategyType.HOT,
            count=3,
            filter_cfg=FilterConfig(sum_min=70, sum_max=130),
        )
        self.assertEqual(len(tickets), 3)
        for tk in tickets:
            s = sum(tk.numbers["red"])
            self.assertGreaterEqual(s, 70)
            self.assertLessEqual(s, 130)

        bt = self.backtest.run(
            rule=rule,
            draws=draws,
            strategy=StrategyType.BALANCED,
            train_window=50,
            test_periods=10,
            tickets_per_draw=3,
        )
        self.assertEqual(bt["test_periods"], 10)
        self.assertEqual(bt["total_tickets"], 30)

    def test_pyqt6_window_instantiation(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        from src.ui.main_window import LotteryMainWindow

        app = QApplication.instance() or QApplication([])
        win = LotteryMainWindow()
        self.assertEqual(win.tabs.count(), 4)

        rule = get_lottery_rule("ssq")
        draws = MockSampleAdapter(seed=1).fetch("ssq", limit=30)
        tickets = self.generator.generate(rule=rule, draws=draws, count=2, deterministic=True)
        text = LotteryMainWindow.format_tickets_text(rule, tickets)
        self.assertIn("红球:", text)
        self.assertIn("蓝球:", text)
        self.assertNotIn("评分", text)
        self.assertNotIn("和值", text)

        img = LotteryMainWindow.render_tickets_image(rule, tickets)
        self.assertFalse(img.isNull())
        self.assertGreater(img.width(), 0)
        self.assertGreater(img.height(), 0)

        win.close()
        _ = app


if __name__ == "__main__":
    unittest.main()
