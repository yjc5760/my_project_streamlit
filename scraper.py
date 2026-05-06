"""
scraper.py (優化版)
爬取 Goodinfo 台灣股市資訊網「我的選股」頁面資料。
已移除硬編碼備用 Cookie，改用 utils.py 共用模組。
"""

from __future__ import annotations
import io
import pandas as pd
from bs4 import BeautifulSoup
from utils import get_goodinfo_session, fetch_html_with_retry

# Goodinfo「我的選股103」篩選條件 URL
_GOODINFO_URL = (
    "https://goodinfo.tw/tw/StockListFilter/StockList.asp"
    "?STEP=DATA&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8"
    "&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6"
    "&SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&SHEET2=%E6%97%A5"
    "&FL_SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&FL_SHEET2=%E6%97%A5"
    "&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83"
    "&MY_FL_RULE_NM=%E9%81%B8%E8%82%A103"
    "&FL_ITEM0=%E7%95%B6%E6%97%A5%EF%BC%9A%E7%B4%85K%E6%A3%92%E6%A3%92%E5%B9%85%28%25%29"
    "&FL_VAL_S0=2.5&FL_VAL_E0=10"
    "&FL_ITEM1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29"
    "&FL_VAL_S1=5000&FL_VAL_E1=900000"
)

_CONTAINER_SELECTOR = "#tblStockList"
_COLUMNS_TO_KEEP = ['代號', '名稱', '市 場', '股價 日期', '成交', '漲跌 價', '漲跌 幅', '成交 張數']


def scrape_goodinfo() -> pd.DataFrame | None:
    """
    爬取 Goodinfo 台灣股市資訊網「我的選股103」的資料並回傳 DataFrame。
    快取由 streamlit_app.py 的 cached_scrape_goodinfo 統一管理。
    """
    # 從環境變數取得 Cookie（由 Streamlit secrets 注入），無則拋出錯誤
    try:
        session = get_goodinfo_session('GOODINFO_COOKIE_MY_STOCK')
    except ValueError as e:
        print(f"❌ {e}")
        return None

    print("正在從 Goodinfo (我的選股) 爬取資料...")
    html_content = fetch_html_with_retry(_GOODINFO_URL, session)
    if html_content is None:
        return None

    # --- 解析資料並轉換為 DataFrame ---
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        data_table = soup.select_one(_CONTAINER_SELECTOR)

        if not data_table:
            print(f"錯誤：在 Goodinfo 頁面中找不到指定的表格 (Selector: {_CONTAINER_SELECTOR})")
            return None

        try:
            dfs = pd.read_html(io.StringIO(str(data_table)), flavor='lxml')
        except Exception as e:
            print(f"❌ pandas read_html 解析失敗: {e}")
            return None

        if not dfs:
            print(f"錯誤：在容器 {_CONTAINER_SELECTOR} 中找不到任何表格。")
            return None

        df = dfs[0]

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(-1)

        missing_columns = [col for col in _COLUMNS_TO_KEEP if col not in df.columns]
        if missing_columns:
            print(f"❌ 篩選欄位失敗：找不到以下原始欄位 {missing_columns}")
            print(f"抓取到的所有欄位名稱: {df.columns.tolist()}")
            return None

        df_filtered = df[_COLUMNS_TO_KEEP].copy()

        if '名稱' in df_filtered.columns and df_filtered['名稱'].dtype == 'object':
            df_filtered['名稱'] = df_filtered['名稱'].astype(str).str.replace(
                r' (市|櫃)$', '', regex=True
            )

        if '代號' in df_filtered.columns:
            df_filtered.rename(columns={'代號': '代碼'}, inplace=True)

        # 正規化欄位名稱：移除所有空白字元
        df_filtered.columns = df_filtered.columns.astype(str).str.replace(r'\s+', '', regex=True)

        print(f"成功爬取到 {len(df_filtered)} 筆資料。")
        return df_filtered

    except Exception as e:
        print(f"錯誤：解析 Goodinfo (我的選股) 資料時發生意外 - {e}")
        return None
