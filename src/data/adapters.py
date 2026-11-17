"""Pluggable historical lottery data collection adapters."""

from __future__ import annotations

import csv
import random
import re
from abc import ABC, abstractmethod
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

import openpyxl
import requests

from src.core.models import DrawRecord
from src.core.registry import get_lottery_rule


class DataAdapterError(RuntimeError):
    """Raised when a data adapter fails to fetch or parse draw records."""


class BaseDataAdapter(ABC):
    """Abstract base class for all historical lottery data adapters."""

    name: str = "base"
    supported_lotteries: Set[str] = set()

    @abstractmethod
    def fetch(self, lottery_code: str, limit: int = 100) -> List[DrawRecord]:
        """Fetch up to `limit` historical draw records for `lottery_code`."""

    def supports(self, lottery_code: str) -> bool:
        return lottery_code in self.supported_lotteries


def _parse_int_tokens(raw: str) -> List[int]:
    """Extract integers from strings like '01,05,12 18|25'."""
    return [int(tok) for tok in re.findall(r"\d+", str(raw))]


class CwlApiAdapter(BaseDataAdapter):
    """Adapter for China Welfare Lottery (中国福彩网) public JSON API (ssq, fc3d)."""

    name = "中国福彩网公开接口"
    supported_lotteries = {"ssq", "fc3d"}

    _LOTTERY_MAP = {
        "ssq": "ssq",
        "fc3d": "3d",
    }
    _API_URL = "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"

    def __init__(self, timeout: int = 12) -> None:
        self.timeout = timeout

    def fetch(self, lottery_code: str, limit: int = 100) -> List[DrawRecord]:
        if not self.supports(lottery_code):
            raise DataAdapterError(f"{self.name} 不支持彩种: {lottery_code}")

        rule = get_lottery_rule(lottery_code)
        api_name = self._LOTTERY_MAP[lottery_code]
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.cwl.gov.cn/",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        records: List[DrawRecord] = []
        page_size = min(max(limit, 30), 100)
        page_no = 1

        try:
            with requests.Session() as session:
                while len(records) < limit:
                    params = {
                        "name": api_name,
                        "pageNo": page_no,
                        "pageSize": page_size,
                        "systemType": "PC",
                    }
                    resp = session.get(self._API_URL, params=params, headers=headers, timeout=self.timeout)
                    resp.raise_for_status()
                    payload = resp.json()
                    items = payload.get("result") or []
                    if not items:
                        break

                    for item in items:
                        issue = str(item.get("code", "")).strip()
                        raw_date = str(item.get("date", "")).strip()
                        draw_date = raw_date[:10] if len(raw_date) >= 10 else raw_date
                        if lottery_code == "ssq":
                            reds = sorted(_parse_int_tokens(item.get("red", "")))
                            blues = _parse_int_tokens(item.get("blue", ""))
                            numbers = {"red": reds, "blue": blues}
                        else:  # fc3d
                            digits = _parse_int_tokens(item.get("red", ""))
                            if len(digits) != 3:
                                continue
                            numbers = {
                                "hundreds": [digits[0]],
                                "tens": [digits[1]],
                                "units": [digits[2]],
                            }
                        rule.validate_numbers(numbers)
                        records.append(
                            DrawRecord(
                                lottery_code=lottery_code,
                                issue=issue,
                                draw_date=draw_date,
                                numbers=numbers,
                            )
                        )
                        if len(records) >= limit:
                            break

                    if len(items) < page_size:
                        break
                    page_no += 1
        except requests.RequestException as exc:
            raise DataAdapterError(f"请求 {self.name} 失败: {exc}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise DataAdapterError(f"解析 {self.name} 数据失败: {exc}") from exc

        return records


class SportteryApiAdapter(BaseDataAdapter):
    """Adapter for China Sports Lottery (中国体彩网) public JSON API (dlt)."""

    name = "中国体彩网公开接口"
    supported_lotteries = {"dlt"}
    _API_URL = "https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry"

    def __init__(self, timeout: int = 12) -> None:
        self.timeout = timeout

    def fetch(self, lottery_code: str, limit: int = 100) -> List[DrawRecord]:
        if not self.supports(lottery_code):
            raise DataAdapterError(f"{self.name} 不支持彩种: {lottery_code}")

        rule = get_lottery_rule(lottery_code)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.lottery.gov.cn/",
            "Accept": "application/json, text/plain, */*",
        }
        records: List[DrawRecord] = []
        page_size = min(max(limit, 30), 100)
        page_no = 1

        try:
            with requests.Session() as session:
                while len(records) < limit:
                    params = {
                        "gameNo": "85",
                        "provinceId": "0",
                        "pageSize": page_size,
                        "isVerify": "1",
                        "pageNo": page_no,
                    }
                    resp = session.get(self._API_URL, params=params, headers=headers, timeout=self.timeout)
                    resp.raise_for_status()
                    payload = resp.json()
                    items = ((payload.get("value") or {}).get("list")) or []
                    if not items:
                        break

                    for item in items:
                        issue = str(item.get("lotteryDrawNum", "")).strip()
                        draw_date = str(item.get("lotteryDrawTime", "")).strip()[:10]
                        tokens = _parse_int_tokens(item.get("lotteryDrawResult", ""))
                        if len(tokens) < 7:
                            continue
                        numbers = {
                            "front": sorted(tokens[:5]),
                            "back": sorted(tokens[5:7]),
                        }
                        rule.validate_numbers(numbers)
                        records.append(
                            DrawRecord(
                                lottery_code=lottery_code,
                                issue=issue,
                                draw_date=draw_date,
                                numbers=numbers,
                            )
                        )
                        if len(records) >= limit:
                            break

                    if len(items) < page_size:
                        break
                    page_no += 1
        except requests.RequestException as exc:
            raise DataAdapterError(f"请求 {self.name} 失败: {exc}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise DataAdapterError(f"解析 {self.name} 数据失败: {exc}") from exc

        return records


class CsvExcelAdapter(BaseDataAdapter):
    """Adapter for importing historical draws from local CSV or Excel files."""

    name = "本地 CSV/Excel 导入适配器"
    supported_lotteries = {"ssq", "dlt", "fc3d"}

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)

    def fetch(self, lottery_code: str, limit: int = 10000) -> List[DrawRecord]:
        if not self.file_path.exists():
            raise DataAdapterError(f"文件不存在: {self.file_path}")

        rule = get_lottery_rule(lottery_code)
        try:
            rows = self._read_rows(self.file_path)
        except Exception as exc:
            raise DataAdapterError(f"读取文件失败: {exc}") from exc

        if not rows:
            return []

        col_map = {c.strip().lower(): c for c in rows[0].keys() if c}

        def _pick_col(candidates: List[str]) -> Optional[str]:
            for cand in candidates:
                if cand.lower() in col_map:
                    return col_map[cand.lower()]
            return None

        issue_col = _pick_col(["issue", "期号", "code", "draw_num"])
        date_col = _pick_col(["draw_date", "开奖日期", "date", "日期"])
        if not issue_col:
            raise DataAdapterError("文件中缺少期号列（支持列名: issue / 期号）")

        records: List[DrawRecord] = []
        for row in rows:
            issue = str(row.get(issue_col, "")).strip()
            if not issue:
                continue
            draw_date = str(row.get(date_col, "")).strip()[:10] if date_col else ""
            numbers = self._extract_numbers(lottery_code, row, _pick_col)
            rule.validate_numbers(numbers)
            records.append(
                DrawRecord(
                    lottery_code=lottery_code,
                    issue=issue,
                    draw_date=draw_date,
                    numbers=numbers,
                )
            )
            if len(records) >= limit:
                break
        return records

    @staticmethod
    def _read_rows(file_path: Path) -> List[Dict[str, str]]:
        if file_path.suffix.lower() in {".xlsx", ".xls"}:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            try:
                ws = wb.active
                it = ws.iter_rows(values_only=True)
                headers = [str(h).strip() if h is not None else "" for h in next(it, ())]
                rows: List[Dict[str, str]] = []
                for vals in it:
                    rows.append(
                        {
                            headers[i]: ("" if i >= len(vals) or vals[i] is None else str(vals[i]))
                            for i in range(len(headers))
                            if headers[i]
                        }
                    )
                return rows
            finally:
                wb.close()

        with file_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            return [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]

    @staticmethod
    def _extract_numbers(lottery_code: str, row: Dict[str, str], pick_col) -> Dict[str, List[int]]:
        if lottery_code == "ssq":
            r_col = pick_col(["red", "红球", "前区"])
            b_col = pick_col(["blue", "蓝球", "后区"])
            if not r_col or not b_col:
                raise DataAdapterError("双色球文件需包含 red/红球 与 blue/蓝球 列")
            return {
                "red": sorted(_parse_int_tokens(row[r_col])),
                "blue": _parse_int_tokens(row[b_col]),
            }
        if lottery_code == "dlt":
            f_col = pick_col(["front", "前区", "red", "红球"])
            b_col = pick_col(["back", "后区", "blue", "蓝球"])
            if not f_col or not b_col:
                raise DataAdapterError("大乐透文件需包含 front/前区 与 back/后区 列")
            return {
                "front": sorted(_parse_int_tokens(row[f_col])),
                "back": sorted(_parse_int_tokens(row[b_col])),
            }
        # fc3d
        n_col = pick_col(["numbers", "开奖号码", "号码", "red"])
        if n_col:
            digits = _parse_int_tokens(row[n_col])
            if len(digits) == 1 and len(str(row[n_col]).strip()) == 3:
                digits = [int(ch) for ch in str(row[n_col]).strip()]
            if len(digits) != 3:
                raise DataAdapterError(f"福彩3D 号码格式错误: {row[n_col]}")
            return {"hundreds": [digits[0]], "tens": [digits[1]], "units": [digits[2]]}

        h_col = pick_col(["hundreds", "百位"])
        t_col = pick_col(["tens", "十位"])
        u_col = pick_col(["units", "个位"])
        if h_col and t_col and u_col:
            return {
                "hundreds": [int(row[h_col])],
                "tens": [int(row[t_col])],
                "units": [int(row[u_col])],
            }
        raise DataAdapterError("福彩3D 文件需包含 numbers/开奖号码 列，或 hundreds/tens/units 列")


