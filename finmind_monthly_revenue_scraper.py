# finmind_monthly_revenue_scraper.py
# 用途：取代 monthly_revenue_scraper.py，從 FinMind API 實作月營收篩選邏輯
# 原 Goodinfo 篩選條件（月營收選股03）：
#   1. 單月營收年增率 - 當月   > 15%
#   2. 單月營收年增率 - 前1月  > 10%
#   3. 單月營收年增率 - 前2月  > 10%
#   4. 單月營收年增率 - 前3月  > 10%
#   5. 單月營收年增率 - 前4月  > 10%
#   6. 單月營收創歷年同期前3高（額外加分條件）
# 全部改為從 FinMind TaiwanStockMonthRevenue 計算

import os
import sys
import requests
import pandas as pd
import numpy as np
from datetime import date, timedelta
import twstock

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _get_token() -> str | None:
    """取得 FinMind API Token（從環境變數）"""
    return os.getenv("FINMIND_API_TOKEN")


def _build_params(base_params: dict) -> tuple[dict, dict]:
    """
    回傳 (params, headers)。
    Token 同時放進 params['token'] 和 Authorization header，
    確保與不同版本的 FinMind API 相容。
    """
    token = _get_token()
    params = dict(base_params)
    headers = {}
    if token:
        params["token"] = token
        headers["Authorization"] = f"Bearer {token}"
    return params, headers


def _fetch_all_revenue() -> pd.DataFrame | None:
    """
    一次性取得所有股票的月營收資料。
    start_date 取 5 年前，確保能計算歷年同期排名。
    需要有效的 FINMIND_API_TOKEN，匿名存取此資料集會回傳 403。
    """
    token = _get_token()
    if not token:
        print("❌ 未設定 FINMIND_API_TOKEN。月營收資料需要有效 Token 才能存取。")
        print("   請至 https://finmindtrade.com/ 免費註冊取得 Token，")
        print("   並在 Streamlit Secrets 加入 FINMIND_API_TOKEN = '你的token'")
        return None

    start_date = (date.today() - timedelta(days=365 * 5)).strftime('%Y-%m-%d')
    print(f"📡 正在從 FinMind 下載所有股票月營收資料（起始：{start_date}）...")

    params, headers = _build_params({
        "dataset":    "TaiwanStockMonthRevenue",
        "start_date": start_date,
        "end_date":   date.today().strftime('%Y-%m-%d'),
    })

    try:
        resp = requests.get(
            FINMIND_URL,
            params=params,
            headers=headers,
            timeout=120,
        )

        if resp.status_code == 403:
            print("❌ FinMind API 回傳 403 Forbidden。")
            print("   可能原因：Token 無效或已過期，請重新至 FinMindtrade.com 確認。")
            return None

        resp.raise_for_status()
        raw = resp.json()

        if raw.get("status") != 200:
            print(f"❌ FinMind API 錯誤：{raw.get('msg', '未知錯誤')}")
            return None

        data = raw.get("data")
        if not data:
            print("❌ FinMind 未回傳任何營收資料")
            return None

        df = pd.DataFrame(data)
        print(f"✅ 取得 {len(df)} 筆原始營收記錄")
        return df

    except requests.exceptions.Timeout:
        print("❌ 請求超時，FinMind 批次下載可能需要較長時間，請稍後再試。")
        return None
    except Exception as e:
        print(f"❌ 下載月營收資料時發生錯誤：{e}")
        return None


def _calc_yoy(df_stock: pd.DataFrame) -> pd.DataFrame:
    """
    對單一股票的月營收 DataFrame 計算：
    - 年增率(YoY)：當月 vs 去年同月
    - 月增率(MoM)：當月 vs 上月
    - 歷年同期排名（同一月份的所有年度中，當年排第幾高）
    """
    df = df_stock.copy().sort_values(['revenue_year', 'revenue_month'])
    df['revenue'] = pd.to_numeric(df['revenue'], errors='coerce')

    # YoY：同月去年 → 按月份 group，做 pct_change
    df = df.sort_values(['revenue_month', 'revenue_year'])
    df['YoY'] = df.groupby('revenue_month')['revenue'].pct_change(1) * 100

    # 歷年同期排名：同月份內，由大到小排名（1=最高）
    df['同期排名'] = df.groupby('revenue_month')['revenue'].rank(ascending=False, method='min')

    # MoM：按時間序排，做 pct_change
    df = df.sort_values(['revenue_year', 'revenue_month'])
    df['MoM'] = df['revenue'].pct_change(1) * 100

    return df


