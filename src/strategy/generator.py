"""Strategy-based lottery ticket generator with weighted sampling and filters."""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from src.analysis.engine import AnalysisEngine
from src.core.models import DrawRecord, LotteryRule, NumberStat, Ticket, ZoneRule


class StrategyType(str, Enum):
    COMPOSITE = "综合评分（频率+遗漏）"
    BALANCED = "均衡随机（理论回归）"
    HOT = "追热选号（高频优先）"
    COLD_REBOUND = "防冷回补（高遗漏优先）"


@dataclass
class FilterConfig:
    sum_min: Optional[int] = None
    sum_max: Optional[int] = None
    include_numbers: Dict[str, List[int]] = field(default_factory=dict)
    exclude_numbers: Dict[str, List[int]] = field(default_factory=dict)


def _weighted_sample(pool: List[int], weights: List[float], count: int, rng: random.Random) -> List[int]:
    """Sample `count` distinct items from `pool` proportional to `weights`."""
    if count > len(pool):
        raise ValueError(f"可用候选号码数量 ({len(pool)}) 小于所需选号数 ({count})")
    avail_pool = list(pool)
    avail_weights = [max(w, 1e-4) for w in weights]
    picked: List[int] = []
    for _ in range(count):
        total_w = sum(avail_weights)
        r = rng.uniform(0, total_w)
        acc = 0.0
        chosen_idx = len(avail_pool) - 1
        for i, w in enumerate(avail_weights):
            acc += w
            if acc >= r:
                chosen_idx = i
                break
        picked.append(avail_pool.pop(chosen_idx))
        avail_weights.pop(chosen_idx)
    return picked


