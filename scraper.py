# scraper.py (整合重試機制與強制 Secrets 版)
import os
import sys
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
import io

# Windows CP950 不支援 emoji，強制 stdout 使用 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def scrape_goodinfo():
    """
    爬取 Goodinfo 台灣股市資訊網 "我的選股103" 的資料並回傳 DataFrame。
    內建 3 次重試機制，並嚴格依賴 Streamlit secrets 注入的 Cookie。
    """

    # --- 1. 設定爬蟲參數 ---
    url = "https://goodinfo.tw/tw/StockListFilter/StockList.asp?STEP=DATA&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6&SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&SHEET2=%E6%97%A5&FL_SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&FL_SHEET2=%E6%97%A5&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83&MY_FL_RULE_NM=%E9%81%B8%E8%82%A103&FL_ITEM0=%E7%95%B6%E6%97%A5%EF%BC%9A%E7%B4%85K%E6%A3%92%E6%A3%92%E5%B9%85%28%25%29&FL_VAL_S0=2%2E5&FL_VAL_E0=10&FL_ITEM1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29&FL_VAL_S1=5000&FL_VAL_E1=900000&FL_ITEM3=%E5%9D%87%E7%B7%9A%E4%B9%96%E9%9B%A2%28%25%29%E2%80%93%E5%AD%A3&FL_VAL_S3=%2D5&FL_VAL_E3=5&FL_ITEM4=K%E5%80%BC+%28%E9%80%B1%29&FL_VAL_S4=0&FL_VAL_E4=50&FL_RULE0=KD%7C%7C%E9%80%B1K%E5%80%BC+%E2%86%97%40%40%E9%80%B1KD%E8%B5%B0%E5%8B%A2%40%40K%E5%80%BC+%E2%86%97&FL_RULE1=%E5%9D%87%E7%B7%9A%E4%BD%8D%E7%BD%AE%7C%7C%E6%9C%88%2F%E5%AD%A3%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E5%9D%87%E5%83%B9%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E6%9C%88%2F%E5%AD%A3&FL_FD0=K%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7CD%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0&FL_FD1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7C%E6%98%A8%E6%97%A5%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%2E3%7C%7C0&FL_FD2=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD3=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD4=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD5=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&IS_RELOAD_REPORT=T"
    
    # 嚴格要求環境變數
    cookie = os.getenv('GOODINFO_COOKIE_MY_STOCK')
    if not cookie:
        print("❌ 錯誤：找不到環境變數 'GOODINFO_COOKIE_MY_STOCK'。請確認已在 Streamlit Secrets 中設定。")
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        "Cookie": cookie
    }
    
    container_selector = "#tblStockList"
    columns_to_keep = ['代號', '名稱', '市 場', '股價 日期', '成交', '漲跌 價', '漲跌 幅', '成交 張數']
    
    print("正在從 Goodinfo (我的選股) 爬取資料...")
    session = requests.Session()
    session.headers.update(headers)

    # --- 2. 實作重試機制的連線抓取 ---
    retries = 3
    delay = 2.0
    html_content = None

    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            response.encoding = 'utf-8'
            html_content = response.text
            break  # 成功抓取則跳出迴圈
        except requests.exceptions.RequestException as e:
            print(f"請求 Goodinfo (我的選股) 失敗 第 {attempt} 次嘗試: {e}")
            if attempt < retries:
                print(f"{delay} 秒後重試...")
                time.sleep(delay)
            else:
                print(f"已重試 {retries} 次，全部失敗。")
                return None

    if not html_content:
        return None

    # --- 3. 解析資料並轉換為 DataFrame ---
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        data_table = soup.select_one(container_selector)

        if not data_table:
            print(f"錯誤：在 Goodinfo 頁面中找不到指定的表格 (Selector: {container_selector})")
            # 通常發生這個情況，可能是 Cookie 失效被導回首頁
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

        # 正規化欄位名稱：移除所有空白字元
        df_filtered.columns = df_filtered.columns.astype(str).str.replace(r'\s+', '', regex=True)

        print(f"成功爬取到 {len(df_filtered)} 筆資料。")
        return df_filtered

    except Exception as e:
        print(f"錯誤：解析 Goodinfo (我的選股) 資料時發生意外 - {e}")
        return None

if __name__ == "__main__":
    df = scrape_goodinfo()
    if df is not None:
        print(df.head())
