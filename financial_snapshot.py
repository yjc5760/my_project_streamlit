# financial_snapshot.py
"""
季報指標表：營收、毛利率、營益率、淨利率、EPS 的本季值與 QoQ／YoY，
外加兩個「獲利品質」檢查：業外收支占稅前淨利比、營業現金流 ÷ 稅後淨利（近四季）。

資料：FinMind TaiwanStockFinancialStatements（綜合損益表，單季數字）、
      TaiwanStockCashFlowsStatement（現金流量表，年初至今累計 → 換算單季，見 quality_screener）。
警示（flags）只做規則判斷、附上數字，不做好壞結論；門檻在 FLAG_RULES 調整。

純計算函式 build_snapshot() 不連網，方便測試；run_financial_snapshot() 才抓資料。
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

import finmind_client
from market_calendar import now_tw
from quality_screener import fetch_ocf_ytd, quarterly_from_ytd
from scrape_utils import disk_memo

FIN_TYPES = {
    "Revenue": "營收", "GrossProfit": "毛利", "OperatingIncome": "營業利益",
    "TotalNonoperatingIncomeAndExpense": "業外收支", "PreTaxIncome": "稅前淨利",
    "IncomeAfterTaxes": "稅後淨利", "EPS": "EPS",
}
FLAG_RULES = {
    "nonop_share": 30.0,        # 業外收支占稅前淨利 > 30%
    "cash_conversion": 0.8,     # 近四季營業現金流 ÷ 稅後淨利 < 0.8
    "gm_drop_pp": 2.0,          # 毛利率 YoY 或 QoQ 下滑 > 2 個百分點
}


def _pct(a, b):
    """成長率（%）；分母 ≤ 0 時不計算（由負轉正的成長率沒有意義）。"""
    if a is None or b is None or pd.isna(a) or pd.isna(b) or b <= 0:
        return None
    return round((a / b - 1) * 100, 2)


def _pp(a, b):
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    return round(a - b, 2)


def _r(v, nd=2):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def quarterly_table(fin: pd.DataFrame) -> pd.DataFrame:
    """FinMind 長表 → 每季一列（PeriodIndex），含金額與比率。"""
    df = fin[fin["type"].isin(FIN_TYPES)].copy()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["q"] = pd.PeriodIndex(pd.to_datetime(df["date"]), freq="Q")
    t = df.pivot_table(index="q", columns="type", values="value", aggfunc="last").sort_index()
    t = t.reindex(columns=list(FIN_TYPES))
    rev = t["Revenue"].where(t["Revenue"] > 0)
    t["毛利率(%)"] = t["GrossProfit"] / rev * 100
    t["營益率(%)"] = t["OperatingIncome"] / rev * 100
    t["淨利率(%)"] = t["IncomeAfterTaxes"] / rev * 100
    pre = t["PreTaxIncome"].where(t["PreTaxIncome"] > 0)
    t["業外占稅前(%)"] = t["TotalNonoperatingIncomeAndExpense"] / pre * 100
    return t


def build_snapshot(fin: pd.DataFrame, ocf_ytd: pd.Series | None = None, quarters: int = 8) -> dict:
    t = quarterly_table(fin)
    if t.empty or t["Revenue"].dropna().empty:
        raise ValueError("綜合損益表沒有營收資料")
    t = t[t["Revenue"].notna()]
    cur = t.index[-1]
    get = lambda q, c: t.at[q, c] if q in t.index else None          # noqa: E731
    prev_q, prev_y = cur - 1, cur - 4

    def money(c):          # 元 → 億
        v, q1, y1 = get(cur, c), get(prev_q, c), get(prev_y, c)
        return {"本季(億)": _r(v / 1e8 if v is not None else None), "QoQ(%)": _pct(v, q1), "YoY(%)": _pct(v, y1)}

    def ratio(c):
        v = get(cur, c)
        return {"本季(%)": _r(v), "QoQ(百分點)": _pp(v, get(prev_q, c)), "YoY(百分點)": _pp(v, get(prev_y, c))}

    eps = get(cur, "EPS")
    latest = {
        "營收": money("Revenue"),
        "毛利率": ratio("毛利率(%)"),
        "營益率": ratio("營益率(%)"),
        "淨利率": ratio("淨利率(%)"),
        "EPS": {"本季(元)": _r(eps), "QoQ(%)": _pct(eps, get(prev_q, "EPS")),
                "YoY(%)": _pct(eps, get(prev_y, "EPS"))},
        "業外占稅前淨利": {"本季(%)": _r(get(cur, "業外占稅前(%)"), 1)},
    }

    hist = t.iloc[-quarters:]
    trend = [{"季度": str(q), "營收(億)": _r(r["Revenue"] / 1e8), "毛利率(%)": _r(r["毛利率(%)"]),
              "營益率(%)": _r(r["營益率(%)"]), "淨利率(%)": _r(r["淨利率(%)"]), "EPS": _r(r["EPS"]),
              "業外占稅前(%)": _r(r["業外占稅前(%)"], 1)} for q, r in hist.iterrows()]

    flags, cash = [], None
    base_notes = []
    for key, col, fmt in (("營收", "Revenue", lambda b: f"{b / 1e8:.2f} 億"), ("EPS", "EPS", lambda b: f"{b:.2f} 元")):
        for lbl, bq in (("QoQ", prev_q), ("YoY", prev_y)):
            g = latest[key][f"{lbl}(%)"]
            if g is not None and abs(g) > 500:        # 基期過低：成長率超過 500% 時加註
                base_notes.append(f"{key} {lbl} {g:+.0f}% 的基期（{bq}）只有 {fmt(get(bq, col))}，"
                                  "基期過低，成長率參考性低")
    nonop = get(cur, "業外占稅前(%)")
    if nonop is not None and not pd.isna(nonop) and nonop > FLAG_RULES["nonop_share"]:
        flags.append(f"本季業外收支占稅前淨利 {nonop:.0f}%（> {FLAG_RULES['nonop_share']:.0f}%），"
                     "獲利有相當部分不是本業賺的，需確認是否一次性")
    for lbl, key in (("YoY", "YoY(百分點)"), ("QoQ", "QoQ(百分點)")):
        d = latest["毛利率"][key]
        if d is not None and d < -FLAG_RULES["gm_drop_pp"]:
            flags.append(f"毛利率 {lbl} 下滑 {abs(d):.1f} 個百分點")
    if latest["營收"]["YoY(%)"] is not None and latest["EPS"]["YoY(%)"] is not None \
            and latest["營收"]["YoY(%)"] > 0 and latest["EPS"]["YoY(%)"] < 0:
        flags.append("營收年增但 EPS 年減（營收成長沒有轉成獲利）")
    if t["PreTaxIncome"].iloc[-1] is not None and t["PreTaxIncome"].iloc[-1] <= 0:
        flags.append("本季稅前虧損")

    if ocf_ytd is not None and len(ocf_ytd):
        ocf_q = quarterly_from_ytd(ocf_ytd)
        ni = t["IncomeAfterTaxes"]
        both = pd.concat([ocf_q.rename("ocf"), ni.rename("ni")], axis=1).dropna()
        last4 = both.iloc[-4:]
        consecutive = len(last4) == 4 and (last4.index[-1] - last4.index[0]).n == 3
        if consecutive:
            o, n = last4["ocf"].sum(), last4["ni"].sum()
            conv = o / n if n > 0 else None
            cash = {"區間": f"{last4.index[0]}～{last4.index[-1]}", "營業現金流(億)": _r(o / 1e8),
                    "稅後淨利(億)": _r(n / 1e8), "現金轉換率(倍)": _r(conv)}
            if conv is not None and conv < FLAG_RULES["cash_conversion"]:
                flags.append(f"近四季營業現金流只有稅後淨利的 {conv:.2f} 倍（< {FLAG_RULES['cash_conversion']}），"
                             "帳上獲利沒有完整轉成現金（常見原因：應收帳款或存貨增加）")
            elif n > 0 and o < 0:
                flags.append("近四季稅後淨利為正、營業現金流為負")
            for q in last4.index:
                for row in trend:
                    if row["季度"] == str(q):
                        row["營業現金流(億)"] = _r(last4.at[q, "ocf"] / 1e8)
        else:
            cash = {"說明": "現金流量表季資料不連續，未計算"}

    return {"最新季度": str(cur), "比較基準": {"QoQ": str(prev_q), "YoY": str(prev_y)},
            "本季指標": latest, "近期季度": trend, "現金轉換": cash, "flags": flags,
            "基期說明": base_notes}


@disk_memo("income", 86400)                       # 季報一季才更新：1 天內不重抓
def fetch_income_statements(code: str, quarters: int = 8) -> pd.DataFrame:
    """FinMind 綜合損益表（長表）。多抓一年多，才能算最早一季的 YoY。"""
    start = now_tw().date() - timedelta(days=int((quarters / 4 + 1.5) * 365.25))
    return finmind_client.fetch("TaiwanStockFinancialStatements", code, start, allow_empty=False)


def run_financial_snapshot(code: str, quarters: int = 8) -> dict:
    fin = fetch_income_statements(code, quarters)
    notes = []
    try:
        ocf = fetch_ocf_ytd(code, years=max(2, int(np.ceil(quarters / 4)) + 1))
    except finmind_client.FinMindError as e:
        ocf, notes = None, [f"現金流量表抓取失敗，未計算現金轉換（{e}）"]
    res = build_snapshot(fin, ocf, quarters)
    res["notes"] = notes
    return res
