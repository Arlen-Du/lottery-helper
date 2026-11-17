"""Walk-forward historical backtesting engine for ticket generation strategies."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from src.core.models import DrawRecord, LotteryRule
from src.strategy.generator import StrategyType, TicketGenerator


class BacktestEngine:
    """Evaluates a strategy via walk-forward simulation on historical draws."""

    def run(
        self,
        rule: LotteryRule,
        draws: List[DrawRecord],
        strategy: StrategyType,
        train_window: int = 50,
        test_periods: int = 20,
        tickets_per_draw: int = 5,
    ) -> Dict[str, Any]:
        """
        `draws` must be ordered newest-first.
        For each test index t in [0, test_periods - 1]:
        train on draws[t+1 : t+1+train_window], generate tickets, compare against draws[t].
        """
        available_tests = len(draws) - train_window
        if available_tests <= 0:
            raise ValueError(
                f"历史数据不足以回测：当前共 {len(draws)} 期，至少需要 {train_window + 1} 期数据。"
            )

        actual_tests = min(test_periods, available_tests)
        generator = TicketGenerator(seed=42)
        hit_counter: Counter[str] = Counter()
        total_primary_hits = 0
        total_tickets = 0
        best_highlights: List[str] = []

        for t in range(actual_tests - 1, -1, -1):
            target = draws[t]
            train_slice = draws[t + 1 : t + 1 + train_window]
            tickets = generator.generate(
                rule=rule,
                draws=train_slice,
                strategy=strategy,
                count=tickets_per_draw,
                window=train_window,
            )

            for tk in tickets:
                total_tickets += 1
                label, primary_hit = self._score_hit(rule, tk.numbers, target.numbers)
                hit_counter[label] += 1
                total_primary_hits += primary_hit
                if primary_hit >= (3 if rule.code in {"ssq", "dlt"} else 2):
                    best_highlights.append(
                        f"第 {target.issue} 期 [{label}]: 推荐 {tk.format_display(rule)} vs 开奖 {target.format_display(rule)}"
                    )

        return {
            "lottery_name": rule.name,
            "strategy_name": strategy.value,
            "test_periods": actual_tests,
            "tickets_per_draw": tickets_per_draw,
            "total_tickets": total_tickets,
            "avg_primary_hits": round(total_primary_hits / max(total_tickets, 1), 3),
            "hit_distribution": dict(hit_counter.most_common()),
            "highlights": best_highlights[-15:],
        }

    @staticmethod
    def _score_hit(
        rule: LotteryRule,
        pred: Dict[str, List[int]],
        actual: Dict[str, List[int]],
    ) -> tuple[str, int]:
        if rule.code == "ssq":
            r_hit = len(set(pred["red"]) & set(actual["red"]))
            b_hit = len(set(pred["blue"]) & set(actual["blue"]))
            return f"红{r_hit}+蓝{b_hit}", r_hit
        if rule.code == "dlt":
            f_hit = len(set(pred["front"]) & set(actual["front"]))
            b_hit = len(set(pred["back"]) & set(actual["back"]))
            return f"前{f_hit}+后{b_hit}", f_hit
        # fc3d
        exact_hits = sum(
            1 for k in ("hundreds", "tens", "units") if pred[k][0] == actual[k][0]
        )
        if exact_hits == 3:
            return "直选全中 (3位)", 3
        return f"定位命中 {exact_hits} 位", exact_hits
