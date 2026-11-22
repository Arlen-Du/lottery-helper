"""Background auto-sync and auto-settlement scheduler for NAS deployment."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.core.registry import list_lottery_rules
from src.data.adapters import AdapterManager, DataAdapterError
from src.data.storage import SQLiteStorage
from src.strategy.prize import settle_pending_purchases

logger = logging.getLogger("lottery_helper.scheduler")

SETTINGS_KEY_CONFIG = "auto_sync_config"
SETTINGS_KEY_STATE = "auto_sync_state"

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "interval_minutes": 360,
    "sync_limit": 30,
    "auto_settle": True,
    "lotteries": ["ssq", "dlt", "fc3d"],
}


class AutoSyncScheduler:
    """Lightweight daemon-thread scheduler that periodically syncs draws and settles purchases."""

    def __init__(
        self,
        storage: SQLiteStorage,
        adapter_manager: Optional[AdapterManager] = None,
    ) -> None:
        self._storage = storage
        self._adapters = adapter_manager or AdapterManager()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._run_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._is_running_job = False

    def get_config(self) -> Dict[str, Any]:
        saved = self._storage.get_setting(SETTINGS_KEY_CONFIG, {})
        cfg = dict(DEFAULT_CONFIG)
        if isinstance(saved, dict):
            cfg.update(saved)
        cfg["interval_minutes"] = max(15, min(int(cfg.get("interval_minutes", 360)), 10080))
        cfg["sync_limit"] = max(10, min(int(cfg.get("sync_limit", 30)), 300))
        cfg["enabled"] = bool(cfg.get("enabled", True))
        cfg["auto_settle"] = bool(cfg.get("auto_settle", True))
        valid_codes = {r.code for r in list_lottery_rules()}
        raw_lotteries = cfg.get("lotteries") or list(valid_codes)
        cfg["lotteries"] = [c for c in raw_lotteries if c in valid_codes] or list(valid_codes)
        return cfg

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self.get_config()
        for key in ("enabled", "interval_minutes", "sync_limit", "auto_settle", "lotteries"):
            if key in patch and patch[key] is not None:
                cfg[key] = patch[key]
        self._storage.set_setting(SETTINGS_KEY_CONFIG, cfg)
        self._wake_event.set()
        return self.get_config()

    def get_state(self) -> Dict[str, Any]:
        state = self._storage.get_setting(SETTINGS_KEY_STATE, {})
        if not isinstance(state, dict):
            state = {}
        cfg = self.get_config()
        next_run_at = None
        if cfg["enabled"]:
            last_run_str = state.get("last_run_at")
            if last_run_str:
                try:
                    last_dt = datetime.fromisoformat(last_run_str)
                    next_run_at = (last_dt + timedelta(minutes=cfg["interval_minutes"])).isoformat(timespec="seconds")
                except ValueError:
                    next_run_at = None
        return {
            "config": cfg,
            "is_running": self._is_running_job,
            "last_run_at": state.get("last_run_at"),
            "next_run_at": next_run_at,
            "last_status": state.get("last_status", "idle"),
            "last_summary": state.get("last_summary", "尚未执行定时同步"),
            "last_results": state.get("last_results", []),
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(target=self._loop, name="lottery-auto-sync", daemon=True)
        self._thread.start()
        logger.info("AutoSyncScheduler started.")

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("AutoSyncScheduler stopped.")

    def _seconds_until_next_run(self, cfg: Dict[str, Any]) -> float:
        state = self._storage.get_setting(SETTINGS_KEY_STATE, {})
        last_run_str = state.get("last_run_at") if isinstance(state, dict) else None
        interval_sec = cfg["interval_minutes"] * 60
        if not last_run_str:
            # Wait 30s after fresh boot so startup stays fast
            return 30.0
        try:
            last_dt = datetime.fromisoformat(last_run_str)
            elapsed = (datetime.now() - last_dt).total_seconds()
            return max(0.0, interval_sec - elapsed)
        except ValueError:
            return float(interval_sec)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            cfg = self.get_config()
            if not cfg["enabled"]:
                self._wake_event.wait(timeout=60.0)
                self._wake_event.clear()
                continue

            wait_sec = self._seconds_until_next_run(cfg)
            if wait_sec > 0:
                woke = self._wake_event.wait(timeout=min(wait_sec, 60.0))
                if woke:
                    self._wake_event.clear()
                continue

            if not self._stop_event.is_set():
                self.run_once(trigger="scheduled")

    def run_once(self, trigger: str = "manual") -> Dict[str, Any]:
        """Synchronize all configured lotteries and settle pending purchases."""
        if not self._run_lock.acquire(blocking=False):
            return self.get_state()

        self._is_running_job = True
        try:
            cfg = self.get_config()
            rules_by_code = {r.code: r for r in list_lottery_rules()}
            results: List[Dict[str, Any]] = []
            total_synced = 0
            total_settled = 0
            total_prize = 0
            errors = 0

            for code in cfg["lotteries"]:
                rule = rules_by_code.get(code)
                if rule is None:
                    continue
                item: Dict[str, Any] = {
                    "lottery_code": code,
                    "lottery_name": rule.name,
                    "status": "ok",
                    "fetched": 0,
                    "settled": 0,
                    "prize_amount": 0,
                    "latest_issue": "",
                    "message": "",
                }
                try:
                    records = self._adapters.fetch_online(code, limit=cfg["sync_limit"])
                    self._storage.save_draws(records)
                    item["fetched"] = len(records)
                    total_synced += len(records)
                    all_draws = self._storage.get_draws(code)
                    if all_draws:
                        item["latest_issue"] = all_draws[0].issue
                    if cfg["auto_settle"]:
                        settled, prize = settle_pending_purchases(self._storage, rule, all_draws)
                        item["settled"] = settled
                        item["prize_amount"] = prize
                        total_settled += settled
                        total_prize += prize
                    item["message"] = (
                        f"同步 {len(records)} 期"
                        + (f"，自动兑奖 {item['settled']} 注（奖金 ¥{item['prize_amount']}）" if item["settled"] else "")
                    )
                except (DataAdapterError, Exception) as exc:  # noqa: BLE001
                    errors += 1
                    item["status"] = "error"
                    item["message"] = str(exc)
                    logger.warning("Auto-sync failed for %s: %s", code, exc)
                results.append(item)

            now_iso = datetime.now().isoformat(timespec="seconds")
            if errors == 0:
                status = "success"
            elif errors < len(results):
                status = "partial"
            else:
                status = "error"

            trigger_label = "定时任务" if trigger == "scheduled" else "手动触发"
            summary = (
                f"[{trigger_label}] 共处理 {len(results)} 个彩种，拉取 {total_synced} 期，"
                f"兑奖 {total_settled} 注（中奖 ¥{total_prize}）"
            )
            if errors:
                summary += f"，{errors} 个彩种同步失败"

            self._storage.set_setting(
                SETTINGS_KEY_STATE,
                {
                    "last_run_at": now_iso,
                    "last_status": status,
                    "last_summary": summary,
                    "last_results": results,
                },
            )
            return self.get_state()
        finally:
            self._is_running_job = False
            self._run_lock.release()
