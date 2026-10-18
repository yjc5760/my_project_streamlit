# drawdown_backtest.py
"""
回檔門檻回測：股價自 N 日高點回檔 X% 時買進，持有 H 個交易日的歷史表現。

- 一律用還原股價：未還原股價在除權息、面額變更、減資日會跳空，會被誤判成回檔。
  FinMind 的 TaiwanStockPriceAdj 需付費等級，所以自己還原：日線（TaiwanStockPrice）
  ＋ 除權息結果、面額變更／分割、減資參考價（皆為免費資料），在事件日之前的價格乘上
  「事件後參考價 ÷ 事件前收盤」（向後還原，最新價格不變）。
- 同一波下跌只算第一次觸發：觸發後要等收盤價「再創 N 日新高」（回檔歸零）才重新計算。
- 基準：每一個交易日都當成買進日、持有 H 日的報酬分佈（等同「隨機任選一天買進」的期望，
  但不靠抽樣，結果可重現）。

純計算函式 backtest_drawdown() 不連網，方便測試；run_drawdown_backtest() 才抓資料。
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

import finmind_client
from market_calendar import now_tw

DEFAULT_THRESHOLDS = (10.0, 15.0, 20.0, 25.0)


# 事件資料集 → (事件前價格欄, 事件後參考價欄) 候選；欄名不符時跳過並在 notes 註明
ADJ_SOURCES = {
    "TaiwanStockDividendResult": ("除權息", [("before_price", "after_price")]),
    "TaiwanStockSplitPrice": ("面額變更／分割", [("before_price", "after_price")]),
    "TaiwanStockCapitalReductionReferencePrice": (
        "減資", [("ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice"),
                 ("before_price", "after_price")]),
}
JUMP_WARN = 0.105          # 台股漲跌幅 10%：還原後單日漲跌仍超過此值 → 可能有未還原事件


def adjustment_events(code: str, start, end) -> tuple[pd.DataFrame, list[str]]:
    """回傳 (事件表 [date, kind, factor], notes)。factor＝事件後參考價 ÷ 事件前收盤。"""
    rows, notes = [], []
    for ds, (kind, cols) in ADJ_SOURCES.items():
        try:
            df = finmind_client.fetch(ds, code, start, end)
        except finmind_client.FinMindError as e:
            notes.append(f"{kind}資料抓取失敗，未還原（{e}）")
            continue
        if df.empty:
            continue
        pair = next(((b, a) for b, a in cols if b in df.columns and a in df.columns), None)
        if pair is None:
            notes.append(f"{kind}資料欄位不符（{list(df.columns)}），未還原")
            continue
        b = pd.to_numeric(df[pair[0]], errors="coerce")
        a = pd.to_numeric(df[pair[1]], errors="coerce")
        ok = (b > 0) & (a > 0)
        for d, f in zip(pd.to_datetime(df.loc[ok, "date"]), (a[ok] / b[ok])):
            rows.append({"date": d, "kind": kind, "factor": float(f)})
    ev = pd.DataFrame(rows, columns=["date", "kind", "factor"])
    return ev.sort_values("date").reset_index(drop=True), notes


def adjust_close(close: pd.Series, events: pd.DataFrame) -> pd.Series:
    """向後還原：事件日之前的價格 × factor（同日多個事件相乘）。最新價格不變。"""
    adj = close.astype(float).copy()
    for d, f in events.groupby("date")["factor"].prod().items():
        adj[adj.index < d] *= f
    return adj


def fetch_adj_close(code: str, years: int = 10) -> tuple[pd.Series, dict]:
    """還原收盤價（DatetimeIndex，已排序）與還原說明。"""
    today = now_tw().date()
    start = today - timedelta(days=int(years * 365.25) + 10)
    close = finmind_client.price_history(code, start, today)["close"]
    events, notes = adjustment_events(code, start, today)
    adj = adjust_close(close, events)
    jumps = adj.pct_change().abs()
    big = jumps[jumps > JUMP_WARN]
    if len(big):
        notes.append("還原後仍有單日漲跌超過 10% 的日子（可能有未還原的事件或新股初上市）："
                     + "、".join(f"{d:%Y-%m-%d}({v * 100:+.1f}%)" for d, v in
                                (adj.pct_change()[big.index]).items()))
    info = {"方法": "日線 × 除權息／面額變更／減資事件因子（向後還原）",
            "事件數": {k: int(v) for k, v in events["kind"].value_counts().items()},
            "notes": notes}
    return adj, info


def _stats(r: np.ndarray) -> dict:
    if len(r) == 0:
        return {"中位數(%)": None, "平均(%)": None, "勝率(%)": None, "最差(%)": None, "最佳(%)": None}
    return {"中位數(%)": round(float(np.median(r)) * 100, 2),
            "平均(%)": round(float(np.mean(r)) * 100, 2),
            "勝率(%)": round(float((r > 0).mean()) * 100, 1),
            "最差(%)": round(float(r.min()) * 100, 2),
            "最佳(%)": round(float(r.max()) * 100, 2)}


def backtest_drawdown(close: pd.Series, thresholds=DEFAULT_THRESHOLDS,
                      lookback: int = 60, horizon: int = 60) -> dict:
    """
    close: 還原收盤價（依日期排序）。
    回傳 {summary: [...每個門檻一列], baseline: {...}, events: {門檻: [...]}, current: {...}}。
    """
    close = close.dropna().astype(float).sort_index()
    if len(close) < lookback + horizon:
        raise ValueError(f"資料只有 {len(close)} 個交易日，不足 lookback+horizon={lookback + horizon}")
    c = close.to_numpy()
    dates = close.index
    high = close.rolling(lookback, min_periods=lookback).max().to_numpy()
    dd = c / high - 1                                   # ≤ 0；前 lookback-1 天為 NaN
    n = len(c)

    # 基準：每天買進持有 horizon 日
    base = c[horizon:] / c[:-horizon] - 1
    base = base[~np.isnan(dd[:-horizon])]               # 和觸發樣本同一段期間（有 N 日高點之後）

    summary, events = [], {}
    for t in thresholds:
        lim = -float(t) / 100 + 1e-9                     # 剛好跌到門檻也算（避開浮點誤差）
        armed, evs = True, []
        for i in range(n):
            if np.isnan(dd[i]):
                continue
            if not armed and dd[i] >= 0:                # 再創 N 日新高 → 這一波結束，重新計算
                armed = True
            if armed and dd[i] <= lim:
                armed = False
                j = i + horizon
                done = j < n
                end = j if done else n - 1
                path = c[i:end + 1]
                evs.append({
                    "觸發日": dates[i].strftime("%Y-%m-%d"),
                    "觸發價": round(float(c[i]), 2),
                    "距高點(%)": round(float(dd[i]) * 100, 2),
                    f"持有{horizon}日報酬(%)": round(float(c[end] / c[i] - 1) * 100, 2),
                    "期間最大浮虧(%)": round(float(path.min() / c[i] - 1) * 100, 2),
                    "已滿期": bool(done),
                })
        done_r = np.array([e[f"持有{horizon}日報酬(%)"] / 100 for e in evs if e["已滿期"]])
        mae = np.array([e["期間最大浮虧(%)"] / 100 for e in evs if e["已滿期"]])
        row = {"回檔門檻(%)": t, "觸發次數": len(evs), "已滿期次數": int(len(done_r)), **_stats(done_r),
               "平均最大浮虧(%)": round(float(mae.mean()) * 100, 2) if len(mae) else None,
               "最深浮虧(%)": round(float(mae.min()) * 100, 2) if len(mae) else None}
        if len(done_r) and len(base):
            row["中位數 − 基準(%)"] = round((float(np.median(done_r)) - float(np.median(base))) * 100, 2)
        summary.append(row)
        events[str(t)] = evs

    cur_dd = float(dd[-1]) if not np.isnan(dd[-1]) else None
    return {
        "summary": summary,
        "baseline": {"樣本天數": int(len(base)), **_stats(base)},
        "events": events,
        "current": {"日期": dates[-1].strftime("%Y-%m-%d"), "還原收盤": round(float(c[-1]), 2),
                    f"{lookback}日高點": round(float(high[-1]), 2) if not np.isnan(high[-1]) else None,
                    "距高點(%)": round(cur_dd * 100, 2) if cur_dd is not None else None,
                    "已達門檻": [t for t in thresholds if cur_dd is not None and cur_dd <= -t / 100 + 1e-9]},
        "period": {"起": dates[0].strftime("%Y-%m-%d"), "迄": dates[-1].strftime("%Y-%m-%d"),
                   "交易日數": int(n)},
    }


def run_drawdown_backtest(code: str, years: int = 10, thresholds=DEFAULT_THRESHOLDS,
                          lookback: int = 60, horizon: int = 60) -> dict:
    adj, info = fetch_adj_close(code, years)
    res = backtest_drawdown(adj, thresholds, lookback, horizon)
    res["adjustment"] = info
    return res
