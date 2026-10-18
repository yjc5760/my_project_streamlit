# backtest_103.py
"""
我的選股103 回測（規則與 local_screener.coarse_screen + evaluate_103 相同）

用法（在專案資料夾的 cmd）：
    .venv-mcp\\Scripts\\python backtest_103.py fetch     # 抓日線到 .cache/bt103/（需網路；可中斷、重跑會續抓）
    .venv-mcp\\Scripts\\python backtest_103.py run       # 離線回測，結果寫到 mcp_output/backtest_103/

    # 改用 Yahoo Finance（快很多；需先 pip install yfinance）
    .venv-mcp\\Scripts\\python backtest_103.py fetch --source yahoo   # 存到 .cache/bt103/prices_yahoo/
    .venv-mcp\\Scripts\\python backtest_103.py compare                # 比對兩個來源重疊的股票
    .venv-mcp\\Scripts\\python backtest_103.py run --source yahoo

設計：
- 訊號：第 t 天收盤後依 8 個條件選出；第 t+1 天開盤買進。開盤即漲停（≥ 昨收 × 1.095）視為買不到。
- 報酬用還原價（除權息、面額變更、減資）計算；訊號本身用原始價（和實際選股時看到的一致）。
- 交易成本：來回 0.585%（手續費 0.1425% × 2 ＋ 證交稅 0.3%）。
- 基準：同一買進日、全部股票等權重的同樣持有期報酬。
- 已知偏誤：股票池是「目前仍上市櫃」的普通股（已下市股票不在內，存活者偏誤）；
  紅K棒幅的昨收用前一交易日收盤，除權息當天與證交所的參考價略有不同。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
CACHE = ROOT / ".cache" / "bt103"
PRICES = CACHE / "prices"
PRICES_YAHOO = CACHE / "prices_yahoo"
DIVS = CACHE / "dividends"
OUT = ROOT / "mcp_output" / "backtest_103"
COST = 0.00585
LIMIT_UP_OPEN = 1.095
_COMMON = re.compile(r"^[1-9]\d{3}$")


def _load_token() -> None:
    if os.getenv("FINMIND_API_TOKEN"):
        return
    p = ROOT / ".streamlit" / "secrets.toml"
    if p.exists():
        m = re.search(r'^\s*FINMIND_API_TOKEN\s*=\s*["\']([^"\']+)["\']', p.read_text(encoding="utf-8"), re.M)
        if m:
            os.environ["FINMIND_API_TOKEN"] = m.group(1)


# ──────────────────────────────────────────────────────────────────────────────
# 訊號（純計算，不連網）
# ──────────────────────────────────────────────────────────────────────────────
def _kd_arrays(high, low, close, n=9, init=50.0):
    from indicators import tw_kd
    k, d = tw_kd(pd.Series(high), pd.Series(low), pd.Series(close), n, init)
    return k.to_numpy(), d.to_numpy()


def weekly_k_asof(df: pd.DataFrame, n: int = 9, init: float = 50.0) -> tuple[np.ndarray, np.ndarray]:
    """
    每個交易日「當下」的週 K 與上一週 K，等同 evaluate_103 把日線截到當天後算 to_weekly + tw_kd：
    本週未收完也算一根（週一到當天的高低收），前幾週為完整週線。
    """
    idx = df.index
    wk = idx.to_period("W-FRI")
    g = pd.DataFrame({"high": df["high"].to_numpy(), "low": df["low"].to_numpy(),
                      "close": df["close"].to_numpy(), "w": wk})
    W = g.groupby("w", sort=True).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    Kw, _ = _kd_arrays(W["high"].to_numpy(), W["low"].to_numpy(), W["close"].to_numpy(), n, init)
    wpos = pd.Index(W.index).get_indexer(wk)                       # 每天所在的週序號
    wtd_high = g.groupby("w")["high"].cummax().to_numpy()
    wtd_low = g.groupby("w")["low"].cummin().to_numpy()
    Wh, Wl = W["high"].to_numpy(), W["low"].to_numpy()
    # 前 n-1 個完整週的最高／最低（不含本週）
    prev_hh = pd.Series(Wh).rolling(n - 1, min_periods=n - 1).max().shift(1).to_numpy()
    prev_ll = pd.Series(Wl).rolling(n - 1, min_periods=n - 1).min().shift(1).to_numpy()
    c = g["close"].to_numpy()
    k_now = np.full(len(c), np.nan)
    k_prev = np.full(len(c), np.nan)
    for i in range(len(c)):
        j = wpos[i]
        if j < n - 1:
            continue
        hh = max(prev_hh[j], wtd_high[i])
        ll = min(prev_ll[j], wtd_low[i])
        rsv = 50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100
        pk = init if j == n - 1 else Kw[j - 1]
        k_now[i] = pk * 2 / 3 + rsv / 3
        k_prev[i] = Kw[j - 1] if j >= 1 else np.nan
    return k_now, k_prev


def conditions_103(df: pd.DataFrame, p) -> pd.DataFrame:
    """df：原始日線 open/high/low/close/volume（股），DatetimeIndex。回傳每天 8 個條件的布林值與指標。"""
    d = df.sort_index()
    c, o, v = d["close"], d["open"], d["volume"]
    prev_c, prev_v = c.shift(1), v.shift(1)
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    dk, dd = _kd_arrays(d["high"].to_numpy(), d["low"].to_numpy(), c.to_numpy(), p.kd_period)
    wk, wk_prev = weekly_k_asof(d, p.kd_period)
    denom = o if p.red_k_base == "open" else prev_c
    red_k = (c - o) / denom * 100
    lots = v / 1000
    out = pd.DataFrame(index=d.index)
    out["red_k"], out["vol_lots"] = red_k, lots
    out["ma60_dev"] = (c / ma60 - 1) * 100
    out["day_k"], out["day_d"], out["week_k"], out["week_k_prev"] = dk, dd, wk, wk_prev
    out["c1_red_k"] = red_k.between(p.red_k_min, p.red_k_max)
    out["c2_volume"] = lots.between(p.vol_min, p.vol_max)
    out["c3_ma60_dev"] = out["ma60_dev"].between(p.ma60_dev_min, p.ma60_dev_max)
    out["c4_week_k"] = out["week_k"].between(p.wk_min, p.wk_max)
    out["c5_week_k_up"] = (out["week_k"] > out["week_k_prev"]) if p.require_wk_up else True
    out["c6_ma20_lt_ma60"] = (ma20 < ma60) if p.require_ma20_lt_ma60 else True
    out["c7_dk_gt_dd"] = (out["day_k"] > out["day_d"]) if p.require_dk_gt_dd else True
    out["c8_vol_up"] = v > p.vol_ratio * prev_v
    enough = pd.Series(np.arange(len(d)) >= 60, index=d.index)          # 和 evaluate_103 一樣至少 61 根
    cond = [k for k in out.columns if k.startswith("c")]
    out[cond] = out[cond].fillna(False).astype(bool) & enough.to_numpy()[:, None]
    out["signal"] = out[cond].all(axis=1)
    return out


COND_KEYS = ["c1_red_k", "c2_volume", "c3_ma60_dev", "c4_week_k", "c5_week_k_up",
             "c6_ma20_lt_ma60", "c7_dk_gt_dd", "c8_vol_up"]
COND_LABEL = {"c1_red_k": "1 紅K棒幅", "c2_volume": "2 成交張數", "c3_ma60_dev": "3 季線乖離",
              "c4_week_k": "4 週K上限", "c5_week_k_up": "5 週K向上", "c6_ma20_lt_ma60": "6 MA20<MA60",
              "c7_dk_gt_dd": "7 日K>日D", "c8_vol_up": "8 量增"}


# ──────────────────────────────────────────────────────────────────────────────
# 資料
# ──────────────────────────────────────────────────────────────────────────────
def universe_markets() -> dict[str, str]:
    """{代碼: 上市／上櫃}（目前仍掛牌的普通股）。"""
    import twstock
    return {c: i.market for c, i in sorted(twstock.codes.items())
            if _COMMON.match(c) and i.type == "股票" and i.market in ("上市", "上櫃")}


def universe() -> list[str]:
    return sorted(universe_markets())


def load_prices(code: str, folder: Path | None = None) -> pd.DataFrame | None:
    f = (folder or PRICES) / f"{code}.csv.gz"
    if not f.exists() or f.stat().st_size < 60:
        return None
    df = pd.read_csv(f, parse_dates=["date"]).set_index("date").sort_index()
    df = df[(df["close"] > 0) & (df["open"] > 0) & (df["high"] > 0)]
    return df if len(df) else None


def _fetch_retry(dataset: str, code: str | None, start, end=None, label=""):
    import finmind_client as fc
    while True:
        try:
            return fc.fetch(dataset, code, start, end)
        except fc.FinMindError as e:
            if e.kind == "rate_limit":
                print(f"  FinMind 額度用完，{fc.QUOTA_COOLDOWN // 60} 分鐘後自動續抓…（{label}）", flush=True)
                time.sleep(fc.QUOTA_COOLDOWN)
                fc.reset_quota_block()
                continue
            raise


def fetch_all(start: str, refresh_days: int = 5) -> None:
    _load_token()
    PRICES.mkdir(parents=True, exist_ok=True)
    DIVS.mkdir(parents=True, exist_ok=True)
    codes = universe()
    today = date.today()
    todo = []
    for c in codes:
        f = PRICES / f"{c}.csv.gz"
        if not f.exists():
            todo.append(c)
        elif f.stat().st_size >= 60:
            last = pd.read_csv(f, usecols=["date"])["date"].max()
            if pd.Timestamp(last).date() < today - timedelta(days=refresh_days):
                todo.append(c)
    print(f"[1/3] 日線：股票池 {len(codes)} 檔，需抓 {len(todo)} 檔（已快取 {len(codes) - len(todo)} 檔）", flush=True)
    t0 = time.time()
    for i, c in enumerate(todo, 1):
        try:
            df = _fetch_retry("TaiwanStockPrice", c, start, today, c)
        except Exception as e:                       # noqa: BLE001
            print(f"  {c} 失敗：{e}", flush=True)
            continue
        if df.empty:
            (PRICES / f"{c}.csv.gz").write_bytes(b"")                    # 標記：沒有資料
        else:
            out = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})[
                ["date", "open", "high", "low", "close", "volume"]]
            out.to_csv(PRICES / f"{c}.csv.gz", index=False)
        if i % 50 == 0 or i == len(todo):
            el = time.time() - t0
            print(f"  {i}/{len(todo)}，已花 {el / 60:.0f} 分鐘", flush=True)

    print("[2/3] 面額變更／分割、減資（全市場各 1 次）", flush=True)
    for ds, fn in (("TaiwanStockSplitPrice", "split.csv"),
                   ("TaiwanStockCapitalReductionReferencePrice", "reduction.csv")):
        try:
            _fetch_retry(ds, None, start, today, ds).to_csv(CACHE / fn, index=False)
        except Exception as e:                       # noqa: BLE001
            print(f"  {ds} 失敗（報酬不還原此類事件）：{e}", flush=True)

    from local_screener import Screen103Params
    p = Screen103Params()
    sig_codes = []
    for c in codes:
        df = load_prices(c)
        if df is not None and len(df) > 61 and conditions_103(df, p)["signal"].any():
            sig_codes.append(c)
    todo = [c for c in sig_codes if not (DIVS / f"{c}.csv").exists()]
    print(f"[3/3] 除權息：有訊號的 {len(sig_codes)} 檔，需抓 {len(todo)} 檔", flush=True)
    for i, c in enumerate(todo, 1):
        try:
            _fetch_retry("TaiwanStockDividendResult", c, start, today, c).to_csv(DIVS / f"{c}.csv", index=False)
        except Exception as e:                       # noqa: BLE001
            print(f"  {c} 除權息失敗：{e}", flush=True)
        if i % 50 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}", flush=True)
    print("完成。接著執行：python backtest_103.py run", flush=True)


# ──────────────────────────────────────────────────────────────────────────────
# Yahoo Finance（yfinance）
#   Close／Open／High／Low：已做分割調整、未做除息調整；Adj Close：分割＋除息都調整。
#   代碼：上市 2330.TW、上櫃 6488.TWO。
# ──────────────────────────────────────────────────────────────────────────────
YAHOO_CHUNK = 80


def _yahoo_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """yf.download(group_by='ticker') 的結果 → 單一股票的小寫欄位表。"""
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return None
        df = raw[ticker]
    else:
        df = raw
    cols = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
            "Adj Close": "adj_close", "Volume": "volume"}
    if not set(cols) <= set(df.columns):
        return None
    out = df[list(cols)].rename(columns=cols).dropna(subset=["close"])
    out = out[(out["close"] > 0) & (out["volume"] > 0)]
    if out.empty:
        return None
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out.index.name = "date"
    return out


def fetch_yahoo(start: str) -> None:
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("沒有安裝 yfinance：請先執行  .venv-mcp\\Scripts\\pip install yfinance")
    PRICES_YAHOO.mkdir(parents=True, exist_ok=True)
    mk = universe_markets()
    todo = [c for c in mk if not (PRICES_YAHOO / f"{c}.csv.gz").exists()]
    print(f"[Yahoo] 股票池 {len(mk)} 檔，需抓 {len(todo)} 檔（每批 {YAHOO_CHUNK} 檔）", flush=True)
    t0, missing = time.time(), []
    for i in range(0, len(todo), YAHOO_CHUNK):
        batch = todo[i:i + YAHOO_CHUNK]
        tickers = {f"{c}.{'TW' if mk[c] == '上市' else 'TWO'}": c for c in batch}
        raw = None
        for attempt in range(3):
            try:
                raw = yf.download(list(tickers), start=start, auto_adjust=False, actions=False,
                                  group_by="ticker", threads=True, progress=False)
                break
            except Exception as e:                       # noqa: BLE001
                wait = 30 * (attempt + 1)
                print(f"  第 {i // YAHOO_CHUNK + 1} 批失敗（{e}），{wait} 秒後重試", flush=True)
                time.sleep(wait)
        if raw is None:
            print("  連續失敗，先停止；稍後重跑會從這裡續抓。", flush=True)
            return
        for t, c in tickers.items():
            df = _yahoo_frame(raw, t)
            if df is None:
                missing.append(c)
                (PRICES_YAHOO / f"{c}.csv.gz").write_bytes(b"")            # 標記：Yahoo 沒有資料
            else:
                df.reset_index().to_csv(PRICES_YAHOO / f"{c}.csv.gz", index=False)
        print(f"  {min(i + YAHOO_CHUNK, len(todo))}/{len(todo)}，已花 {(time.time() - t0) / 60:.1f} 分鐘", flush=True)
        time.sleep(2)
    print(f"完成。Yahoo 沒有資料：{len(missing)} 檔{('（例：' + '、'.join(missing[:10]) + '）') if missing else ''}")
    print("接著執行：python backtest_103.py compare（比對 FinMind 與 Yahoo）", flush=True)


def adjusted_yahoo(df: pd.DataFrame) -> pd.DataFrame:
    """Yahoo：用 Adj Close ÷ Close 的比值把開高低收一起做除息還原（分割已含在原始價裡）。"""
    f = (df["adj_close"] / df["close"]).astype(float)
    return pd.DataFrame({k: df[k].astype(float) * f for k in ("open", "high", "low", "close")}, index=df.index)


def compare_sources(tol: float = 0.005) -> pd.DataFrame:
    """比對 FinMind 與 Yahoo 重疊股票：逐日收盤價、成交量差異，以及「成交張數 ≥ 5000」判斷是否一致。"""
    rows = []
    for f in sorted(PRICES.glob("*.csv.gz")):
        code = f.name[:4]
        a, b = load_prices(code), load_prices(code, PRICES_YAHOO)
        if a is None or b is None:
            continue
        j = a.join(b, how="inner", lsuffix="_fm", rsuffix="_yh")
        if len(j) < 20:
            continue
        cdiff = (j["close_yh"] / j["close_fm"] - 1).abs()
        vr = j["volume_yh"] / j["volume_fm"]
        lots_fm, lots_yh = j["volume_fm"] / 1000 >= 5000, j["volume_yh"] / 1000 >= 5000
        rows.append({
            "代碼": code, "FinMind天數": len(a), "Yahoo天數": len(b), "重疊天數": len(j),
            "Yahoo缺的天數": int(len(a.index.difference(b.index))),
            "收盤差>0.5%的天數比例(%)": round(float((cdiff > tol).mean() * 100), 2),
            "成交量比(中位數)": round(float(vr.median()), 3),
            "成交量差>5%的天數比例(%)": round(float(((vr - 1).abs() > 0.05).mean() * 100), 2),
            "張數≥5000判斷不一致天數": int((lots_fm != lots_yh).sum()),
        })
    return pd.DataFrame(rows)


def _events_for(code: str, split: pd.DataFrame, red: pd.DataFrame) -> pd.DataFrame:
    rows = []
    f = DIVS / f"{code}.csv"
    if f.exists() and f.stat().st_size > 10:
        dv = pd.read_csv(f)
        for d, b, a in zip(dv["date"], dv["before_price"], dv["after_price"]):
            if b > 0 and a > 0:
                rows.append((pd.Timestamp(d), a / b))
    for src, bcol, acol in ((split, "before_price", "after_price"),
                            (red, "ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice")):
        if src is None or src.empty or bcol not in src.columns:
            continue
        s = src[src["stock_id"].astype(str) == code]
        for d, b, a in zip(s["date"], s[bcol], s[acol]):
            if b > 0 and a > 0:
                rows.append((pd.Timestamp(d), a / b))
    return pd.DataFrame(rows, columns=["date", "factor"])


def adjusted(df: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """open/high/low/close 向後還原（事件日之前 × factor）。"""
    out = df[["open", "high", "low", "close"]].astype(float).copy()
    if events is not None and len(events):
        for d, f in events.groupby("date")["factor"].prod().items():
            out.loc[out.index < d] *= f
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 交易模擬
# ──────────────────────────────────────────────────────────────────────────────
HOLDS = (1, 3, 5, 10, 20, 60)
RULES = [  # (名稱, 停損%, 停利%, 最長持有天數)
    ("持有5日", None, None, 5), ("持有10日", None, None, 10), ("持有20日", None, None, 20),
    ("停損5%＋持有10日", 5, None, 10), ("停損8%＋持有20日", 8, None, 20), ("停損10%＋持有20日", 10, None, 20),
    ("停損8%＋停利15%＋持有20日", 8, 15, 20), ("停損5%＋停利10%＋持有10日", 5, 10, 10),
    ("停損8%＋停利20%＋持有60日", 8, 20, 60),
]


def simulate_rule(adj: pd.DataFrame, e: int, stop, tp, hold: int):
    """e：進場列（隔日開盤買）。回傳 (淨報酬, 出場列, 出場原因)；資料不足回傳 None。"""
    o, h, l, c = (adj[k].to_numpy() for k in ("open", "high", "low", "close"))
    entry = o[e]
    last = e + hold - 1
    if last >= len(c):
        return None
    sp = entry * (1 - stop / 100) if stop else None
    tpp = entry * (1 + tp / 100) if tp else None
    for i in range(e, last + 1):
        if sp is not None and l[i] <= sp:                       # 同一天同時碰到停損與停利 → 保守算停損
            px = min(o[i], sp) if i > e else sp
            return px / entry - 1 - COST, i, "停損"
        if tpp is not None and h[i] >= tpp:
            px = max(o[i], tpp) if i > e else tpp
            return px / entry - 1 - COST, i, "停利"
    return c[last] / entry - 1 - COST, last, "到期"


def _stats(r: np.ndarray) -> dict:
    r = r[~np.isnan(r)]
    if len(r) == 0:
        return {"筆數": 0}
    win, loss = r[r > 0], r[r <= 0]
    pf = win.sum() / -loss.sum() if loss.sum() < 0 else None
    return {"筆數": int(len(r)), "勝率(%)": round(float((r > 0).mean() * 100), 1),
            "平均(%)": round(float(r.mean() * 100), 2), "中位數(%)": round(float(np.median(r) * 100), 2),
            "平均獲利(%)": round(float(win.mean() * 100), 2) if len(win) else None,
            "平均虧損(%)": round(float(loss.mean() * 100), 2) if len(loss) else None,
            "獲利因子": round(float(pf), 2) if pf else None,
            "最差(%)": round(float(r.min() * 100), 2), "最佳(%)": round(float(r.max() * 100), 2)}


YAHOO_JUMP = 0.105      # 台股漲跌幅 10%：還原後單日漲跌超過這個值，視為 Yahoo 分割／除權資料錯誤
TAINT_DAYS = 60         # 疑似錯誤日前後 60 個交易日內的訊號、基準報酬都剔除


def yahoo_taint(adj_close: pd.Series, skip_first: int = 5, days: int = TAINT_DAYS) -> np.ndarray:
    """回傳布林陣列：True＝該日前後 days 日內有不合理跳動（上市前 skip_first 日無漲跌幅限制，不算）。"""
    r = adj_close.astype(float).pct_change().abs().to_numpy()
    bad = np.zeros(len(r), bool)
    bad[skip_first:] = r[skip_first:] > YAHOO_JUMP
    out = np.zeros(len(r), bool)
    for i in np.nonzero(bad)[0]:
        out[max(0, i - days): i + days + 1] = True
    return out


def run(start: str, end: str | None = None, source: str = "finmind",
        codes: set[str] | None = None, tag: str = "") -> dict:
    from local_screener import Screen103Params
    p = Screen103Params()
    folder = PRICES_YAHOO if source == "yahoo" else PRICES
    out_dir = OUT / (source + tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    split = pd.read_csv(CACHE / "split.csv") if (CACHE / "split.csv").exists() else None
    red = pd.read_csv(CACHE / "reduction.csv") if (CACHE / "reduction.csv").exists() else None
    t_start, t_end = pd.Timestamp(start), pd.Timestamp(end) if end else pd.Timestamp.max

    trades, bench_parts, ablation_rows = [], [], []
    n_stock = n_skip_limit = n_taint = n_taint_stock = 0
    for f in sorted(folder.glob("*.csv.gz")):
        code = f.name[:4]
        if codes is not None and code not in codes:
            continue
        raw = load_prices(code, folder)
        if raw is None or len(raw) < 130:
            continue
        n_stock += 1
        cond = conditions_103(raw, p)
        adj = adjusted_yahoo(raw) if source == "yahoo" else adjusted(raw, _events_for(code, split, red))
        taint = yahoo_taint(raw["adj_close"]) if source == "yahoo" else np.zeros(len(raw), bool)
        n_taint_stock += int(taint.any())
        ao, ac = adj["open"].to_numpy(), adj["close"].to_numpy()
        rc, ro = raw["close"].to_numpy(), raw["open"].to_numpy()
        dates = raw.index
        # 基準：每天收盤後「買全部」→ 隔日開盤進場、持有 N 日（等權重平均在後面按日期彙總）
        b = {}
        for H in (5, 10, 20, 60):
            fwd = np.full(len(ac), np.nan)                        # 索引＝訊號日 t；進場 t+1 開盤、出場 t+H 收盤
            ent = np.arange(1, len(ac))
            ex = ent + H - 1
            ok = ex < len(ac)
            fwd[ent[ok] - 1] = ac[ex[ok]] / ao[ent[ok]] - 1 - COST
            fwd[taint] = np.nan
            b[H] = fwd
        bench_parts.append(pd.DataFrame({f"b{H}": b[H] for H in b}, index=dates))
        # 條件拆解：拿掉某一條件時的訊號
        cm = cond[COND_KEYS].to_numpy()
        for drop in [None] + COND_KEYS:
            mask = np.ones(len(cond), bool)
            for j, k in enumerate(COND_KEYS):
                if k != drop:
                    mask &= cm[:, j]
            for t in np.nonzero(mask & ~taint)[0]:
                if not (t_start <= dates[t] <= t_end) or t + 1 >= len(dates):
                    continue
                if ro[t + 1] >= rc[t] * LIMIT_UP_OPEN:
                    continue
                r10 = ac[t + 10] / ao[t + 1] - 1 - COST if t + 10 < len(ac) else np.nan
                r20 = ac[t + 20] / ao[t + 1] - 1 - COST if t + 20 < len(ac) else np.nan
                ablation_rows.append((drop or "原始 8 條件", code, dates[t], r10, r20))
        # 正式交易
        for t in np.nonzero(cond["signal"].to_numpy())[0]:
            if not (t_start <= dates[t] <= t_end) or t + 1 >= len(dates):
                continue
            if taint[t]:
                n_taint += 1
                continue
            if ro[t + 1] >= rc[t] * LIMIT_UP_OPEN:
                n_skip_limit += 1
                continue
            e = t + 1
            rec = {"代碼": code, "訊號日": dates[t].date(), "進場日": dates[e].date(), "進場價(原始)": ro[e]}
            for H in HOLDS:
                x = e + H - 1
                rec[f"持有{H}日(%)"] = (ac[x] / ao[e] - 1 - COST) * 100 if x < len(ac) else np.nan
            w = slice(e, min(e + 20, len(ac)))
            rec["20日內最大浮虧(%)"] = (adj["low"].to_numpy()[w].min() / ao[e] - 1) * 100
            rec["20日內最大浮盈(%)"] = (adj["high"].to_numpy()[w].max() / ao[e] - 1) * 100
            for name, s, tp, hold in RULES:
                res = simulate_rule(adj, e, s, tp, hold)
                rec[name] = res[0] * 100 if res else np.nan
                rec[name + "_出場"] = res[2] if res else None
                rec[name + "_天數"] = (res[1] - e + 1) if res else None
            for k in ("red_k", "vol_lots", "ma60_dev", "day_k", "day_d", "week_k"):
                rec[k] = cond[k].iloc[t]
            trades.append(rec)

    T = pd.DataFrame(trades)
    B = pd.concat(bench_parts).groupby(level=0).mean()               # 每個訊號日：全市場等權重
    B = B[(B.index >= t_start) & (B.index <= t_end)]
    T.to_csv(out_dir / "trades.csv", index=False, encoding="utf-8-sig")
    summary = {"資料來源": source, "期間": [start, end or str(B.index.max().date())], "股票池": n_stock,
               "訊號數": int(len(T) + n_skip_limit + n_taint), "開盤漲停買不到": n_skip_limit,
               "資料疑似錯誤剔除": n_taint, "有疑似錯誤的股票數": n_taint_stock, "可交易筆數": int(len(T)),
               "成本": "來回 0.585%", "params": asdict(p)}
    if T.empty:
        json.dump(summary, open(out_dir / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return summary
    T["訊號日"] = pd.to_datetime(T["訊號日"])
    bmap = B.reindex(T["訊號日"])
    hold_tbl = {}
    for H in HOLDS:
        r = T[f"持有{H}日(%)"].to_numpy() / 100
        row = _stats(r)
        if f"b{H}" in B.columns:
            bm = bmap[f"b{H}"].to_numpy()
            ok = ~np.isnan(r) & ~np.isnan(bm)
            row["同日全市場平均(%)"] = round(float(bm[ok].mean() * 100), 2)
            row["超額報酬平均(%)"] = round(float((r[ok] - bm[ok]).mean() * 100), 2)
            row["贏過全市場比例(%)"] = round(float((r[ok] > bm[ok]).mean() * 100), 1)
        hold_tbl[f"持有{H}日"] = row
    summary["固定持有"] = hold_tbl
    summary["全市場基準(每天買進)"] = {f"持有{H}日": _stats(B[f"b{H}"].to_numpy()) for H in (5, 10, 20, 60)}
    rule_tbl = {}
    for name, *_ in RULES:
        row = _stats(T[name].to_numpy() / 100)
        row["出場原因"] = T[name + "_出場"].value_counts().to_dict()
        row["平均持有天數"] = round(float(T[name + "_天數"].mean()), 1)
        rule_tbl[name] = row
    summary["出場規則"] = rule_tbl
    T["年"] = T["訊號日"].dt.year
    summary["逐年(持有10日)"] = {int(y): _stats(g["持有10日(%)"].to_numpy() / 100) for y, g in T.groupby("年")}
    summary["逐年(停損8%＋持有20日)"] = {int(y): _stats(g["停損8%＋持有20日"].to_numpy() / 100)
                                     for y, g in T.groupby("年")}
    summary["20日內最大浮虧"] = {"平均(%)": round(float(T["20日內最大浮虧(%)"].mean()), 2),
                             "中位數(%)": round(float(T["20日內最大浮虧(%)"].median()), 2),
                             "跌超過8%的比例(%)": round(float((T["20日內最大浮虧(%)"] <= -8).mean() * 100), 1)}
    A = pd.DataFrame(ablation_rows, columns=["版本", "代碼", "訊號日", "r10", "r20"])
    abl = {}
    for ver in ["原始 8 條件"] + COND_KEYS:                       # 原始版本排第一列
        g = A[A["版本"] == ver]
        if g.empty:
            continue
        bm10 = B.reindex(g["訊號日"])["b10"].to_numpy()
        r10 = g["r10"].to_numpy()
        ok = ~np.isnan(r10) & ~np.isnan(bm10)
        label = ver if ver == "原始 8 條件" else "拿掉 " + COND_LABEL[ver]
        abl[label] = {"訊號數": int(len(g)), **{k: v for k, v in _stats(r10).items() if k in ("勝率(%)", "平均(%)", "中位數(%)")},
                      "10日超額平均(%)": round(float((r10[ok] - bm10[ok]).mean() * 100), 2) if ok.any() else None}
    summary["條件拆解(持有10日)"] = abl
    # 投組模擬：每筆訊號等權重、同時持有不限檔數；沒有部位的日子報酬 0
    summary["投組模擬"] = {name: portfolio(T, name) for name in ("持有10日", "停損8%＋持有20日")}
    json.dump(summary, open(out_dir / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    return summary


def portfolio(T: pd.DataFrame, rule: str) -> dict:
    """用每筆交易的進出場日與報酬，近似成等權重投組（報酬在持有期間平均分攤）。"""
    days = T[rule + "_天數"] if rule + "_天數" in T else None
    if days is None:                                                      # 固定持有欄位
        H = int(re.search(r"(\d+)", rule).group(1))
        ret, nd = T[f"持有{H}日(%)"] / 100, pd.Series(H, index=T.index)
    else:
        ret, nd = T[rule] / 100, days
    ok = ret.notna() & nd.notna()
    idx = pd.bdate_range(T["進場日"].min(), pd.Timestamp(T["進場日"].max()) + pd.Timedelta(days=120))
    daily_sum = pd.Series(0.0, index=idx)
    count = pd.Series(0.0, index=idx)
    for (ent, r, n) in zip(pd.to_datetime(T.loc[ok, "進場日"]), ret[ok], nd[ok]):
        rng = pd.bdate_range(ent, periods=int(n))
        g = (1 + r) ** (1 / n) - 1
        daily_sum[rng] += g
        count[rng] += 1
    port = (daily_sum / count.replace(0, np.nan)).fillna(0)
    eq = (1 + port).cumprod()
    yrs = len(port) / 252
    mdd = float((eq / eq.cummax() - 1).min())
    return {"年化報酬(%)": round(float(eq.iloc[-1] ** (1 / yrs) - 1) * 100, 1) if yrs > 0 else None,
            "最大回撤(%)": round(mdd * 100, 1), "有持股的天數比例(%)": round(float((count > 0).mean() * 100), 1),
            "平均同時持股數": round(float(count[count > 0].mean()), 1)}


def main():
    ap = argparse.ArgumentParser(description="我的選股103 回測")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="抓日線（需網路）")
    f.add_argument("--start", default="2018-06-01")
    f.add_argument("--source", choices=["finmind", "yahoo"], default="finmind")
    sub.add_parser("compare", help="比對 FinMind 與 Yahoo 重疊股票的資料差異（離線）")
    r = sub.add_parser("run", help="離線回測")
    r.add_argument("--start", default="2019-01-01")
    r.add_argument("--end", default=None)
    r.add_argument("--source", choices=["finmind", "yahoo"], default="finmind")
    r.add_argument("--overlap", action="store_true", help="只跑 FinMind 也有抓到的股票（用來比對兩個來源的回測結果）")
    a = ap.parse_args()
    if a.cmd == "fetch":
        fetch_yahoo(a.start) if a.source == "yahoo" else fetch_all(a.start)
    elif a.cmd == "compare":
        df = compare_sources()
        OUT.mkdir(parents=True, exist_ok=True)
        df.to_csv(OUT / "compare_sources.csv", index=False, encoding="utf-8-sig")
        print(f"重疊 {len(df)} 檔；結果：{OUT / 'compare_sources.csv'}")
        if len(df):
            print(df.describe().round(3).to_string())
    else:
        codes = {f.name[:4] for f in PRICES.glob("*.csv.gz") if f.stat().st_size > 10} if a.overlap else None
        tag = "_overlap" if a.overlap else ""
        s = run(a.start, a.end, a.source, codes=codes, tag=tag)
        print(json.dumps({k: s[k] for k in ("資料來源", "期間", "股票池", "訊號數", "可交易筆數")}, ensure_ascii=False))
        print(f"結果：{OUT / (a.source + tag)}")


if __name__ == "__main__":
    main()
