# finmind_my_stock_scraper.py
# 用途：取代 scraper.py，從 FinMind API 實作「我的選股103」的篩選邏輯
# 原 Goodinfo 篩選條件：
#   - 當日：紅K棒幅 2.5%~10%
#   - 成交張數 5000~900000 張
#   - 股價與季線乖離 -5%~5%（近季線）
#   - 週KD 金叉向上 + K值 0~50
#   - 月線/季線空頭排列（月>季）
# 全部改為從 FinMind TaiwanStockPrice / TaiwanStockKBar 計算

import os
import sys
import requests
import pandas as pd
import numpy as np
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import twstock

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── FinMind API 基礎設定 ──────────────────────────────────────
FINMIND_URL   = "https://api.finmindtrade.com/api/v4/data"
FINMIND_FILTER_URL = "https://api.finmindtrade.com/api/v4/taiwan_stock_filter"


def _get_token() -> dict:
    """回傳 FinMind Authorization header（有 token 時使用，無則匿名）"""
    token = os.getenv("FINMIND_API_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _fetch_all_listed_stocks() -> list[str]:
    """
    取得所有上市/上櫃股票代碼（4 位數字，排除 ETF / 特別股）
    使用 twstock.codes 本機資料，不需要 API 請求。
    """
    codes = []
    for code, info in twstock.codes.items():
        # 只取上市(twse)、上櫃(tpex)的普通股，且代碼為 4 位數字
        if (
            getattr(info, 'market', '') in ('上市', '上櫃', 'twse', 'tpex')
            and code.isdigit()
            and len(code) == 4
        ):
            codes.append(code)
    print(f"共取得 {len(codes)} 檔上市/上櫃股票代碼")
    return sorted(codes)


def _fetch_stock_price(stock_id: str, days: int = 120) -> pd.DataFrame | None:
    """
    從 FinMind 取得單一股票近 N 日的日線資料。
    回傳欄位：Date(index), Open, High, Low, Close, Volume
    """
    start_date = (date.today() - timedelta(days=days)).strftime('%Y-%m-%d')
    try:
        resp = requests.get(
            FINMIND_URL,
            params={
                "dataset":    "TaiwanStockPrice",
                "data_id":    stock_id,
                "start_date": start_date,
                "end_date":   date.today().strftime('%Y-%m-%d'),
            },
            headers=_get_token(),
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
        if raw.get("status") != 200 or not raw.get("data"):
            return None

        df = pd.DataFrame(raw["data"])
        df.rename(columns={
            'date': 'Date', 'open': 'Open', 'max': 'High',
            'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume'
        }, inplace=True)
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])

    except Exception:
        return None


def _calc_kd(df: pd.DataFrame, k_period=9, k_slowing=3, d_period=3) -> tuple[float | None, float | None]:
    """計算最新一日的日KD值"""
    if df is None or len(df) < k_period + k_slowing:
        return None, None
    high  = df['High'].values
    low   = df['Low'].values
    close = df['Close'].values
    min_low   = pd.Series(low).rolling(k_period).min()
    max_high  = pd.Series(high).rolling(k_period).max()
    denom     = (max_high - min_low).replace(0, np.nan)
    raw_k     = 100 * ((pd.Series(close) - min_low) / denom)
    k         = raw_k.rolling(k_slowing).mean()
    d         = k.rolling(d_period).mean()
    return (float(k.iloc[-1]) if not pd.isna(k.iloc[-1]) else None,
            float(d.iloc[-1]) if not pd.isna(d.iloc[-1]) else None)


