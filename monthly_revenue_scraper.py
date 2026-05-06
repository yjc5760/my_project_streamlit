"""
monthly_revenue_scraper.py (優化版)
爬取 Goodinfo 台灣股市資訊網「月營收選股」頁面資料。
已移除硬編碼備用 Cookie，改用 utils.py 共用模組。
"""

import pandas as pd
from io import StringIO
from utils import get_goodinfo_session, fetch_html_with_retry

# Goodinfo「月營收選股103」篩選條件 URL
_TARGET_URL = (
    "https://goodinfo.tw/tw/StockListFilter/StockList.asp"
    "?STEP=DATA&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8"
    "&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6"
    "&SHEET=%E7%87%9F%E6%94%B6%E7%8B%80%E6%B3%81"
    "&SHEET2=%E6%9C%88%E7%87%9F%E6%94%B6%E7%8B%80%E6%B3%81"
    "&FL_SHEET=%E5%B9%B4%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B"
    "&FL_SHEET2=%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B"
    "&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83"
    "&MY_FL_RULE_NM=%E6%9C%88%E7%87%9F%E6%94%B6%E9%81%B8%E8%82%A103"
)

_TABLE_ID = "tblStockList"


def _parse_and_clean(html_content: str) -> pd.DataFrame | None:
    """
    從 HTML 字串解析並清理月營收表格資料，回傳整理後的 DataFrame。
    """
    try:
        tables = pd.read_html(
            StringIO(html_content),
            flavor='lxml',
            attrs={'id': _TABLE_ID}
        )

        if not tables:
            print(f"❌ 錯誤：找不到 ID 為 '{_TABLE_ID}' 的表格。")
            return None

        data_df = tables[0]
        print(f"🎉 成功解析資料！原始列數: {len(data_df)}。")

        # --- 步驟 A: 處理 MultiIndex ---
        if isinstance(data_df.columns, pd.MultiIndex):
            print("ℹ️ 檢測到多層標頭，取最後一層...")
            data_df.columns = data_df.columns.get_level_values(-1)

        # --- 步驟 B: 尋找真正的標頭列 ---
        header_row_idx = None
        for idx, row in data_df.iterrows():
            row_str = str(row.values)
            if '代號' in row_str or '代 號' in row_str or '名稱' in row_str:
                header_row_idx = idx
                break

        if header_row_idx is not None:
            print(f"ℹ️ 在第 {header_row_idx} 列找到欄位名稱，正在重設標頭...")
            data_df.columns = data_df.iloc[header_row_idx]
            data_df = data_df.iloc[header_row_idx + 1:].reset_index(drop=True)

        # --- 步驟 C: 欄位標準化 ---
        data_df.columns = (
            data_df.columns.astype(str)
            .str.replace(' ', '', regex=False)
            .str.replace('\xa0', '', regex=False)
        )

        # 將 '代號' 改為 '代碼'
        if '代號' in data_df.columns:
            data_df = data_df.rename(columns={'代號': '代碼'})

        # --- 步驟 D: 移除無效資料列 ---
        if '名稱' in data_df.columns:
            data_df = data_df[
                ~data_df['名稱'].astype(str).str.contains('合計|總計', na=False)
            ]

        if '代碼' in data_df.columns:
            data_df = data_df[
                pd.to_numeric(data_df['代碼'], errors='coerce').notna()
            ]

        # --- 步驟 E: 最終檢查 ---
        if '代碼' not in data_df.columns or '名稱' not in data_df.columns:
            print(f"❌ 嚴重警告：清理後的資料缺少 '代碼' 或 '名稱'。")
            print(f"👉 目前欄位: {data_df.columns.to_list()}")
            return None

        print(f"✅ 資料清理完成，剩餘 {len(data_df)} 筆。")
        return data_df

    except Exception as e:
        print(f"❌ 在清理資料時發生錯誤: {e}")
        return None


def scrape_goodinfo() -> pd.DataFrame | None:
    """
    專門用於爬取 Goodinfo「月營收選股」頁面的主函式。
    快取由 streamlit_app.py 的 cached_scrape_monthly_revenue 統一管理。
    """
    # 從環境變數取得 Cookie（由 Streamlit secrets 注入），無則拋出錯誤
    try:
        session = get_goodinfo_session('GOODINFO_COOKIE_MONTHLY')
    except ValueError as e:
        print(f"❌ {e}")
        return None

    print("🔄 正在嘗試連線到 Goodinfo 月營收選股頁面...")
    html_content = fetch_html_with_retry(_TARGET_URL, session)

    if html_content is None:
        return None

    # 檢查是否被重定向回首頁（Goodinfo 常見的擋爬蟲機制）
    if "<title>Goodinfo! 台灣股市資訊網 - 首頁</title>" in html_content:
        print("❌ 警告：似乎被重定向回首頁。您的 Cookie 可能已失效，請更新 Secrets 中的 GOODINFO_COOKIE_MONTHLY。")
        return None

    print("✅ 連線成功，正在解析表格...")
    return _parse_and_clean(html_content)


if __name__ == "__main__":
    df = scrape_goodinfo()
    if df is not None:
        print(df.head())
        print(df.columns)
    else:
        print("無法獲取資料。")
