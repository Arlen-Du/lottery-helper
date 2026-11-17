"""Statistical analysis and empirical probability evaluation engine."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from src.core.models import DrawRecord, LotteryRule, NumberStat, ZoneRule


class AnalysisEngine:
    """Computes frequency, omission, pattern distributions, and ticket evaluations."""

    @staticmethod
    def _slice_window(draws: List[DrawRecord], window: Optional[int]) -> List[DrawRecord]:
        if window is not None and window > 0:
            return draws[:window]
        return draws

    def analyze_zone(
        self,
        draws: List[DrawRecord],
        zone: ZoneRule,
        window: Optional[int] = 100,
    ) -> List[NumberStat]:
        """Compute per-number frequency, omission, desire index, and score for a zone."""
        sample = self._slice_window(draws, window)
        total = len(sample)
        pool = range(zone.min_num, zone.max_num + 1)

        if total == 0:
            return [
                NumberStat(
                    zone_key=zone.key,
                    number=num,
                    frequency=0,
                    total_draws=0,
                    empirical_prob=0.0,
                    theory_prob=round(zone.theory_prob, 4),
                    current_omission=0,
                    avg_omission=round(zone.theory_omission, 2),
                    max_omission=0,
                    desire_index=0.0,
                    score=50.0,
                )
                for num in pool
            ]

        raw_stats: List[Dict[str, Any]] = []
        for num in pool:
            freq = 0
            curr_omission: Optional[int] = None
            max_omission = 0
            streak = 0

            for idx, d in enumerate(sample):
                nums = d.numbers.get(zone.key, [])
                if num in nums:
                    freq += 1
                    if curr_omission is None:
                        curr_omission = idx
                    if streak > max_omission:
                        max_omission = streak
                    streak = 0
                else:
                    streak += 1

            if curr_omission is None:
                curr_omission = total
            if streak > max_omission:
                max_omission = streak

            avg_omission = (total - freq) / (freq + 1)
            empirical_prob = freq / total
            desire = curr_omission / max(avg_omission, 1.0)
            raw_stats.append(
                {
                    "number": num,
                    "frequency": freq,
                    "empirical_prob": empirical_prob,
                    "current_omission": curr_omission,
                    "avg_omission": avg_omission,
                    "max_omission": max_omission,
                    "desire_index": desire,
                }
            )

        max_freq = max((s["frequency"] for s in raw_stats), default=1) or 1
        max_desire = max((s["desire_index"] for s in raw_stats), default=1.0) or 1.0

        results: List[NumberStat] = []
        for s in raw_stats:
            freq_norm = s["frequency"] / max_freq
            desire_norm = min(s["desire_index"] / max_desire, 1.0)
            # Balanced composite score: 50% empirical frequency + 50% omission rebound
            score = round((0.5 * freq_norm + 0.5 * desire_norm) * 100.0, 1)
            results.append(
                NumberStat(
                    zone_key=zone.key,
                    number=s["number"],
                    frequency=s["frequency"],
                    total_draws=total,
                    empirical_prob=round(s["empirical_prob"], 4),
                    theory_prob=round(zone.theory_prob, 4),
                    current_omission=s["current_omission"],
                    avg_omission=round(s["avg_omission"], 2),
                    max_omission=s["max_omission"],
                    desire_index=round(s["desire_index"], 2),
                    score=score,
                )
            )
        return results

    def analyze_all_zones(
        self,
        draws: List[DrawRecord],
        rule: LotteryRule,
        window: Optional[int] = 100,
    ) -> Dict[str, List[NumberStat]]:
        return {zone.key: self.analyze_zone(draws, zone, window=window) for zone in rule.zones}

    @staticmethod
    def _extract_primary_numbers(rule: LotteryRule, numbers: Dict[str, List[int]]) -> Tuple[List[int], int]:
        """Return (primary_nums, midpoint_for_size_split)."""
        if rule.code == "fc3d":
            vals = [
                numbers["hundreds"][0],
                numbers["tens"][0],
                numbers["units"][0],
            ]
            return vals, 5  # 0-4 small, 5-9 big
        primary_zone = rule.zones[0]
        midpoint = (primary_zone.min_num + primary_zone.max_num) // 2 + 1
        return list(numbers.get(primary_zone.key, [])), midpoint

    @staticmethod
    def _compute_pattern_features(nums: List[int], midpoint: int) -> Dict[str, Any]:
        total_sum = sum(nums)
        odd_cnt = sum(1 for n in nums if n % 2 == 1)
        even_cnt = len(nums) - odd_cnt
        big_cnt = sum(1 for n in nums if n >= midpoint)
        small_cnt = len(nums) - big_cnt
        span = max(nums) - min(nums) if nums else 0
        sorted_nums = sorted(nums)
        consec = sum(1 for i in range(len(sorted_nums) - 1) if sorted_nums[i + 1] - sorted_nums[i] == 1)
        return {
            "sum": total_sum,
            "odd_even": f"{odd_cnt}:{even_cnt}",
            "size_ratio": f"{big_cnt}:{small_cnt}",
            "span": span,
            "consecutive": consec,
        }

    def analyze_patterns(
        self,
        draws: List[DrawRecord],
        rule: LotteryRule,
        window: Optional[int] = 100,
    ) -> Dict[str, Any]:
        """Compute historical pattern distributions for the primary zone."""
        sample = self._slice_window(draws, window)
        if not sample:
            return {
                "total": 0,
                "sum_avg": 0.0,
                "sum_min": 0,
                "sum_max": 0,
                "sum_p25": 0,
                "sum_p75": 0,
                "odd_even_top": [],
                "size_ratio_top": [],
                "span_avg": 0.0,
                "consecutive_dist": {},
            }

        sums: List[int] = []
        spans: List[int] = []
        odd_even_counter: Counter[str] = Counter()
        size_counter: Counter[str] = Counter()
        consec_counter: Counter[int] = Counter()

        for d in sample:
            nums, midpoint = self._extract_primary_numbers(rule, d.numbers)
            feat = self._compute_pattern_features(nums, midpoint)
            sums.append(feat["sum"])
            spans.append(feat["span"])
            odd_even_counter[feat["odd_even"]] += 1
            size_counter[feat["size_ratio"]] += 1
            consec_counter[feat["consecutive"]] += 1

        sums_sorted = sorted(sums)
        n = len(sums_sorted)
        p25 = sums_sorted[max(0, int(n * 0.25) - 1)]
        p75 = sums_sorted[min(n - 1, int(n * 0.75))]

        return {
            "total": n,
            "sum_avg": round(sum(sums) / n, 1),
            "sum_min": sums_sorted[0],
            "sum_max": sums_sorted[-1],
            "sum_p25": p25,
            "sum_p75": p75,
            "odd_even_top": [(k, v, round(v / n * 100, 1)) for k, v in odd_even_counter.most_common(4)],
            "size_ratio_top": [(k, v, round(v / n * 100, 1)) for k, v in size_counter.most_common(4)],
            "span_avg": round(sum(spans) / n, 1),
            "consecutive_dist": {k: round(v / n * 100, 1) for k, v in sorted(consec_counter.items())},
        }

    def build_eval_context(
        self,
        draws: List[DrawRecord],
        rule: LotteryRule,
        window: Optional[int] = 100,
    ) -> Dict[str, Any]:
        """Precompute statistics and pattern counters for fast batch ticket evaluation."""
        sample = self._slice_window(draws, window)
        stats_by_zone = self.analyze_all_zones(sample, rule, window=None)
        stat_maps = {
            z_key: {s.number: s for s in stat_list}
            for z_key, stat_list in stats_by_zone.items()
        }
        oe_counter: Counter[str] = Counter()
        sz_counter: Counter[str] = Counter()
        sums: List[int] = []
        for d in sample:
            d_nums, d_mid = self._extract_primary_numbers(rule, d.numbers)
            d_feat = self._compute_pattern_features(d_nums, d_mid)
            oe_counter[d_feat["odd_even"]] += 1
            sz_counter[d_feat["size_ratio"]] += 1
            sums.append(d_feat["sum"])

        sums_sorted = sorted(sums)
        n = len(sums_sorted)
        p25 = sums_sorted[max(0, int(n * 0.25) - 1)] if n else 0
        p75 = sums_sorted[min(n - 1, int(n * 0.75))] if n else 0

        return {
            "total": n,
            "stats_by_zone": stats_by_zone,
            "stat_maps": stat_maps,
            "oe_counter": oe_counter,
            "sz_counter": sz_counter,
            "sums": sums_sorted,
            "sum_p25": p25,
            "sum_p75": p75,
        }

    def evaluate_with_context(
        self,
        ctx: Dict[str, Any],
        rule: LotteryRule,
        numbers: Dict[str, List[int]],
    ) -> Dict[str, Any]:
        """Evaluate a candidate ticket using a precomputed evaluation context."""
        rule.validate_numbers(numbers)
        stat_maps = ctx["stat_maps"]
        num_scores: List[float] = []
        for zone in rule.zones:
            s_map = stat_maps.get(zone.key, {})
            for num in numbers[zone.key]:
                if num in s_map:
                    num_scores.append(s_map[num].score)
        avg_num_score = round(sum(num_scores) / max(len(num_scores), 1), 1)

        nums, midpoint = self._extract_primary_numbers(rule, numbers)
        feat = self._compute_pattern_features(nums, midpoint)
        total = ctx["total"]

        if total == 0:
            return {
                "features": feat,
                "avg_num_score": avg_num_score,
                "sum_band_rate": 0.0,
                "odd_even_rate": 0.0,
                "size_rate": 0.0,
                "overall_fit": avg_num_score,
                "diagnostics": ["暂无历史数据，仅完成号码规则校验。"],
            }

        oe_exact = ctx["oe_counter"].get(feat["odd_even"], 0)
        sz_exact = ctx["sz_counter"].get(feat["size_ratio"], 0)
        sum_tol = 3 if rule.code == "fc3d" else 12
        target_sum = feat["sum"]
        sum_near = sum(1 for s in ctx["sums"] if abs(s - target_sum) <= sum_tol)

        oe_rate = round(oe_exact / total * 100, 1)
        sz_rate = round(sz_exact / total * 100, 1)
        sum_rate = round(sum_near / total * 100, 1)

        pattern_score = min(100.0, (oe_rate * 1.4 + sz_rate * 1.4 + sum_rate * 0.8) / 1.5)
        overall_fit = round(0.45 * avg_num_score + 0.55 * pattern_score, 1)

        diagnostics: List[str] = []
        p25, p75 = ctx["sum_p25"], ctx["sum_p75"]
        if p25 <= feat["sum"] <= p75:
            diagnostics.append(f"和值 {feat['sum']} 处于历史核心高频区间 [{p25}, {p75}]。")
        else:
            diagnostics.append(f"和值 {feat['sum']} 偏离历史核心区间 [{p25}, {p75}]，属于相对偏态组合。")

        if oe_rate >= 15.0:
            diagnostics.append(f"奇偶比 {feat['odd_even']} 在近 {total} 期中出现 {oe_rate}%，形态常见。")
        else:
            diagnostics.append(f"奇偶比 {feat['odd_even']} 在近 {total} 期中仅出现 {oe_rate}%，形态偏冷。")

        if sz_rate >= 15.0:
            diagnostics.append(f"大小比 {feat['size_ratio']} 在近 {total} 期中出现 {sz_rate}%，分布均衡。")
        else:
            diagnostics.append(f"大小比 {feat['size_ratio']} 在近 {total} 期中出现 {sz_rate}%，分布偏冷。")

        return {
            "features": feat,
            "avg_num_score": avg_num_score,
            "sum_band_rate": sum_rate,
            "odd_even_rate": oe_rate,
            "size_rate": sz_rate,
            "overall_fit": overall_fit,
            "diagnostics": diagnostics,
        }

    def evaluate_ticket(
        self,
        draws: List[DrawRecord],
        rule: LotteryRule,
        numbers: Dict[str, List[int]],
        window: Optional[int] = 100,
    ) -> Dict[str, Any]:
        """Evaluate a candidate ticket against historical pattern & number statistics."""
        ctx = self.build_eval_context(draws, rule, window=window)
        return self.evaluate_with_context(ctx, rule, numbers)
