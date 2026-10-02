# local_screener.py
"""
本機計算「我的選股103」，取代 Goodinfo 爬蟲（scraper.scrape_goodinfo）。

背景：Goodinfo 自 2026-09 起加上 Cloudflare JS Challenge，cf_clearance 綁定 IP / UA /
指紋，Streamlit Cloud（美國機房）無法使用從台灣瀏覽器複製的 Cookie。改為自行計算。

兩階段：
  1. 粗篩（全市場，2 個交易日的收盤行情）：條件 1 紅K棒幅、2 成交張數、8 量增
     資料來源：TWSE + TPEx 官方每日收盤行情；失敗時改用 FinMind 全市場日資料
     （FinMind 全市場查詢需 backer / sponsor 權限）。
  2. 細篩（候選股逐檔抓 FinMind 日線）：條件 3 季線乖離、4/5 週K、6 月/季空頭排列、7 日K>日D

KD 採台灣慣用的遞迴式（Goodinfo 同此算法）：
    RSV = (C - L9) / (H9 - L9) * 100
    K   = K_prev * 2/3 + RSV * 1/3
    D   = D_prev * 2/3 + K   * 1/3     （初始 K = D = 50）
tw_kd 放在 indicators.py，stock_analyzer 的個股 KD 也改用同一個算法。

用法：
    from local_screener import scrape_local_103
    df = scrape_local_103()            # 欄位與 scrape_goodinfo() 相同；失敗時 raise

    python local_screener.py                       # 最新交易日
    python local_screener.py --date 2026-10-01     # 指定日期（供與 Goodinfo 歷史結果比對）
    python local_screener.py --base open --detail out.csv
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import numpy as np
import pandas as pd
import requests

from indicators import tw_kd  # noqa: F401  （台灣遞迴 KD，與 stock_analyzer 共用）

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# 與 scrape_goodinfo() 相同的輸出欄位，streamlit_app 可直接沿用
OUTPUT_COLUMNS = ["代碼", "名稱", "市場", "股價日期", "成交", "漲跌價", "漲跌幅", "成交張數"]


class ScreenerError(RuntimeError):
    """選股失敗（請勿回傳 None，避免被 st.cache_data 快取住失敗結果）。"""


# ---------------------------------------------------------------------------
# 參數（可接到側邊欄滑桿）
# ---------------------------------------------------------------------------
@dataclass
class Screen103Params:
    red_k_min: float = 2.5           # 1. 紅K棒幅 (%)
    red_k_max: float = 10.0
    red_k_base: str = "prev_close"   #    分母：'prev_close'（昨收）或 'open'（開盤）— 待與 Goodinfo 校正
    vol_min: float = 5_000           # 2. 成交張數
    vol_max: float = 900_000
    ma60_dev_min: float = -5.0       # 3. 季線乖離 (%)
    ma60_dev_max: float = 5.0
    wk_min: float = 0.0              # 4. 週K值
    wk_max: float = 50.0
    require_wk_up: bool = True       # 5. 週K值向上
    require_ma20_lt_ma60: bool = True  # 6. 月/季線空頭排列（MA20 < MA60）
    require_dk_gt_dd: bool = True    # 7. 日K > 日D
    vol_ratio: float = 1.3           # 8. 今日量 > vol_ratio × 昨日量
    kd_period: int = 9               # KD 參數
    history_days: int = 420          # 細篩抓多少日曆天的日線（週KD 收斂需要足夠長度）


@dataclass
class ScreenResult:
    matches: pd.DataFrame            # 符合全部條件，欄位同 OUTPUT_COLUMNS（+ 指標欄）
    detail: pd.DataFrame             # 所有粗篩候選股的指標值與逐條件判斷（驗證／校正用）
    trade_date: date
    prev_date: date
    source: str                      # 'twse+tpex' 或 'finmind'
    universe_size: int               # 粗篩前的股票數
    errors: dict = field(default_factory=dict)   # 細篩抓資料失敗的代碼 → 訊息


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
_COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")   # 4 碼、非 0 開頭 → 排除 ETF / 權證 / 特別股


def _num(x) -> float:
    """'1,234.5' → 1234.5；'--'、''、'除權息' 等 → NaN。"""
    if x is None:
        return np.nan
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"<[^>]*>", "", str(x)).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def _to_date(d) -> date:
    if d is None:
        return date.today()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d), "%Y-%m-%d").date()


def _get_json(url: str, params: dict | None = None, timeout: int = 20, retries: int = 3) -> dict:
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout)
            if r.status_code == 429 and i < retries - 1:
                time.sleep(2 ** i)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if i < retries - 1:
                time.sleep(1 + i)
    raise ScreenerError(f"連線失敗 {url}: {last}")


def _finmind(params: dict) -> pd.DataFrame:
    token = os.getenv("FINMIND_API_TOKEN")
    headers = {"User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last = None
    for i in range(3):
        try:
            r = requests.get(FINMIND_URL, params=params, headers=headers, timeout=30)
            if r.status_code == 429 and i < 2:
                time.sleep(2 ** (i + 1))
                continue
            r.raise_for_status()
            js = r.json()
            if js.get("status") != 200:
                raise ScreenerError(f"FinMind 錯誤: {js.get('msg') or js.get('error_message') or js}")
            return pd.DataFrame(js.get("data") or [])
        except ScreenerError:
            raise
        except (requests.RequestException, ValueError) as e:
            last = e
            if i < 2:
                time.sleep(1 + i)
    raise ScreenerError(f"FinMind 連線失敗: {last}")


# ---------------------------------------------------------------------------
# 粗篩資料來源 1：TWSE + TPEx 官方每日收盤行情
# 統一輸出欄位：code, name, market, open, high, low, close, change, volume(股)
# ---------------------------------------------------------------------------
def parse_twse_mi_index(js: dict) -> pd.DataFrame:
    """解析 TWSE MI_INDEX（type=ALLBUT0999）。支援新版 tables[] 與舊版 fieldsN/dataN。"""
    tables = []
    for t in js.get("tables") or []:
        tables.append((t.get("fields") or [], t.get("data") or []))
    for k, v in js.items():                       # 舊版：fields9 / data9 …
        m = re.fullmatch(r"fields(\d+)", k)
        if m:
            tables.append((v, js.get(f"data{m.group(1)}") or []))

    for fields, data in tables:
        if "證券代號" in fields and "收盤價" in fields and data:
            idx = {f: i for i, f in enumerate(fields)}
            sign_col = next((f for f in fields if f.startswith("漲跌(")), None)
            rows = []
            for r in data:
                chg = _num(r[idx["漲跌價差"]]) if "漲跌價差" in idx else np.nan
                if sign_col is not None and not np.isnan(chg):
                    sign = re.sub(r"<[^>]*>", "", str(r[idx[sign_col]])).strip()
                    if sign == "-":
                        chg = -chg
                    elif sign.upper() == "X":       # 不比價（除權息首日等）
                        chg = np.nan
                rows.append({
                    "code": str(r[idx["證券代號"]]).strip(),
                    "name": str(r[idx["證券名稱"]]).strip(),
                    "market": "上市",
                    "open": _num(r[idx["開盤價"]]),
                    "high": _num(r[idx["最高價"]]),
                    "low": _num(r[idx["最低價"]]),
                    "close": _num(r[idx["收盤價"]]),
                    "change": chg,
                    "volume": _num(r[idx["成交股數"]]),
                })
            return pd.DataFrame(rows)
    return pd.DataFrame()


_TPEX_POSITIONAL = ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數"]


def parse_tpex_daily(js: dict) -> pd.DataFrame:
    """解析 TPEx 上櫃每日收盤行情。支援新版 tables[] 與舊版 aaData。"""
    candidates = []
    for t in js.get("tables") or []:
        candidates.append((t.get("fields") or [], t.get("data") or []))
    if js.get("aaData"):
        candidates.append((_TPEX_POSITIONAL, js["aaData"]))

    for fields, data in candidates:
        fields = [str(f).replace(" ", "") for f in fields]
        if "代號" in fields and "收盤" in fields and data:
            idx = {f: i for i, f in enumerate(fields)}
            rows = []
            for r in data:
                rows.append({
                    "code": str(r[idx["代號"]]).strip(),
                    "name": str(r[idx["名稱"]]).strip(),
                    "market": "上櫃",
                    "open": _num(r[idx["開盤"]]),
                    "high": _num(r[idx["最高"]]),
                    "low": _num(r[idx["最低"]]),
                    "close": _num(r[idx["收盤"]]),
                    "change": _num(r[idx["漲跌"]]),
                    "volume": _num(r[idx["成交股數"]]),
                })
            return pd.DataFrame(rows)
    return pd.DataFrame()


def fetch_twse_day(d: date) -> pd.DataFrame:
    js = _get_json("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
                   {"date": d.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"})
    return parse_twse_mi_index(js)


def fetch_tpex_day(d: date) -> pd.DataFrame:
    try:
        js = _get_json("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQ",
                       {"date": d.strftime("%Y/%m/%d"), "response": "json"})
        df = parse_tpex_daily(js)
        if not df.empty:
            return df
    except ScreenerError:
        pass
    roc = f"{d.year - 1911}/{d:%m/%d}"                 # 舊版端點（民國日期）
    js = _get_json("https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php",
                   {"l": "zh-tw", "d": roc, "o": "json"})
    return parse_tpex_daily(js)


def _official_market_day(d: date) -> pd.DataFrame:
    twse = fetch_twse_day(d)
    if twse.empty:
        return twse                                   # 休市
    time.sleep(0.5)
    tpex = fetch_tpex_day(d)
    if tpex.empty:
        raise ScreenerError(f"TWSE 有 {d} 的資料，但 TPEx 沒有（端點可能改版）")
    return pd.concat([twse, tpex], ignore_index=True)


# ---------------------------------------------------------------------------
# 粗篩資料來源 2：FinMind 全市場（需 backer / sponsor）
# ---------------------------------------------------------------------------
def _finmind_market_window(end: date, lookback: int = 14) -> pd.DataFrame:
    df = _finmind({"dataset": "TaiwanStockPrice",
                   "start_date": (end - timedelta(days=lookback)).isoformat(),
                   "end_date": end.isoformat()})
    if df.empty:
        raise ScreenerError("FinMind 全市場查詢無資料（可能需要 backer / sponsor 方案）")
    info = _finmind({"dataset": "TaiwanStockInfo"})
    info = info.drop_duplicates("stock_id")
    mkt = info.set_index("stock_id")["type"].map({"twse": "上市", "tpex": "上櫃"})
    names = info.set_index("stock_id")["stock_name"]
    df = df.rename(columns={"stock_id": "code", "max": "high", "min": "low",
                            "spread": "change", "Trading_Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["name"] = df["code"].map(names)
    df["market"] = df["code"].map(mkt)
    return df[df["market"].notna()]


# ---------------------------------------------------------------------------
# 粗篩：組出 [今日, 昨日] 的全市場資料
# ---------------------------------------------------------------------------
def load_two_days(trade_date: date | None, source: str = "auto",
                  max_back: int = 12) -> tuple[pd.DataFrame, date, date, str]:
    """
    回傳 (df, 今日, 昨日, 來源)。df 每列一檔：
      code, name, market, open, high, low, close, change, volume, prev_close, prev_volume
    trade_date 為 None 時取最近一個有資料的交易日。
    """
    start = _to_date(trade_date)
    errors = []

    if source in ("auto", "official"):
        try:
            found = []
            d = start
            for _ in range(max_back):
                df = _official_market_day(d)
                if not df.empty:
                    found.append((d, df))
                    if len(found) == 2:
                        break
                d -= timedelta(days=1)
                time.sleep(0.5)
            if len(found) < 2:
                raise ScreenerError(f"{max_back} 天內找不到兩個交易日的官方資料")
            (d0, today), (d1, prev) = found
            return _merge_two(today, prev), d0, d1, "twse+tpex"
        except ScreenerError as e:
            errors.append(f"官方來源：{e}")
            if source == "official":
                raise

    if source in ("auto", "finmind"):
        try:
            df = _finmind_market_window(start, lookback=max_back + 2)
            days = sorted(df["date"].unique())
            if len(days) < 2:
                raise ScreenerError("FinMind 回傳不足兩個交易日")
            d0, d1 = days[-1], days[-2]
            today = df[df["date"] == d0]
            prev = df[df["date"] == d1]
            return _merge_two(today, prev), d0, d1, "finmind"
        except ScreenerError as e:
            errors.append(f"FinMind：{e}")

    raise ScreenerError("粗篩資料取得失敗 — " + "；".join(errors))


def _merge_two(today: pd.DataFrame, prev: pd.DataFrame) -> pd.DataFrame:
    cols = ["code", "name", "market", "open", "high", "low", "close", "change", "volume"]
    t = today[cols].copy()
    p = prev[["code", "close", "volume"]].rename(columns={"close": "prev_close_raw",
                                                          "volume": "prev_volume"})
    m = t.merge(p, on="code", how="inner")
    m = m[m["code"].astype(str).str.match(_COMMON_STOCK)]
    # 昨收優先用「今收 − 漲跌」（= 參考價，除權息日也正確），否則用昨日收盤
    ref = m["close"] - m["change"]
    m["prev_close"] = ref.where(ref.notna() & (ref > 0), m["prev_close_raw"])
    m["change"] = m["change"].where(m["change"].notna(), m["close"] - m["prev_close"])
    return m.drop(columns="prev_close_raw").reset_index(drop=True)


def red_k_pct(open_, close, prev_close, base: str) -> pd.Series | float:
    """紅K棒幅 (%) = (收 − 開) / 分母 × 100；黑K（收 ≤ 開）為負值。"""
    denom = open_ if base == "open" else prev_close
    return (close - open_) / denom * 100


def coarse_screen(mkt: pd.DataFrame, p: Screen103Params) -> pd.DataFrame:
    """條件 1、2、8。回傳附上計算欄位的候選股。"""
    df = mkt.dropna(subset=["open", "close", "prev_close", "volume", "prev_volume"]).copy()
    df["red_k"] = red_k_pct(df["open"], df["close"], df["prev_close"], p.red_k_base)
    df["vol_lots"] = df["volume"] / 1000
    df["prev_vol_lots"] = df["prev_volume"] / 1000
    c1 = df["red_k"].between(p.red_k_min, p.red_k_max)
    c2 = df["vol_lots"].between(p.vol_min, p.vol_max)
    c8 = df["vol_lots"] > p.vol_ratio * df["prev_vol_lots"]
    return df[c1 & c2 & c8].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 指標
# ---------------------------------------------------------------------------
def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """日線（index 為 DatetimeIndex）→ 週線；本週未收完也算一根（同看盤軟體）。"""
    w = daily.resample("W-FRI").agg({"open": "first", "high": "max",
                                     "low": "min", "close": "last"})
    return w.dropna(subset=["close"])


def evaluate_103(daily: pd.DataFrame, p: Screen103Params) -> dict:
    """
    對單一股票日線（欄位 open/high/low/close/volume[股]，DatetimeIndex，最後一列 = 選股日）
    計算條件 3–7 所需指標與判斷結果。
    """
    d = daily.sort_index()
    if len(d) < 61:
        raise ValueError(f"日線不足 61 根（{len(d)}）")
    close = d["close"]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    dk, dd = tw_kd(d["high"], d["low"], close, p.kd_period)
    w = to_weekly(d)
    wk, wd = tw_kd(w["high"], w["low"], w["close"], p.kd_period)
    t = close.iloc[-1]

    out = {
        "ma20": ma20, "ma60": ma60,
        "ma60_dev": (t / ma60 - 1) * 100,
        "day_k": dk.iloc[-1], "day_d": dd.iloc[-1],
        "week_k": wk.iloc[-1],
        "week_k_prev": wk.iloc[-2] if len(wk) >= 2 else np.nan,
        "week_d": wd.iloc[-1],
    }
    out["c3_ma60_dev"] = bool(p.ma60_dev_min <= out["ma60_dev"] <= p.ma60_dev_max)
    out["c4_week_k"] = bool(p.wk_min <= out["week_k"] <= p.wk_max)
    out["c5_week_k_up"] = (not p.require_wk_up) or bool(out["week_k"] > out["week_k_prev"])
    out["c6_ma20_lt_ma60"] = (not p.require_ma20_lt_ma60) or bool(ma20 < ma60)
    out["c7_dk_gt_dd"] = (not p.require_dk_gt_dd) or bool(out["day_k"] > out["day_d"])
    out["match"] = all(out[c] for c in ("c3_ma60_dev", "c4_week_k", "c5_week_k_up",
                                         "c6_ma20_lt_ma60", "c7_dk_gt_dd"))
    return out


# ---------------------------------------------------------------------------
# 細篩：逐檔抓 FinMind 日線
# ---------------------------------------------------------------------------
def fetch_daily_history(code: str, end: date, days: int) -> pd.DataFrame:
    df = _finmind({"dataset": "TaiwanStockPrice", "data_id": code,
                   "start_date": (end - timedelta(days=days)).isoformat(),
                   "end_date": end.isoformat()})
    if df.empty:
        raise ScreenerError(f"FinMind 無 {code} 日線")
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce")
    # FinMind 暫停交易日會給 0 價，剔除
    return df[(df["close"] > 0) & (df["high"] > 0)].dropna()


def _align_to_trade_date(hist: pd.DataFrame, row: pd.Series, trade_date: date) -> pd.DataFrame:
    """日線截到選股日；若 FinMind 尚未更新當日資料，用粗篩的當日行情補一根。"""
    ts = pd.Timestamp(trade_date)
    hist = hist[hist.index <= ts]
    if hist.empty or hist.index[-1] < ts:
        bar = pd.DataFrame({"open": [row["open"]], "high": [row["high"]], "low": [row["low"]],
                            "close": [row["close"]], "volume": [row["volume"]]}, index=[ts])
        hist = pd.concat([hist, bar])
    return hist


def fine_screen(cands: pd.DataFrame, trade_date: date, p: Screen103Params,
                max_workers: int = 4,
                progress_cb: Callable[[int, int, str], None] | None = None
                ) -> tuple[pd.DataFrame, dict]:
    rows, errors = [], {}
    total = len(cands)

    def work(row):
        hist = fetch_daily_history(row["code"], trade_date, p.history_days)
        hist = _align_to_trade_date(hist, row, trade_date)
        return evaluate_103(hist, p)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(work, r): r for _, r in cands.iterrows()}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            try:
                rows.append({**r.to_dict(), **f.result()})
            except Exception as e:                       # noqa: BLE001
                errors[r["code"]] = f"{type(e).__name__}: {e}"
            if progress_cb:
                progress_cb(i, total, r["code"])
    detail = pd.DataFrame(rows)
    if not detail.empty:
        detail = detail.sort_values("code").reset_index(drop=True)
    return detail, errors


# ---------------------------------------------------------------------------
# 對外介面
# ---------------------------------------------------------------------------
def screen_103(trade_date=None, params: Screen103Params | None = None, source: str = "auto",
               max_workers: int = 4,
               progress_cb: Callable[[int, int, str], None] | None = None) -> ScreenResult:
    p = params or Screen103Params()
    mkt, d0, d1, src = load_two_days(trade_date, source)
    cands = coarse_screen(mkt, p)
    n_mkt = mkt["market"].value_counts().to_dict()
    n_cand = cands["market"].value_counts().to_dict() if len(cands) else {}
    print(f"[103] {d0}（前一交易日 {d1}，來源 {src}）："
          f"全市場 {len(mkt)} 檔（上市 {n_mkt.get('上市', 0)}／上櫃 {n_mkt.get('上櫃', 0)}）"
          f" → 粗篩 {len(cands)} 檔（上市 {n_cand.get('上市', 0)}／上櫃 {n_cand.get('上櫃', 0)}）")
    if not n_mkt.get("上櫃"):
        raise ScreenerError("全市場資料中沒有上櫃股票，TPEx 來源可能失效")

    detail, errors = fine_screen(cands, d0, p, max_workers, progress_cb) if len(cands) else (pd.DataFrame(), {})
    if errors:
        print(f"[103] 細篩失敗 {len(errors)} 檔：{list(errors)[:10]}")

    if detail.empty:
        matches = pd.DataFrame(columns=OUTPUT_COLUMNS)
    else:
        hit = detail[detail["match"]].copy()
        matches = pd.DataFrame({
            "代碼": hit["code"],
            "名稱": hit["name"],
            "市場": hit["market"],
            "股價日期": d0.strftime("%Y/%m/%d"),
            "成交": hit["close"],
            "漲跌價": hit["change"].round(2),
            "漲跌幅": (hit["change"] / hit["prev_close"] * 100).round(2),
            "成交張數": hit["vol_lots"].round(0).astype(int),
            "紅K棒幅": hit["red_k"].round(2),
            "季線乖離": hit["ma60_dev"].round(2),
            "日K": hit["day_k"].round(2),
            "日D": hit["day_d"].round(2),
            "週K": hit["week_k"].round(2),
        }).reset_index(drop=True)
    print(f"[103] 符合全部條件：{len(matches)} 檔")
    return ScreenResult(matches, detail, d0, d1, src, len(mkt), errors)


def scrape_local_103(params: Screen103Params | None = None, **kw) -> pd.DataFrame:
    """
    scrape_goodinfo() 的替代品：回傳相同欄位的 DataFrame。
    失敗時 raise ScreenerError（不回傳 None），避免 st.cache_data 快取住失敗結果。
    若細篩有一半以上抓資料失敗，也視為失敗。
    """
    res = screen_103(params=params, **kw)
    n_cand = len(res.detail) + len(res.errors)
    if n_cand and len(res.errors) * 2 > n_cand:
        raise ScreenerError(f"細篩失敗過多（{len(res.errors)}/{n_cand}），可能是 FinMind 限流")
    return res.matches


def _main():
    ap = argparse.ArgumentParser(description="本機計算 Goodinfo「我的選股103」")
    ap.add_argument("--date", help="選股日 YYYY-MM-DD（預設今天，遇休市自動往前）")
    ap.add_argument("--base", choices=["prev_close", "open"], default="prev_close",
                    help="紅K棒幅分母")
    ap.add_argument("--source", choices=["auto", "official", "finmind"], default="auto")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--detail", help="輸出所有候選股的指標與逐條件判斷 CSV")
    ap.add_argument("--csv", help="輸出符合結果 CSV")
    a = ap.parse_args()

    p = Screen103Params(red_k_base=a.base)
    res = screen_103(a.date, p, a.source, a.workers)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    print(res.matches.to_string(index=False) if not res.matches.empty else "（無符合股票）")
    if a.detail and not res.detail.empty:
        res.detail.to_csv(a.detail, index=False, encoding="utf-8-sig")
        print(f"候選股明細 → {a.detail}")
    if a.csv:
        res.matches.to_csv(a.csv, index=False, encoding="utf-8-sig")
        print(f"結果 → {a.csv}")
    if res.errors:
        print("細篩失敗：", res.errors)


if __name__ == "__main__":
    _main()
