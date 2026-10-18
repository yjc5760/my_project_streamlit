# quality_screener.py
"""
四條件選股（從月營收選股的結果再加篩）：

  ② 營收 YoY 持續成長、不減速：最近 accel_months 個月的單月年增率都 > 0，且逐月不下降。
  ③ 均線多頭：MA5 > MA20 且 MA10 > MA20（日線收盤）。
  ① 大戶（持股 > 400 張）持續增加：最近 holder_weeks 週中至少 holder_min_up 週增加、
     期間累計為正、最新一週不減少。holder_strict=True 時另要求 8 週前 < 4 週前 < 最新。
  ④ 營業現金流持續增加：近四季合計（TTM）連續 ocf_quarters 季上升，且最新 TTM > 0。

依「成本低到高」逐關篩：② 不用連網 → ③ 日線（有快取）→ ① 大戶週資料 → ④ 現金流量表。
沒過前一關就不再抓後面的資料，結果裡會記錄卡在哪一關。

資料抓取函式由呼叫端注入（price_fn／holders_fn／cashflow_fn），純判斷函式不連網，方便測試。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

import numpy as np
import pandas as pd

import finmind_client
from market_calendar import now_tw
from scrape_utils import disk_memo

MAX_WORKERS = 4
OCF_TYPES = ("CashFlowsFromOperatingActivities", "NetCashInflowFromOperatingActivities")

PriceFn = Callable[[str], pd.DataFrame]       # 回傳含 close 欄的日線（DatetimeIndex）
HoldersFn = Callable[[str], pd.DataFrame]     # 回傳 資料日期, >400張大股東持有百分比
CashflowFn = Callable[[str], pd.Series]       # 回傳營業現金流「年初至今累計」（index＝季底日期）


@dataclass
class QualityParams:
    accel_months: int = 3          # ② 檢查最近幾個月的年增率
    holder_weeks: int = 4          # ① 看最近幾週
    holder_min_up: int = 3         # ① 其中至少幾週增加
    holder_strict: bool = False    # ① 另要求 8 週前 < 4 週前 < 最新
    ocf_quarters: int = 3          # ④ TTM 連續上升幾季
    min_volume_lots: float = 0     # 成交張數下限（0＝不限）


# ──────────────────────────────────────────────────────────────────────────────
# 純判斷函式
# ──────────────────────────────────────────────────────────────────────────────
def yoy_sequence(row: dict, months: int) -> list | None:
    """月營收選股一列 → 由舊到新的年增率 [前(m-1)月, …, 前1月, 當月]；缺值回傳 None。"""
    cols = [f"前{k}月年增(%)" for k in range(months - 1, 0, -1)] + ["年增(%)"]
    vals = [row.get(c) for c in cols]
    if any(v is None or pd.isna(v) for v in vals):
        return None
    return [float(v) for v in vals]


def revenue_accelerating(row: dict, months: int = 3) -> bool:
    seq = yoy_sequence(row, months)
    return bool(seq) and all(v > 0 for v in seq) and all(b >= a for a, b in zip(seq, seq[1:]))


def holders_rising(pct_new_to_old, weeks: int = 4, min_up: int = 3, strict: bool = False) -> dict:
    """pct_new_to_old：大戶持股比例，新到舊。回傳 {pass, 最新, N週增減, 8週增減, 週增減(舊→新)}。"""
    y = pd.Series(pct_new_to_old, dtype=float).dropna().to_numpy()[::-1]   # 轉成舊到新
    need = max(weeks + 1, 9 if strict else 0)
    if len(y) < need:
        return {"pass": False, "reason": f"大戶資料只有 {len(y)} 週，不足 {need} 週"}
    diffs = np.diff(y[-(weeks + 1):])
    d_n = y[-1] - y[-(weeks + 1)]
    ok = bool((diffs > 0).sum() >= min_up and d_n > 0 and diffs[-1] >= 0)
    d8 = y[-1] - y[-9] if len(y) >= 9 else None
    if strict:
        ok = bool(ok and len(y) >= 9 and y[-1] > y[-5] > y[-9])
    return {"pass": ok, "最新(%)": round(float(y[-1]), 2), f"{weeks}週增減": round(float(d_n), 2),
            "8週增減": round(float(d8), 2) if d8 is not None else None,
            "週增減(舊→新)": [round(float(d), 2) for d in diffs]}


def ma_aligned(close: pd.Series) -> dict:
    c = pd.Series(close, dtype=float).dropna()
    if len(c) < 20:
        return {"pass": False, "reason": f"日線只有 {len(c)} 根，不足 20 根"}
    ma5, ma10, ma20 = (float(c.iloc[-n:].mean()) for n in (5, 10, 20))
    return {"pass": ma5 > ma20 and ma10 > ma20, "日期": pd.Timestamp(c.index[-1]).strftime("%Y-%m-%d"),
            "收盤": round(float(c.iloc[-1]), 2), "MA5": round(ma5, 2), "MA10": round(ma10, 2),
            "MA20": round(ma20, 2)}


def quarterly_from_ytd(cum: pd.Series) -> pd.Series:
    """台灣財報現金流量表是「年初至今累計」：Q1 照用，其餘減掉同年前一季。缺前一季 → NaN。"""
    s = pd.Series(cum, dtype=float).dropna()
    s.index = pd.PeriodIndex(pd.to_datetime(s.index), freq="Q")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s.reindex(pd.period_range(s.index.min(), s.index.max(), freq="Q"))
    prev = s.shift(1)
    q = s - prev
    q[s.index.quarter == 1] = s[s.index.quarter == 1]
    return q


def ttm(quarterly: pd.Series) -> pd.Series:
    """近四季合計；四季中缺任一季 → NaN。"""
    return quarterly.rolling(4, min_periods=4).sum()


def ocf_rising(cum: pd.Series, quarters: int = 3) -> dict:
    t = ttm(quarterly_from_ytd(cum)).dropna()
    if len(t) < quarters + 1:
        return {"pass": False, "reason": f"可計算的 TTM 只有 {len(t)} 季，不足 {quarters + 1} 季"}
    tail = t.iloc[-(quarters + 1):]
    consecutive = (tail.index[-1] - tail.index[0]).n == quarters
    ok = bool(consecutive and (np.diff(tail.to_numpy()) > 0).all() and tail.iloc[-1] > 0)
    return {"pass": ok, "TTM營業現金流(億)": {str(k): round(float(v) / 1e8, 2) for k, v in tail.items()},
            **({} if consecutive else {"reason": "季資料不連續"})}


# ──────────────────────────────────────────────────────────────────────────────
# 資料抓取
# ──────────────────────────────────────────────────────────────────────────────
@disk_memo("ocf", 86400)                          # 季報一季才更新：1 天內不重抓
def fetch_ocf_ytd(code: str, years: int = 3) -> pd.Series:
    """FinMind 現金流量表 → 營業活動淨現金流（年初至今累計，元），index＝季底日期。"""
    start = now_tw().date() - timedelta(days=int(years * 365.25) + 100)
    df = finmind_client.fetch("TaiwanStockCashFlowsStatement", code, start, allow_empty=False)
    for t in OCF_TYPES:
        sub = df[df["type"] == t]
        if not sub.empty:
            return pd.Series(pd.to_numeric(sub["value"], errors="coerce").to_numpy(),
                             index=pd.to_datetime(sub["date"])).sort_index()
    raise finmind_client.FinMindError("no_data", f"{code} 現金流量表沒有營業活動現金流欄位")


# ──────────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class QualityResult:
    matches: list = field(default_factory=list)     # 四關全過
    detail: list = field(default_factory=list)      # 每檔的逐關結果（含未通過者）
    funnel: dict = field(default_factory=dict)      # 各關通過檔數
    errors: list = field(default_factory=list)      # 抓資料失敗：{代碼, 名稱, 關卡, error}


def _run_stage(rows, fn, workers=MAX_WORKERS):
    """對 rows 並發執行 fn(row)；回傳 [(row, result or Exception)]，順序同輸入。"""
    def safe(r):
        try:
            return fn(r)
        except Exception as e:                     # noqa: BLE001
            return e
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(zip(rows, ex.map(safe, rows)))


def screen_quality(rev_matches: pd.DataFrame, price_fn: PriceFn, holders_fn: HoldersFn,
                   cashflow_fn: CashflowFn, params: QualityParams | None = None) -> QualityResult:
    p = params or QualityParams()
    out = QualityResult()
    rows = [] if rev_matches is None or rev_matches.empty else rev_matches.to_dict("records")
    out.funnel["月營收選股"] = len(rows)

    if p.min_volume_lots and rows and "成交張數" in rows[0]:
        rows = [r for r in rows if pd.notna(r.get("成交張數")) and r["成交張數"] >= p.min_volume_lots]
        out.funnel[f"成交張數 ≥ {p.min_volume_lots:g}"] = len(rows)

    recs = {}
    for r in rows:
        code = str(r["代碼"]).strip()
        recs[code] = {"代碼": code, "名稱": r.get("名稱"), "營收月份": r.get("營收月份"),
                      "成交張數": r.get("成交張數"),
                      "②營收YoY(舊→新)": yoy_sequence(r, p.accel_months), "未通過": None}

    def fail(code, stage, why=None):
        recs[code]["未通過"] = stage + (f"（{why}）" if why else "")

    # ② 營收加速（不連網）
    alive = []
    for r in rows:
        code = str(r["代碼"]).strip()
        (alive.append(code) if revenue_accelerating(r, p.accel_months) else fail(code, "②營收加速"))
    out.funnel["②營收 YoY 不減速"] = len(alive)

    stages = [
        ("③均線", "③MA5、MA10 > MA20", lambda c: ma_aligned(price_fn(c)["close"])),
        ("①大戶", "①大戶持續增加",
         lambda c: holders_rising(holders_fn(c).iloc[:, 1], p.holder_weeks, p.holder_min_up, p.holder_strict)),
        ("④營現流", "④營業現金流 TTM 上升", lambda c: ocf_rising(cashflow_fn(c), p.ocf_quarters)),
    ]
    for key, label, fn in stages:
        nxt = []
        for code, res in _run_stage(alive, fn):
            if isinstance(res, Exception):
                out.errors.append({"代碼": code, "名稱": recs[code]["名稱"], "關卡": key, "error": str(res)})
                fail(code, key, "資料抓取失敗")
                continue
            recs[code][key] = res
            if res.get("pass"):
                nxt.append(code)
            else:
                fail(code, key, res.get("reason"))
        alive = nxt
        out.funnel[label] = len(alive)

    out.detail = list(recs.values())
    out.matches = [recs[c] for c in alive]
    return out
