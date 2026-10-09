"""月營收選股（今年每月創同期新高）— 以假資料測試，不連網。"""
import pandas as pd
import pytest

import revenue_screener as rs
from revenue_screener import RevenueParams, screen_revenue


def _rev(code, y, m):
    """各測試股票的「真實」月營收（元）。回傳 None 表示該月尚未公告。"""
    base = {2026: 100, 2025: 50, 2024: 60, 2023: 55, 2022: 70, 2021: 999}[y]   # 2021 超出 4 年，不應影響
    if code == "1111" and y == 2022 and m == 5:
        return 200                      # 2022/05 比 2026/05 高 → 不符條件 A
    if code == "2222" and y == 2025 and m == 3:
        return 150                      # 2026/03 年增率為負 → 不符條件 B
    if code == "3333" and y == 2026 and m == 9:
        return None                     # 9 月還沒公告
    if code == "4444" and y <= 2023:
        return None                     # 2024 年才上市：沒資料的年份不比較
    return base + m


def _fake_month(year, month):
    if (year, month) > (2026, 9):
        return pd.DataFrame()
    rows = []
    for code in ("2344", "1111", "2222", "3333", "4444"):
        cur, ly = _rev(code, year, month), _rev(code, year - 1, month)
        if cur is None:
            continue
        rows.append({"code": code, "name": f"股{code}", "revenue": float(cur),
                     "rev_prev_month": float("nan"),
                     "rev_last_year": float(ly) if ly is not None else float("nan"),
                     "mom": 1.0,
                     "yoy": (cur / ly - 1) * 100 if ly else float("nan"),
                     "market": "上市"})
    df = pd.DataFrame(rows)
    df["ym"] = year * 100 + month
    return df


@pytest.fixture(autouse=True)
def fake_mops(monkeypatch):
    monkeypatch.setattr(rs, "fetch_mops_month", _fake_month)


def test_every_month_new_high_and_positive_yoy():
    res = screen_revenue("2026-10-09", RevenueParams(), with_quotes=False)
    assert [y_m[:2] for y_m in res.months_loaded] == [(2026, m) for m in range(1, 10)]
    assert sorted(res.matches["代碼"]) == ["2344", "4444"]
    row = res.matches.set_index("代碼").loc["2344"]
    assert row["營收月份"] == "2026/09" and row["檢查月份"] == "1～9月"
    # 最弱的是 9 月：109 vs 前高 max(59, 69, 64, 79) = 79（2021 的 999 超出 4 年不列入）
    assert row["最弱月超越前高(%)"] == pytest.approx(round((109 / 79 - 1) * 100, 1))
    assert "前8月年增(%)" in res.matches.columns
    d = res.detail.set_index("code")
    assert not d.loc["1111", "match"]                      # 條件 A 失敗
    assert "2222" not in d.index                           # 條件 B 粗篩就被刷掉
    assert "3333" not in d.index                           # 9 月未公告 → 不入選


def test_per_company_month_uses_own_latest():
    res = screen_revenue("2026-10-09", RevenueParams(per_company_month=True), with_quotes=False)
    m = res.matches.set_index("代碼")
    assert "3333" in m.index and m.loc["3333", "營收月份"] == "2026/08"


def test_lookback_years_and_yoy_threshold():
    # 只比 1 年：1111 的 2022/05 不再列入比較 → 通過
    res = screen_revenue("2026-10-09", RevenueParams(lookback_years=1), with_quotes=False)
    assert "1111" in set(res.matches["代碼"])
    # 年增率門檻拉到 200%：全部不通過（2026 vs 2025 約 +100%）
    res = screen_revenue("2026-10-09", RevenueParams(yoy_min=200), with_quotes=False)
    assert res.matches.empty
