"""Core data models for lottery rules, draw records, analysis stats, and tickets."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ZoneRule(BaseModel):
    """Defines a single number zone within a lottery game (e.g., red balls, front zone, hundreds digit)."""

    key: str = Field(..., description="Internal key, e.g., 'red', 'blue', 'front', 'back', 'hundreds'")
    label: str = Field(..., description="Display name, e.g., '红球', '蓝球'")
    min_num: int = Field(..., description="Minimum valid ball number (inclusive)")
    max_num: int = Field(..., description="Maximum valid ball number (inclusive)")
    count: int = Field(..., description="Number of balls drawn in this zone")
    ordered: bool = Field(default=False, description="Whether order matters (True for 3D digits)")
    color: str = Field(default="#E53935", description="UI badge color hex")

    @property
    def pool_size(self) -> int:
        return self.max_num - self.min_num + 1

    @property
    def theory_prob(self) -> float:
        """Theoretical probability of a single number appearing in one draw in this zone."""
        return self.count / self.pool_size

    @property
    def theory_omission(self) -> float:
        """Theoretical average omission interval between appearances."""
        p = self.theory_prob
        return (1.0 - p) / p if p > 0 else 0.0

    def format_num(self, num: int) -> str:
        """Format number with leading zero if pool max >= 10."""
        return f"{num:02d}" if self.max_num >= 10 else str(num)


class LotteryRule(BaseModel):
    """Defines a complete lottery game rule."""

    code: str = Field(..., description="Unique lottery code, e.g., 'ssq', 'dlt', 'fc3d'")
    name: str = Field(..., description="Display name, e.g., '双色球'")
    zones: List[ZoneRule] = Field(..., description="Ordered list of zones")
    description: str = Field(default="", description="Short rule summary")

    def get_zone(self, key: str) -> ZoneRule:
        for z in self.zones:
            if z.key == key:
                return z
        raise KeyError(f"Zone '{key}' not found in lottery '{self.code}'")

    def validate_numbers(self, numbers: Dict[str, List[int]]) -> None:
        """Validate a ticket or draw record against this lottery's zone rules."""
        for zone in self.zones:
            if zone.key not in numbers:
                raise ValueError(f"缺少分区 '{zone.label}' ({zone.key})")
            vals = numbers[zone.key]
            if len(vals) != zone.count:
                raise ValueError(f"分区 '{zone.label}' 需要 {zone.count} 个号码，当前为 {len(vals)} 个")
            if not zone.ordered and len(set(vals)) != len(vals):
                raise ValueError(f"分区 '{zone.label}' 不允许重复号码")
            for v in vals:
                if v < zone.min_num or v > zone.max_num:
                    raise ValueError(
                        f"分区 '{zone.label}' 号码 {v} 超出范围 [{zone.min_num}, {zone.max_num}]"
                    )


class DrawRecord(BaseModel):
    """Represents a single historical lottery draw."""

    lottery_code: str
    issue: str = Field(..., description="Issue number, e.g., '2026120'")
    draw_date: str = Field(default="", description="Draw date in YYYY-MM-DD")
    numbers: Dict[str, List[int]] = Field(..., description="Mapping from zone key to drawn numbers")
    extra: Dict[str, Any] = Field(default_factory=dict)

    def format_display(self, rule: LotteryRule) -> str:
        parts = []
        for z in rule.zones:
            nums = self.numbers.get(z.key, [])
            parts.append(" ".join(z.format_num(n) for n in nums))
        return " | ".join(parts)


class NumberStat(BaseModel):
    """Statistical profile for a single number in a zone."""

    zone_key: str
    number: int
    frequency: int
    total_draws: int
    empirical_prob: float
    theory_prob: float
    current_omission: int
    avg_omission: float
    max_omission: int
    desire_index: float = Field(..., description="current_omission / max(avg_omission, 1)")
    score: float = Field(..., description="Normalized composite score (0-100)")


class Ticket(BaseModel):
    """A generated or user-selected lottery ticket with evaluation metadata."""

    lottery_code: str
    numbers: Dict[str, List[int]]
    strategy_name: str = ""
    score: float = 0.0
    pattern_summary: str = ""
    notes: Optional[str] = None

    def format_display(self, rule: LotteryRule) -> str:
        parts = []
        for z in rule.zones:
            nums = self.numbers.get(z.key, [])
            parts.append(" ".join(z.format_num(n) for n in nums))
        return " + ".join(parts)