def _apply_filters(stock_id: str) -> dict | None:
    """
    對單一股票套用所有篩選條件。
    回傳 dict（通過）或 None（不通過）。

    篩選條件（對應原 Goodinfo 我的選股103）：
    1. 今日為紅K棒（收>開），棒幅 2.5%~10%
    2. 今日成交張數 5,000~900,000 張
    3. 股價與 60日均線乖離 -5%~+5%（近季線）
    4. 日 K 值 0~50（低檔）且今日 K > 昨日 K（KD 金叉或向上）
    5. 月線(20日均) > 季線(60日均)（多頭排列）
    """
    df = _fetch_stock_price(stock_id, days=120)
    if df is None or len(df) < 65:
        return None

    latest = df.iloc[-1]
    prev   = df.iloc[-2] if len(df) >= 2 else latest

    open_px  = float(latest['Open'])
    close_px = float(latest['Close'])
    high_px  = float(latest['High'])
    volume   = float(latest['Volume'])   # 單位：股，換算張 ÷ 1000

    # --- 條件 1：紅K棒幅 2.5%~10% ---
    if open_px <= 0:
        return None
    bar_pct = (close_px - open_px) / open_px * 100
    if not (2.5 <= bar_pct <= 10.0):
        return None
    if close_px <= open_px:      # 確保收紅
        return None

    # --- 條件 2：成交張數 5,000~900,000 ---
    volume_lots = volume / 1000
    if not (5_000 <= volume_lots <= 900_000):
        return None

    # --- 條件 3：與 60日均線乖離 -5%~+5% ---
    sma60 = df['Close'].rolling(60).mean().iloc[-1]
    if pd.isna(sma60) or sma60 <= 0:
        return None
    dev_60 = (close_px - sma60) / sma60 * 100
    if not (-5.0 <= dev_60 <= 5.0):
        return None

    # --- 條件 4：日KD 低檔且向上（K 0~50 且今日K > 昨日K）---
    k_today, d_today = _calc_kd(df)
    if k_today is None:
        return None
    if not (0 <= k_today <= 50):
        return None
    # 用前一日計算 K，判斷是否向上
    k_prev, _ = _calc_kd(df.iloc[:-1])
    if k_prev is None or k_today <= k_prev:
        return None

    # --- 條件 5：月線 > 季線（多頭排列）---
    sma20 = df['Close'].rolling(20).mean().iloc[-1]
    if pd.isna(sma20) or sma20 <= sma60:
        return None

    # --- 取得股票名稱 ---
    try:
        stock_name = twstock.codes[stock_id].name
    except KeyError:
        stock_name = stock_id

    return {
        '代碼':       stock_id,
        '名稱':       stock_name,
        '市場':       getattr(twstock.codes.get(stock_id), 'market', ''),
        '股價日期':   df.index[-1].strftime('%Y-%m-%d'),
        '成交':       round(close_px, 2),
        '漲跌幅':     round(bar_pct, 2),
        '成交張數':   int(volume_lots),
        '季線乖離%':  round(dev_60, 2),
        '日K值':      round(k_today, 2),
        '日D值':      round(d_today, 2) if d_today is not None else None,
        '月線':       round(float(sma20), 2),
        '季線':       round(float(sma60), 2),
    }


def scrape_goodinfo(max_workers: int = 8) -> pd.DataFrame | None:
    """
    主函式（保持與原 scraper.py 相同的函式名稱，方便直接替換）。
    遍歷所有上市/上櫃股票，套用篩選條件後回傳 DataFrame。

    Args:
        max_workers: 併發執行緒數，預設 8（FinMind 免費版建議不超過 10）

    Returns:
        pd.DataFrame（符合條件的股票），或 None（發生錯誤）
    """
    print("🔄 開始從 FinMind 篩選「我的選股」...")

    all_codes = _fetch_all_listed_stocks()
    if not all_codes:
        print("❌ 無法取得股票清單")
        return None

    results = []
    errors  = 0

    print(f"📡 開始併發查詢 {len(all_codes)} 檔股票（{max_workers} 執行緒）...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_apply_filters, code): code for code in all_codes}

        for i, future in enumerate(as_completed(future_map), 1):
            try:
                result = future.result(timeout=20)
                if result is not None:
                    results.append(result)
            except Exception:
                errors += 1

            if i % 100 == 0:
                print(f"  進度：{i}/{len(all_codes)}，已通過篩選：{len(results)} 檔")

    print(f"✅ 篩選完成！通過：{len(results)} 檔，查詢失敗：{errors} 檔")

    if not results:
        print("⚠️ 沒有股票符合篩選條件。")
        return pd.DataFrame()   # 回傳空 DataFrame 而非 None，讓 UI 顯示「無結果」

    df = pd.DataFrame(results)
    # 欄位順序與原 scraper.py 輸出保持一致
    col_order = ['代碼', '名稱', '市場', '股價日期', '成交', '漲跌幅', '成交張數', '季線乖離%', '日K值', '日D值']
    df = df[[c for c in col_order if c in df.columns]]
    df.sort_values('成交張數', ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)

    print(f"📋 回傳 {len(df)} 筆篩選結果")
    return df


if __name__ == "__main__":
    df = scrape_goodinfo()
    if df is not None and not df.empty:
        print(df.to_string(index=False))
    else:
        print("沒有符合條件的股票，或發生錯誤。")
