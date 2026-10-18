"""季報指標表（financial_snapshot）的純計算邏輯——數字取自欣興 3037 的 FinMind 實際資料。"""
import pandas as pd
import pytest

import financial_snapshot as fs

# 欣興：2025Q3～2026Q2（元；EPS 為元／股）
_Q = {
    "2025-09-30": dict(Revenue=33994419000, GrossProfit=4548515000, OperatingIncome=1532544000,
                       TotalNonoperatingIncomeAndExpense=1414176000, PreTaxIncome=2946720000,
                       IncomeAfterTaxes=2450951000, EPS=1.44),
    "2025-12-31": dict(Revenue=34690886000, GrossProfit=5472133000, OperatingIncome=2365656000,
                       TotalNonoperatingIncomeAndExpense=1784802000, PreTaxIncome=4150458000,
                       IncomeAfterTaxes=3915853000, EPS=2.32),
    "2026-03-31": dict(Revenue=37446455000, GrossProfit=6726042000, OperatingIncome=2756634000,
                       TotalNonoperatingIncomeAndExpense=3542211000, PreTaxIncome=6298845000,
                       IncomeAfterTaxes=5380738000, EPS=3.28),
    "2026-06-30": dict(Revenue=42889818000, GrossProfit=10638138000, OperatingIncome=6651152000,
                       TotalNonoperatingIncomeAndExpense=8924635000, PreTaxIncome=15575787000,
                       IncomeAfterTaxes=13342387000, EPS=8.45),
}


def _fin(q=_Q):
    rows = [{"date": d, "stock_id": "3037", "type": k, "value": v, "origin_name": ""}
            for d, kv in q.items() for k, v in kv.items()]
    rows.append({"date": "2026-06-30", "stock_id": "3037", "type": "OTHNOE", "value": 1, "origin_name": ""})
    return pd.DataFrame(rows)


# 欣興營業現金流（年初至今累計，億）
_OCF = pd.Series([e * 1e8 for e in [87.27374, 109.60347, 149.67094, 58.3267, 133.23412]],
                 index=pd.to_datetime(["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]))


def test_latest_quarter_metrics():
    s = fs.build_snapshot(_fin())
    m = s["本季指標"]
    assert s["最新季度"] == "2026Q2" and s["比較基準"] == {"QoQ": "2026Q1", "YoY": "2025Q2"}
    assert m["營收"]["本季(億)"] == 428.9
    assert m["營收"]["QoQ(%)"] == pytest.approx((42889818000 / 37446455000 - 1) * 100, abs=0.01)
    assert m["營收"]["YoY(%)"] is None                       # 沒有去年同季資料
    assert m["毛利率"]["本季(%)"] == pytest.approx(10638138000 / 42889818000 * 100, abs=0.01)
    assert m["毛利率"]["QoQ(百分點)"] == pytest.approx(24.80 - 17.96, abs=0.02)
    assert m["EPS"]["本季(元)"] == 8.45 and m["EPS"]["QoQ(%)"] == pytest.approx(157.62, abs=0.01)
    assert m["業外占稅前淨利"]["本季(%)"] == pytest.approx(57.3, abs=0.1)
    assert [r["季度"] for r in s["近期季度"]] == ["2025Q3", "2025Q4", "2026Q1", "2026Q2"]


def test_flags_nonoperating_share():
    s = fs.build_snapshot(_fin())
    assert any("業外收支占稅前淨利 57%" in f for f in s["flags"])


def test_cash_conversion():
    s = fs.build_snapshot(_fin(), _OCF)
    c = s["現金轉換"]
    assert c["區間"] == "2025Q3～2026Q2"
    assert c["營業現金流(億)"] == pytest.approx(195.63, abs=0.01)     # 和 quality_screener 的 TTM 一致
    assert c["稅後淨利(億)"] == pytest.approx(250.9, abs=0.01)
    assert c["現金轉換率(倍)"] == pytest.approx(0.78, abs=0.01)
    assert any("0.78 倍" in f for f in s["flags"])
    assert s["近期季度"][-1]["營業現金流(億)"] == pytest.approx(74.91, abs=0.01)


def test_gross_margin_drop_and_eps_divergence_flags():
    q = {k: dict(v) for k, v in _Q.items()}
    q["2026-06-30"].update(GrossProfit=6000000000, EPS=1.0, IncomeAfterTaxes=1000000000,
                           PreTaxIncome=1200000000, TotalNonoperatingIncomeAndExpense=100000000)
    q["2025-06-30"] = dict(Revenue=30000000000, GrossProfit=6000000000, OperatingIncome=1, PreTaxIncome=1,
                           TotalNonoperatingIncomeAndExpense=0, IncomeAfterTaxes=1, EPS=2.0)
    s = fs.build_snapshot(_fin(q))
    assert any(f.startswith("毛利率 QoQ 下滑") for f in s["flags"])
    assert any(f.startswith("毛利率 YoY 下滑") for f in s["flags"])
    assert any("EPS 年減" in f for f in s["flags"])


def test_negative_base_growth_is_none():
    assert fs._pct(5, -2) is None and fs._pct(5, 0) is None and fs._pct(6, 4) == 50.0


def test_low_base_note():
    q = {k: dict(v) for k, v in _Q.items()}
    q["2025-06-30"] = dict(Revenue=32466000000, GrossProfit=1, OperatingIncome=1, PreTaxIncome=1,
                           TotalNonoperatingIncomeAndExpense=0, IncomeAfterTaxes=1, EPS=0.02)
    s = fs.build_snapshot(_fin(q))
    assert s["本季指標"]["EPS"]["YoY(%)"] == 42150.0
    assert s["基期說明"] == ["EPS YoY +42150% 的基期（2025Q2）只有 0.02 元，基期過低，成長率參考性低"]
    assert fs.build_snapshot(_fin())["基期說明"] == []