def _filter_by_revenue_growth(df_all: pd.DataFrame) -> pd.DataFrame:
    """
    對所有股票套用月營收篩選邏輯，回傳符合條件的股票彙整表。

    篩選條件：
    1. 最近 5 個月的 YoY 分別 > 15%, 10%, 10%, 10%, 10%（當月最嚴）
    2. （選配）最新月份的歷年同期排名 <= 3（創近 5 年同期前 3 高）
    """
    # 標準化欄位名稱
    df_all.columns = df_all.columns.str.strip()

    # 確認必要欄位存在
    required = ['stock_id', 'revenue_year', 'revenue_month', 'revenue']
    if not all(c in df_all.columns for c in required):
        print(f"❌ 資料欄位不足，需要：{required}，實際：{df_all.columns.tolist()}")
        return pd.DataFrame()

    df_all['revenue_year']  = pd.to_numeric(df_all['revenue_year'],  errors='coerce')
    df_all['revenue_month'] = pd.to_numeric(df_all['revenue_month'], errors='coerce')
    df_all['revenue']       = pd.to_numeric(df_all['revenue'],       errors='coerce')
    df_all = df_all.dropna(subset=required)

    results = []
    stock_ids = df_all['stock_id'].unique()
    print(f"🔍 開始逐股篩選（共 {len(stock_ids)} 檔）...")

    for stock_id in stock_ids:
        df_s = df_all[df_all['stock_id'] == stock_id].copy()
        if len(df_s) < 6:      # 至少需要 6 個月才能計算 5 期 YoY
            continue

        df_s = _calc_yoy(df_s)

        # 取最近 5 個月（排序後取尾 5 筆）
        df_s = df_s.sort_values(['revenue_year', 'revenue_month'])
        recent = df_s.tail(5).reset_index(drop=True)

        if len(recent) < 5:
            continue

        yoy_values = recent['YoY'].values   # index 0=最舊, 4=最新
        thresholds = [10, 10, 10, 10, 15]   # 由舊到新：前4月>10%, 當月>15%

        # 條件 1：YoY 門檻
        if not all(
            pd.notna(y) and y > t
            for y, t in zip(yoy_values, thresholds)
        ):
            continue

        # 取最新月份資訊
        latest = recent.iloc[-1]
        yoy_curr = float(latest['YoY'])
        mom_curr = float(latest['MoM']) if pd.notna(latest['MoM']) else None
        rank     = float(latest['同期排名']) if pd.notna(latest['同期排名']) else None

        # 取股票名稱
        try:
            stock_name = twstock.codes[str(stock_id)].name
        except KeyError:
            stock_name = str(stock_id)

        # 彙整近 5 月 YoY（供 UI 顯示）
        yoy_m0 = round(float(recent.iloc[0]['YoY']), 1) if pd.notna(recent.iloc[0]['YoY']) else None
        yoy_m1 = round(float(recent.iloc[1]['YoY']), 1) if pd.notna(recent.iloc[1]['YoY']) else None
        yoy_m2 = round(float(recent.iloc[2]['YoY']), 1) if pd.notna(recent.iloc[2]['YoY']) else None
        yoy_m3 = round(float(recent.iloc[3]['YoY']), 1) if pd.notna(recent.iloc[3]['YoY']) else None
        yoy_m4 = round(float(recent.iloc[4]['YoY']), 1) if pd.notna(recent.iloc[4]['YoY']) else None

        results.append({
            '代碼':          str(stock_id),
            '名稱':          stock_name,
            '最新月份':      f"{int(latest['revenue_year'])}-{int(latest['revenue_month']):02d}",
            '月營收(千元)':  int(latest['revenue']) if pd.notna(latest['revenue']) else None,
            '年增率%(當月)': yoy_m4,
            '年增率%(前1月)': yoy_m3,
            '年增率%(前2月)': yoy_m2,
            '年增率%(前3月)': yoy_m1,
            '年增率%(前4月)': yoy_m0,
            '月增率%':       round(mom_curr, 1) if mom_curr is not None else None,
            '歷年同期排名':  int(rank) if rank is not None else None,
            '創同期前3高':   '✅' if (rank is not None and rank <= 3) else '',
        })

    df_result = pd.DataFrame(results)
    if df_result.empty:
        print("⚠️ 沒有股票符合月營收篩選條件。")
        return df_result

    # 依當月年增率降序排列
    df_result.sort_values('年增率%(當月)', ascending=False, inplace=True)
    df_result.reset_index(drop=True, inplace=True)
    print(f"✅ 符合月營收篩選條件：{len(df_result)} 檔")
    return df_result


def scrape_goodinfo() -> pd.DataFrame | None:
    """
    主函式（保持與原 monthly_revenue_scraper.py 相同的函式名稱，方便直接替換）。
    從 FinMind 下載月營收資料並套用篩選邏輯。

    Returns:
        pd.DataFrame（符合條件的股票），或 None（API 呼叫失敗）
    """
    print("🔄 開始從 FinMind 篩選「月營收選股」...")

    df_all = _fetch_all_revenue()
    if df_all is None:
        return None

    df_result = _filter_by_revenue_growth(df_all)

    # 為保持與 streamlit_app.py 顯示邏輯的相容性，確保有 '代碼' 和 '名稱' 欄位
    if not df_result.empty:
        print(f"📋 月營收篩選完成，回傳 {len(df_result)} 筆結果")
    return df_result


if __name__ == "__main__":
    df = scrape_goodinfo()
    if df is not None and not df.empty:
        print(df.to_string(index=False))
    else:
        print("沒有符合條件的股票，或發生錯誤。")
