"""Prize calculation, next-issue inference, and purchase settlement engine."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.core.models import DrawRecord, LotteryRule
from src.data.storage import SQLiteStorage


def infer_next_issue(draws: List[DrawRecord]) -> str:
    """Infer the next expected issue number from the latest draw record."""
    if not draws:
        return "2026001"
    latest = draws[0].issue.strip()
    if latest.isdigit():
        return str(int(latest) + 1).zfill(len(latest))
    return f"{latest}_next"


def evaluate_prize(
    rule: LotteryRule,
    ticket_nums: Dict[str, List[int]],
    draw_nums: Dict[str, List[int]],
    bet_count: int = 1,
) -> Tuple[str, int]:
    """
    Evaluate winning tier and total prize amount (in CNY) for a single ticket.
    Floating top-tier jackpots use standard reference estimates.
    """
    bet_count = max(1, bet_count)

    if rule.code == "ssq":
        r_hit = len(set(ticket_nums.get("red", [])) & set(draw_nums.get("red", [])))
        b_hit = len(set(ticket_nums.get("blue", [])) & set(draw_nums.get("blue", [])))
        if r_hit == 6 and b_hit == 1:
            return "一等奖 (参考)", 5_000_000 * bet_count
        if r_hit == 6 and b_hit == 0:
            return "二等奖 (参考)", 200_000 * bet_count
        if r_hit == 5 and b_hit == 1:
            return "三等奖", 3_000 * bet_count
        if (r_hit == 5 and b_hit == 0) or (r_hit == 4 and b_hit == 1):
            return "四等奖", 200 * bet_count
        if (r_hit == 4 and b_hit == 0) or (r_hit == 3 and b_hit == 1):
            return "五等奖", 10 * bet_count
        if b_hit == 1:
            return "六等奖", 5 * bet_count
        return "未中奖", 0

    if rule.code == "dlt":
        f_hit = len(set(ticket_nums.get("front", [])) & set(draw_nums.get("front", [])))
        b_hit = len(set(ticket_nums.get("back", [])) & set(draw_nums.get("back", [])))
        if f_hit == 5 and b_hit == 2:
            return "一等奖 (参考)", 5_000_000 * bet_count
        if f_hit == 5 and b_hit == 1:
            return "二等奖 (参考)", 150_000 * bet_count
        if f_hit == 5 and b_hit == 0:
            return "三等奖", 10_000 * bet_count
        if f_hit == 4 and b_hit == 2:
            return "四等奖", 3_000 * bet_count
        if f_hit == 4 and b_hit == 1:
            return "五等奖", 300 * bet_count
        if f_hit == 3 and b_hit == 2:
            return "六等奖", 200 * bet_count
        if f_hit == 4 and b_hit == 0:
            return "七等奖", 100 * bet_count
        if (f_hit == 3 and b_hit == 1) or (f_hit == 2 and b_hit == 2):
            return "八等奖", 15 * bet_count
        if (
            (f_hit == 3 and b_hit == 0)
            or (f_hit == 1 and b_hit == 2)
            or (f_hit == 2 and b_hit == 1)
            or (f_hit == 0 and b_hit == 2)
        ):
            return "九等奖", 5 * bet_count
        return "未中奖", 0

    if rule.code == "fc3d":
        exact = all(
            ticket_nums.get(k, [None])[0] == draw_nums.get(k, [None])[0]
            for k in ("hundreds", "tens", "units")
        )
        if exact:
            return "直选一等奖", 1_040 * bet_count
        return "未中奖", 0

    return "未中奖", 0


def settle_pending_purchases(
    storage: SQLiteStorage,
    rule: LotteryRule,
    draws: List[DrawRecord],
) -> Tuple[int, int]:
    """
    Settle any pending purchases for `rule.code` whose `target_issue` is present in `draws`.
    Returns (settled_count, total_prize_won).
    """
    draw_map = {d.issue: d for d in draws if d.lottery_code == rule.code}
    purchases = storage.get_purchases(rule.code)
    settled_count = 0
    total_prize = 0

    for p in purchases:
        if p.status != "pending" or p.id is None:
            continue
        draw = draw_map.get(p.target_issue)
        if draw is None:
            continue
        tier, amount = evaluate_prize(rule, p.numbers, draw.numbers, bet_count=p.bet_count)
        storage.update_purchase_settlement(
            purchase_id=p.id,
            prize_tier=tier,
            prize_amount=amount,
            draw_numbers=draw.numbers,
        )
        settled_count += 1
        total_prize += amount

    return settled_count, total_prize