class MockSampleAdapter(BaseDataAdapter):
    """Generates realistic synthetic historical draws for offline testing and demos."""

    name = "内置模拟数据生成器（离线演示）"
    supported_lotteries = {"ssq", "dlt", "fc3d"}

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)

    def fetch(self, lottery_code: str, limit: int = 120) -> List[DrawRecord]:
        rule = get_lottery_rule(lottery_code)
        today = date.today()
        records: List[DrawRecord] = []

        for idx in range(limit):
            issue_num = 2026000 + (limit - idx)
            draw_dt = today - timedelta(days=idx * 2)
            numbers: Dict[str, List[int]] = {}
            for z in rule.zones:
                pool = list(range(z.min_num, z.max_num + 1))
                picked = self._rng.sample(pool, z.count)
                numbers[z.key] = picked if z.ordered else sorted(picked)
            records.append(
                DrawRecord(
                    lottery_code=lottery_code,
                    issue=str(issue_num),
                    draw_date=draw_dt.isoformat(),
                    numbers=numbers,
                )
            )
        return records


class AdapterManager:
    """Routes online data collection requests to the appropriate registered adapter."""

    def __init__(self) -> None:
        self._online_adapters: List[BaseDataAdapter] = [
            CwlApiAdapter(),
            SportteryApiAdapter(),
        ]

    def fetch_online(self, lottery_code: str, limit: int = 100) -> List[DrawRecord]:
        for adapter in self._online_adapters:
            if adapter.supports(lottery_code):
                return adapter.fetch(lottery_code, limit=limit)
        raise DataAdapterError(f"未找到支持彩种 '{lottery_code}' 的在线采集适配器")

    @staticmethod
    def import_file(lottery_code: str, file_path: str | Path) -> List[DrawRecord]:
        return CsvExcelAdapter(file_path).fetch(lottery_code)

    @staticmethod
    def generate_mock(lottery_code: str, limit: int = 120) -> List[DrawRecord]:
        return MockSampleAdapter().fetch(lottery_code, limit=limit)
