"""FastAPI web application exposing Lottery Helper for fnOS native NAS deployment."""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.analysis.engine import AnalysisEngine
from src.core.models import PurchaseRecord
from src.core.registry import get_lottery_rule, list_lottery_rules
from src.data.adapters import AdapterManager, DataAdapterError
from src.data.storage import SQLiteStorage
from src.strategy.backtest import BacktestEngine
from src.strategy.generator import FilterConfig, StrategyType, TicketGenerator
from src.strategy.prize import infer_next_issue, settle_pending_purchases
from src.web.scheduler import AutoSyncScheduler


def _resolve_static_dir() -> Path:
    """Resolve static web assets directory both in source tree and PyInstaller bundle."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "src" / "web" / "static"
        if bundled.exists():
            return bundled
    return Path(__file__).resolve().parent / "static"


STRATEGY_MAP: Dict[str, StrategyType] = {
    "composite": StrategyType.COMPOSITE,
    "balanced": StrategyType.BALANCED,
    "hot": StrategyType.HOT,
    "cold_rebound": StrategyType.COLD_REBOUND,
    StrategyType.COMPOSITE.value: StrategyType.COMPOSITE,
    StrategyType.BALANCED.value: StrategyType.BALANCED,
    StrategyType.HOT.value: StrategyType.HOT,
    StrategyType.COLD_REBOUND.value: StrategyType.COLD_REBOUND,
}


class SyncRequest(BaseModel):
    limit: int = Field(default=100, ge=10, le=500)


class MockRequest(BaseModel):
    limit: int = Field(default=120, ge=20, le=500)


class GenerateRequest(BaseModel):
    strategy: str = Field(default="composite")
    count: int = Field(default=5, ge=1, le=20)
    window: Optional[int] = Field(default=100)
    deterministic: bool = Field(default=True)
    sum_min: Optional[int] = Field(default=None)
    sum_max: Optional[int] = Field(default=None)
    include_numbers: Dict[str, List[int]] = Field(default_factory=dict)
    exclude_numbers: Dict[str, List[int]] = Field(default_factory=dict)


class EvaluateRequest(BaseModel):
    numbers: Dict[str, List[int]]
    window: Optional[int] = Field(default=100)


class PurchaseItemRequest(BaseModel):
    target_issue: str
    numbers: Dict[str, List[int]]
    bet_count: int = Field(default=1, ge=1, le=99)


class CreatePurchasesRequest(BaseModel):
    items: List[PurchaseItemRequest] = Field(..., min_length=1)


class DeletePurchasesRequest(BaseModel):
    ids: List[int] = Field(..., min_length=1)


class BacktestRequest(BaseModel):
    strategy: str = Field(default="composite")
    train_window: int = Field(default=50, ge=10, le=300)
    test_periods: int = Field(default=20, ge=5, le=100)
    tickets_per_draw: int = Field(default=5, ge=1, le=20)


class SchedulerConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = Field(default=None, ge=15, le=10080)
    sync_limit: Optional[int] = Field(default=None, ge=10, le=300)
    auto_settle: Optional[bool] = None
    lotteries: Optional[List[str]] = None


def _get_rule_or_404(code: str):
    try:
        return get_lottery_rule(code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _resolve_strategy(raw: str) -> StrategyType:
    if raw not in STRATEGY_MAP:
        raise HTTPException(status_code=400, detail=f"不支持的策略: {raw}")
    return STRATEGY_MAP[raw]


def create_app(
    db_path: Optional[str | Path] = None,
    adapter_manager: Optional[AdapterManager] = None,
    start_scheduler: bool = True,
) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    storage = SQLiteStorage(db_path=db_path)
    adapters = adapter_manager or AdapterManager()
    engine = AnalysisEngine()
    backtest_engine = BacktestEngine()
    scheduler = AutoSyncScheduler(storage=storage, adapter_manager=adapters)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_scheduler:
            scheduler.start()
        try:
            yield
        finally:
            if start_scheduler:
                scheduler.stop()

    app = FastAPI(
        title="彩票选号与历史数据分析助手",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.storage = storage
    app.state.scheduler = scheduler

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "db_path": str(storage.db_path),
            "scheduler_enabled": scheduler.get_config()["enabled"],
        }

    @app.get("/api/lotteries")
    def get_lotteries() -> List[Dict[str, Any]]:
        result = []
        for rule in list_lottery_rules():
            draws = storage.get_draws(rule.code, limit=1)
            result.append(
                {
                    **rule.model_dump(),
                    "draw_count": storage.count_draws(rule.code),
                    "latest_issue": draws[0].issue if draws else "",
                    "latest_date": draws[0].draw_date if draws else "",
                    "next_issue": infer_next_issue(draws),
                }
            )
        return result

    @app.get("/api/draws/{code}")
    def list_draws(code: str, limit: int = Query(default=100, ge=1, le=2000)) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        draws = storage.get_draws(code, limit=limit)
        total = storage.count_draws(code)
        return {
            "lottery_code": code,
            "total": total,
            "next_issue": infer_next_issue(draws),
            "items": [
                {
                    **d.model_dump(),
                    "display": d.format_display(rule),
                }
                for d in draws
            ],
        }

    @app.post("/api/draws/{code}/sync")
    def sync_draws(code: str, req: SyncRequest) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        try:
            records = adapters.fetch_online(code, limit=req.limit)
        except DataAdapterError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        storage.save_draws(records)
        all_draws = storage.get_draws(code)
        settled, prize = settle_pending_purchases(storage, rule, all_draws)
        return {
            "fetched": len(records),
            "total": len(all_draws),
            "settled_count": settled,
            "settled_prize": prize,
            "latest_issue": all_draws[0].issue if all_draws else "",
        }

    @app.post("/api/draws/{code}/mock")
    def mock_draws(code: str, req: MockRequest) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        records = adapters.generate_mock(code, limit=req.limit)
        storage.save_draws(records)
        all_draws = storage.get_draws(code)
        settled, prize = settle_pending_purchases(storage, rule, all_draws)
        return {
            "generated": len(records),
            "total": len(all_draws),
            "settled_count": settled,
            "settled_prize": prize,
        }

    @app.post("/api/draws/{code}/import")
    async def import_draws(code: str, file: UploadFile = File(...)) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        suffix = Path(file.filename or "upload.csv").suffix.lower()
        if suffix not in {".csv", ".xlsx", ".xls"}:
            raise HTTPException(status_code=400, detail="仅支持导入 .csv 或 .xlsx/.xls 文件")
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            records = adapters.import_file(code, tmp_path)
        except DataAdapterError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            tmp_path.unlink(missing_ok=True)
        storage.save_draws(records)
        all_draws = storage.get_draws(code)
        settled, prize = settle_pending_purchases(storage, rule, all_draws)
        return {
            "imported": len(records),
            "total": len(all_draws),
            "settled_count": settled,
            "settled_prize": prize,
        }

    @app.delete("/api/draws/{code}")
    def clear_draws(code: str) -> Dict[str, Any]:
        _get_rule_or_404(code)
        deleted = storage.clear_draws(code)
        return {"deleted": deleted}

    @app.get("/api/analysis/{code}")
    def analyze(code: str, window: Optional[int] = Query(default=100)) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        actual_window = None if (window is None or window <= 0) else window
        draws = storage.get_draws(code)
        stats_by_zone = engine.analyze_all_zones(draws, rule, window=actual_window)
        patterns = engine.analyze_patterns(draws, rule, window=actual_window)
        return {
            "lottery_code": code,
            "total_stored": len(draws),
            "window": actual_window,
            "zones": {
                z_key: [s.model_dump() for s in stat_list]
                for z_key, stat_list in stats_by_zone.items()
            },
            "patterns": patterns,
        }

    @app.post("/api/generate/{code}")
    def generate_tickets(code: str, req: GenerateRequest) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        strategy = _resolve_strategy(req.strategy)
        draws = storage.get_draws(code)
        actual_window = None if (req.window is None or req.window <= 0) else req.window
        filter_cfg = FilterConfig(
            sum_min=req.sum_min,
            sum_max=req.sum_max,
            include_numbers=req.include_numbers,
            exclude_numbers=req.exclude_numbers,
        )
        generator = TicketGenerator()
        try:
            tickets = generator.generate(
                rule=rule,
                draws=draws,
                strategy=strategy,
                count=req.count,
                window=actual_window,
                filter_cfg=filter_cfg,
                deterministic=req.deterministic,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "lottery_code": code,
            "strategy": strategy.value,
            "deterministic": req.deterministic,
            "next_issue": infer_next_issue(draws),
            "tickets": [
                {
                    **t.model_dump(),
                    "display": t.format_display(rule),
                }
                for t in tickets
            ],
        }

    @app.post("/api/evaluate/{code}")
    def evaluate_ticket(code: str, req: EvaluateRequest) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        draws = storage.get_draws(code)
        actual_window = None if (req.window is None or req.window <= 0) else req.window
        try:
            return engine.evaluate_ticket(draws, rule, req.numbers, window=actual_window)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/purchases/{code}")
    def list_purchases(code: str) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        purchases = storage.get_purchases(code)
        draws = storage.get_draws(code, limit=1)
        total_cost = sum(p.cost for p in purchases)
        total_prize = sum(p.prize_amount for p in purchases)
        pending_count = sum(1 for p in purchases if p.status == "pending")
        return {
            "lottery_code": code,
            "next_issue": infer_next_issue(draws),
            "summary": {
                "total_records": len(purchases),
                "pending_count": pending_count,
                "total_cost": total_cost,
                "total_prize": total_prize,
                "net_profit": total_prize - total_cost,
            },
            "items": [
                {
                    **p.model_dump(),
                    "display": p.format_display(rule),
                }
                for p in purchases
            ],
        }

    @app.post("/api/purchases/{code}")
    def create_purchases(code: str, req: CreatePurchasesRequest) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        records: List[PurchaseRecord] = []
        for item in req.items:
            try:
                rule.validate_numbers(item.numbers)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            records.append(
                PurchaseRecord(
                    lottery_code=code,
                    target_issue=item.target_issue.strip(),
                    numbers=item.numbers,
                    bet_count=item.bet_count,
                    cost=2 * item.bet_count,
                )
            )
        inserted = storage.add_purchases(records)
        all_draws = storage.get_draws(code)
        settled, prize = settle_pending_purchases(storage, rule, all_draws)
        return {
            "inserted": inserted,
            "settled_count": settled,
            "settled_prize": prize,
        }

    @app.post("/api/purchases/{code}/settle")
    def settle_purchases(code: str) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        all_draws = storage.get_draws(code)
        settled, prize = settle_pending_purchases(storage, rule, all_draws)
        return {"settled_count": settled, "settled_prize": prize}

    @app.post("/api/purchases/delete")
    def delete_purchases(req: DeletePurchasesRequest) -> Dict[str, Any]:
        deleted = storage.delete_purchases(req.ids)
        return {"deleted": deleted}

    @app.post("/api/backtest/{code}")
    def run_backtest(code: str, req: BacktestRequest) -> Dict[str, Any]:
        rule = _get_rule_or_404(code)
        strategy = _resolve_strategy(req.strategy)
        draws = storage.get_draws(code)
        try:
            return backtest_engine.run(
                rule=rule,
                draws=draws,
                strategy=strategy,
                train_window=req.train_window,
                test_periods=req.test_periods,
                tickets_per_draw=req.tickets_per_draw,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/scheduler")
    def get_scheduler_state() -> Dict[str, Any]:
        return scheduler.get_state()

    @app.put("/api/scheduler")
    def update_scheduler_config(req: SchedulerConfigRequest) -> Dict[str, Any]:
        scheduler.update_config(req.model_dump(exclude_none=True))
        return scheduler.get_state()

    @app.post("/api/scheduler/run")
    def run_scheduler_now() -> Dict[str, Any]:
        return scheduler.run_once(trigger="manual")

    static_dir = _resolve_static_dir()
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        def serve_index():
            return FileResponse(str(static_dir / "index.html"))

    return app


def get_default_app() -> FastAPI:
    return create_app(start_scheduler=os.environ.get("LOTTERY_DISABLE_SCHEDULER", "") != "1")
