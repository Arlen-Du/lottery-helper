"""Registry of supported lottery games and their rules."""

from __future__ import annotations

from typing import Dict, List
from src.core.models import LotteryRule, ZoneRule


LOTTERY_RULES: Dict[str, LotteryRule] = {
    "ssq": LotteryRule(
        code="ssq",
        name="双色球",
        description="红球 01-33 选 6 个 + 蓝球 01-16 选 1 个",
        zones=[
            ZoneRule(
                key="red",
                label="红球",
                min_num=1,
                max_num=33,
                count=6,
                ordered=False,
                color="#D93025",
            ),
            ZoneRule(
                key="blue",
                label="蓝球",
                min_num=1,
                max_num=16,
                count=1,
                ordered=False,
                color="#1A73E8",
            ),
        ],
    ),
    "dlt": LotteryRule(
        code="dlt",
        name="大乐透",
        description="前区 01-35 选 5 个 + 后区 01-12 选 2 个",
        zones=[
            ZoneRule(
                key="front",
                label="前区",
                min_num=1,
                max_num=35,
                count=5,
                ordered=False,
                color="#D93025",
            ),
            ZoneRule(
                key="back",
                label="后区",
                min_num=1,
                max_num=12,
                count=2,
                ordered=False,
                color="#1A73E8",
            ),
        ],
    ),
    "fc3d": LotteryRule(
        code="fc3d",
        name="福彩3D",
        description="百位、十位、个位各从 0-9 中选 1 个数字（直选有序）",
        zones=[
            ZoneRule(
                key="hundreds",
                label="百位",
                min_num=0,
                max_num=9,
                count=1,
                ordered=True,
                color="#E37400",
            ),
            ZoneRule(
                key="tens",
                label="十位",
                min_num=0,
                max_num=9,
                count=1,
                ordered=True,
                color="#1E8E3E",
            ),
            ZoneRule(
                key="units",
                label="个位",
                min_num=0,
                max_num=9,
                count=1,
                ordered=True,
                color="#1A73E8",
            ),
        ],
    ),
}


def get_lottery_rule(code: str) -> LotteryRule:
    """Return the LotteryRule for the given code."""
    if code not in LOTTERY_RULES:
        raise KeyError(f"不支持的彩种代码: {code}")
    return LOTTERY_RULES[code]


def list_lottery_rules() -> List[LotteryRule]:
    """Return all registered lottery rules."""
    return list(LOTTERY_RULES.values())
