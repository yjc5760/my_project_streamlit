# scraper.py 

import os
import sys
import requests
import pandas as pd
from bs4 import BeautifulSoup
import io

# Windows CP950 不支援 emoji，強制 stdout 使用 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 🛑 [已移除原本寫死的 _FALLBACK_COOKIE] 🛑

def scrape_goodinfo():
    """
    爬取 Goodinfo 台灣股市資訊網 "我的選股103" 的資料並回傳 DataFrame。
    快取由 streamlit_app.py 的 cached_scrape_goodinfo 統一管理。
    """
    url = "https://goodinfo.tw/tw/StockListFilter/StockList.asp?STEP=DATA..." # (網址保持不變)
    
    # --- 1. 從環境變數讀取 Cookie 並加上防呆檢查 ---
    cookie = os.getenv('GOODINFO_COOKIE_MY_STOCK')
    if not cookie:
        print("❌ 錯誤：未找到 GOODINFO_COOKIE_MY_STOCK。請確認已在 Streamlit secrets 中設定。")
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        "Cookie": cookie
    }
    container_selector = "#tblStockList"
    columns_to_keep = ['代號', '名稱', '市 場', '股價 日期', '成交', '漲跌 價', '漲跌 幅', '成交 張數']
    
    # --- 2. 建立連線並抓取網頁 ---
    print("正在從 Goodinfo (我的選股) 爬取資料...")
    session = requests.Session()
    session.headers.update(headers)

    try:
        response = session.get(url, timeout=20)
        response.raise_for_status()
        response.encoding = 'utf-8'
        html_content = response.text

    except requests.exceptions.RequestException as e:
        print(f"請求 Goodinfo (我的選股) 失敗: {e}")
        return None

    # --- 3. 解析資料並轉換為 DataFrame ---
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        data_table = soup.select_one(container_selector)

        if not data_table:
            print(f"錯誤：在 Goodinfo 頁面中找不到指定的表格 (Selector: {container_selector})")
            return None

        try:
            dfs = pd.read_html(io.StringIO(str(data_table)), flavor='lxml')
        except Exception as e:
            print(f"❌ pandas read_html 自動判斷表頭失敗: {e}")
            return None

        if not dfs:
            print(f"錯誤：在容器 {container_selector} 中找不到任何表格 (<table>)。")
            return None

        df = dfs[0]

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(-1)

        missing_columns = [col for col in columns_to_keep if col not in df.columns]
        if missing_columns:
            print(f"❌ 篩選欄位失敗：找不到以下原始欄位 {missing_columns}")
            print(f"抓取到的所有欄位名稱: {df.columns.tolist()}")
            return None

        df_filtered = df[columns_to_keep].copy()

        if '名稱' in df_filtered.columns and df_filtered['名稱'].dtype == 'object':
            df_filtered['名稱'] = df_filtered['名稱'].astype(str).str.replace(r' (市|櫃)$', '', regex=True)

        if '代號' in df_filtered.columns:
            df_filtered.rename(columns={'代號': '代碼'}, inplace=True)

        # 正規化欄位名稱：移除所有空白字元，以符合 streamlit_app.py 的期望
        # (Goodinfo HTML 的欄位名稱常含多餘空格，例如 '市 場' → '市場')
        df_filtered.columns = df_filtered.columns.astype(str).str.replace(r'\s+', '', regex=True)

        print(f"成功爬取到 {len(df_filtered)} 筆資料。")
        return df_filtered

    except Exception as e:
        print(f"錯誤：解析 Goodinfo (我的選股) 資料時發生意外 - {e}")
        return None