class TicketGenerator:
    """Generates candidate tickets using statistical weights and optional filters."""

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)
        self._engine = AnalysisEngine()

    @staticmethod
    def _compute_weights(stats: List[NumberStat], strategy: StrategyType) -> Dict[int, float]:
        weights: Dict[int, float] = {}
        for s in stats:
            if strategy == StrategyType.BALANCED:
                # Near-uniform with gentle smoothing
                w = 1.0 + (s.score / 500.0)
            elif strategy == StrategyType.HOT:
                w = 0.2 + (s.empirical_prob * 100.0) ** 1.4
            elif strategy == StrategyType.COLD_REBOUND:
                w = 0.2 + (max(s.desire_index, 0.1) * 5.0) ** 1.3
            else:  # COMPOSITE
                w = 0.5 + (s.score / 20.0) ** 1.3
            weights[s.number] = w
        return weights

    def _pick_zone(
        self,
        zone: ZoneRule,
        stats: List[NumberStat],
        strategy: StrategyType,
        include: List[int],
        exclude: List[int],
    ) -> List[int]:
        weight_map = self._compute_weights(stats, strategy)
        valid_inc = [n for n in include if zone.min_num <= n <= zone.max_num and n not in exclude]
        if len(valid_inc) > zone.count:
            raise ValueError(f"分区 '{zone.label}' 胆码数量 ({len(valid_inc)}) 超过选号数 ({zone.count})")

        remaining_need = zone.count - len(valid_inc)
        candidates = [
            n
            for n in range(zone.min_num, zone.max_num + 1)
            if n not in exclude and n not in valid_inc
        ]
        cand_weights = [weight_map.get(n, 1.0) for n in candidates]
        sampled = _weighted_sample(candidates, cand_weights, remaining_need, self._rng) if remaining_need > 0 else []
        combined = valid_inc + sampled
        return combined if zone.ordered else sorted(combined)

    def generate(
        self,
        rule: LotteryRule,
        draws: List[DrawRecord],
        strategy: StrategyType = StrategyType.COMPOSITE,
        count: int = 5,
        window: Optional[int] = 100,
        filter_cfg: Optional[FilterConfig] = None,
        deterministic: bool = True,
    ) -> List[Ticket]:
        filter_cfg = filter_cfg or FilterConfig()
        ctx = self._engine.build_eval_context(draws, rule, window=window)
        stats_by_zone = ctx["stats_by_zone"]

        if deterministic:
            return self._generate_deterministic_top_n(
                rule=rule,
                ctx=ctx,
                stats_by_zone=stats_by_zone,
                strategy=strategy,
                count=count,
                filter_cfg=filter_cfg,
            )

        tickets: List[Ticket] = []
        seen_keys: set[str] = set()
        max_attempts = max(count * 40, 200)
        attempts = 0

        while len(tickets) < count and attempts < max_attempts:
            attempts += 1
            numbers: Dict[str, List[int]] = {}
            for zone in rule.zones:
                inc = filter_cfg.include_numbers.get(zone.key, [])
                exc = filter_cfg.exclude_numbers.get(zone.key, [])
                numbers[zone.key] = self._pick_zone(
                    zone, stats_by_zone[zone.key], strategy, inc, exc
                )

            # Apply sum filter on primary numbers
            primary_nums, _ = self._engine._extract_primary_numbers(rule, numbers)
            total_sum = sum(primary_nums)
            if filter_cfg.sum_min is not None and total_sum < filter_cfg.sum_min:
                continue
            if filter_cfg.sum_max is not None and total_sum > filter_cfg.sum_max:
                continue

            key = "|".join(",".join(map(str, numbers[z.key])) for z in rule.zones)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            eval_res = self._engine.evaluate_with_context(ctx, rule, numbers)
            feat = eval_res["features"]
            summary = f"和值:{feat['sum']} | 奇偶:{feat['odd_even']} | 大小:{feat['size_ratio']} | 跨度:{feat['span']}"
            tickets.append(
                Ticket(
                    lottery_code=rule.code,
                    numbers=numbers,
                    strategy_name=strategy.value,
                    score=eval_res["overall_fit"],
                    pattern_summary=summary,
                )
            )

        return tickets

    def _generate_deterministic_top_n(
        self,
        rule: LotteryRule,
        ctx: dict,
        stats_by_zone: Dict[str, List[NumberStat]],
        strategy: StrategyType,
        count: int,
        filter_cfg: FilterConfig,
    ) -> List[Ticket]:
        """Deterministically rank top candidate combinations by strategy weight & historical pattern fit."""
        zone_combos: List[List[Tuple[str, List[int]]]] = []
        norm_weight_maps: Dict[str, Dict[int, float]] = {}

        for zone in rule.zones:
            stats = stats_by_zone[zone.key]
            raw_weights = self._compute_weights(stats, strategy)
            max_w = max(raw_weights.values(), default=1.0) or 1.0
            norm_weight_maps[zone.key] = {k: (v / max_w) * 100.0 for k, v in raw_weights.items()}

            inc = [
                n
                for n in filter_cfg.include_numbers.get(zone.key, [])
                if zone.min_num <= n <= zone.max_num
            ]
            exc = set(filter_cfg.exclude_numbers.get(zone.key, []))
            valid_inc = [n for n in inc if n not in exc]
            if len(valid_inc) > zone.count:
                raise ValueError(f"分区 '{zone.label}' 胆码数量 ({len(valid_inc)}) 超过选号数 ({zone.count})")

            remaining_need = zone.count - len(valid_inc)
            candidates = [
                n
                for n in range(zone.min_num, zone.max_num + 1)
                if n not in exc and n not in valid_inc
            ]
            # Sort candidates deterministically by strategy weight DESC, then number ASC
            candidates.sort(key=lambda n: (-raw_weights.get(n, 0.0), n))

            if remaining_need == 0:
                combos = [valid_inc if zone.ordered else sorted(valid_inc)]
            else:
                top_k = remaining_need + (4 if remaining_need >= 4 else 3)
                pool = candidates[: min(len(candidates), top_k)]
                if len(pool) < remaining_need:
                    raise ValueError(f"分区 '{zone.label}' 可用候选号码不足 ({len(pool)} < {remaining_need})")
                combos = [
                    (valid_inc + list(c)) if zone.ordered else sorted(valid_inc + list(c))
                    for c in itertools.combinations(pool, remaining_need)
                ]
            zone_combos.append([(zone.key, c) for c in combos])

        scored_candidates: List[Tuple[float, Ticket]] = []
        for combo_tuple in itertools.product(*zone_combos):
            numbers = {z_key: nums for z_key, nums in combo_tuple}
            primary_nums, _ = self._engine._extract_primary_numbers(rule, numbers)
            total_sum = sum(primary_nums)
            if filter_cfg.sum_min is not None and total_sum < filter_cfg.sum_min:
                continue
            if filter_cfg.sum_max is not None and total_sum > filter_cfg.sum_max:
                continue

            eval_res = self._engine.evaluate_with_context(ctx, rule, numbers)
            # Strategy-specific number score (0-100)
            strat_scores = [
                norm_weight_maps[z.key][num]
                for z in rule.zones
                for num in numbers[z.key]
            ]
            avg_strat_score = sum(strat_scores) / max(len(strat_scores), 1)
            rank_score = round(0.55 * avg_strat_score + 0.45 * eval_res["overall_fit"], 1)

            feat = eval_res["features"]
            summary = f"和值:{feat['sum']} | 奇偶:{feat['odd_even']} | 大小:{feat['size_ratio']} | 跨度:{feat['span']}"
            scored_candidates.append(
                (
                    rank_score,
                    Ticket(
                        lottery_code=rule.code,
                        numbers=numbers,
                        strategy_name=strategy.value,
                        score=rank_score,
                        pattern_summary=summary,
                    ),
                )
            )

        scored_candidates.sort(key=lambda item: (-item[0], tuple(tuple(item[1].numbers[z.key]) for z in rule.zones)))
        return [tk for _, tk in scored_candidates[:count]]
