# market_calendar.py
"""
台股交易日曆：判斷某天是否為交易日、現在是否為盤中。

休市日來源：證交所 OpenAPI「市場開休市日期」（每個年度抓一次，存在記憶體）。
抓不到時退回「週一到週五都算交易日」，至少週末判斷正確。
"""
from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Asia/Taipei")
OPEN_TIME, CLOSE_TIME = dtime(9, 0), dtime(13, 30)
HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
_RETRY_AFTER = 1800          # 抓取失敗後，30 分鐘內不再重試（避免每次重繪都卡在逾時）

_lock = threading.Lock()
_closed_days: dict[int, set[date]] = {}         # 西元年 → 休市日
_failed_at: dict[int, float] = {}


def _parse_roc_date(s: str) -> date | None:
    """'1150101' → 2026-01-01；也接受 8 碼西元格式。"""
    s = re.sub(r"\D", "", str(s))
    try:
        if len(s) == 7:
            return date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:]))
        if len(s) == 8:
            return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None
    return None


def parse_holiday_schedule(rows: list[dict]) -> set[date]:
    """
    證交所休市表裡也列了「開始交易日」「最後交易日」這類有交易的日子，要排除：
    名稱含「交易日」且不含「無交易」→ 有交易；其餘（放假、僅辦理結算交割）→ 休市。
    """
    closed = set()
    for r in rows:
        d = _parse_roc_date(r.get("Date", ""))
        if d is None:
            continue
        name = f"{r.get('Name', '')}{r.get('Description', '')}"
        if "交易日" in r.get("Name", "") and "無交易" not in name:
            continue
        closed.add(d)
    return closed


def _closed_days_for(year: int) -> set[date]:
    with _lock:
        if year in _closed_days:
            return _closed_days[year]
        if time.time() - _failed_at.get(year, 0) < _RETRY_AFTER:
            return set()
    try:
        r = requests.get(HOLIDAY_URL, timeout=5, headers={"Accept": "application/json"})
        r.raise_for_status()
        closed = parse_holiday_schedule(r.json())
    except Exception as e:                       # noqa: BLE001  抓不到就只判斷週末
        print(f"[market_calendar] 無法取得休市日（{e}），暫以週一至週五為交易日")
        with _lock:
            _failed_at[year] = time.time()
        return set()
    by_year: dict[int, set[date]] = {}
    for d in closed:
        by_year.setdefault(d.year, set()).add(d)
    with _lock:
        for y, days in by_year.items():
            _closed_days[y] = days
        _closed_days.setdefault(year, set())     # 表內沒有這一年（例如年底還沒公告明年）
        return _closed_days[year]


def now_tw() -> datetime:
    return datetime.now(TZ)


def is_trading_day(d: date | None = None) -> bool:
    d = d or now_tw().date()
    if d.weekday() >= 5:
        return False
    return d not in _closed_days_for(d.year)


def is_trading_hours(now: datetime | None = None) -> bool:
    """交易日的 09:00～13:30（台北時間）。"""
    now = now or now_tw()
    return is_trading_day(now.date()) and OPEN_TIME <= now.time() <= CLOSE_TIME
