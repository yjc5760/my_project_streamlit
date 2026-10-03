"""第 1、2 項：非交易日預估量因子、盤中 5 日均量。"""
from datetime import date, datetime

import pandas as pd
import pytest

import market_calendar
import yahoo_scraper
from stock_analyzer import avg_volume_before_today

TZ = market_calendar.TZ

# 證交所 OpenAPI 實際回傳格式（2026 年度節錄）
HOLIDAY_ROWS = [
    {"Name": "中華民國開國紀念日", "Date": "1150101", "Weekday": "四", "Description": "依規定放假1日。"},
    {"Name": "國曆新年開始交易日", "Date": "1150102", "Weekday": "五", "Description": "國曆新年開始交易。"},
    {"Name": "農曆春節前最後交易日", "Date": "1150211", "Weekday": "三", "Description": "農曆春節前最後交易。"},
    {"Name": "市場無交易，僅辦理結算交割作業", "Date": "1150212", "Weekday": "四", "Description": ""},
    {"Name": "農曆除夕及春節", "Date": "1150216", "Weekday": "一", "Description": "依規定放假5日。"},
    {"Name": "農曆春節後開始交易日", "Date": "1150223", "Weekday": "一", "Description": "農曆春節後開始交易。"},
]


def test_parse_holiday_schedule():
    closed = market_calendar.parse_holiday_schedule(HOLIDAY_ROWS)
    assert closed == {date(2026, 1, 1), date(2026, 2, 12), date(2026, 2, 16)}


def test_is_trading_day(monkeypatch):
    monkeypatch.setattr(market_calendar, "_closed_days_for", lambda y: {date(2026, 10, 9)})
    assert market_calendar.is_trading_day(date(2026, 10, 8))        # 週四
    assert not market_calendar.is_trading_day(date(2026, 10, 9))    # 國定假日
    assert not market_calendar.is_trading_day(date(2026, 10, 10))   # 週六


def test_is_trading_hours():
    assert market_calendar.is_trading_hours(datetime(2026, 10, 8, 10, 0, tzinfo=TZ))
    assert not market_calendar.is_trading_hours(datetime(2026, 10, 8, 14, 0, tzinfo=TZ))
    assert not market_calendar.is_trading_hours(datetime(2026, 10, 10, 10, 0, tzinfo=TZ))


@pytest.mark.parametrize("when, expected", [
    (datetime(2026, 10, 3, 10, 0, tzinfo=TZ), 1.0),      # 週六上午：以前會算成 2.83 倍
    (datetime(2026, 10, 4, 9, 30, tzinfo=TZ), 1.0),      # 週日
    (datetime(2026, 10, 8, 10, 0, tzinfo=TZ), 2.83),     # 交易日 10:00 查表
    (datetime(2026, 10, 8, 13, 45, tzinfo=TZ), 1.0),     # 收盤後
])
def test_volume_factor(when, expected):
    assert yahoo_scraper._get_volume_factor(when) == pytest.approx(expected)


def test_volume_factor_holiday(monkeypatch):
    monkeypatch.setattr(market_calendar, "_closed_days_for", lambda y: {date(2026, 10, 9)})
    assert yahoo_scraper._get_volume_factor(datetime(2026, 10, 9, 10, 0, tzinfo=TZ)) == 1.0


def test_volume_factor_interpolates():
    f = yahoo_scraper._get_volume_factor(datetime(2026, 10, 8, 10, 2, 30, tzinfo=TZ))
    assert f == pytest.approx((2.83 + 2.70) / 2)


def _vol(days: list[str], values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.to_datetime(days))


DAYS = ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]


def test_avg_vol_intraday_keeps_yesterday():
    """盤中：FinMind 最後一根是昨天（10/7），5 日均量要含昨天。"""
    v = _vol(DAYS, [1, 1, 1, 1, 1, 100])
    assert avg_volume_before_today(v, today=date(2026, 10, 8)) == pytest.approx((1 * 4 + 100) / 5)


def test_avg_vol_after_close_excludes_today():
    """收盤後：最後一根就是今天（10/7），只取今天以前 5 根。"""
    v = _vol(DAYS, [1, 2, 3, 4, 5, 999])
    assert avg_volume_before_today(v, today=date(2026, 10, 7)) == pytest.approx(3.0)
