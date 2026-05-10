# scraper.py (修正版：移除 Accept-Encoding 避免解壓縮失敗導致空 body)
import os
import sys
import time
import random
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
    快取由 streamlit_app.py 的 cached_scrape_goodinfo 統一管理。
    """

    # --- 1. 設定爬蟲參數 ---
    url = "https://goodinfo.tw/tw/StockListFilter/StockList.asp?STEP=DATA&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6&SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&SHEET2=%E6%97%A5&FL_SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&FL_SHEET2=%E6%97%A5&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83&MY_FL_RULE_NM=%E9%81%B8%E8%82%A103&FL_ITEM0=%E7%95%B6%E6%97%A5%EF%BC%9A%E7%B4%85K%E6%A3%92%E6%A3%92%E5%B9%85%28%25%29&FL_VAL_S0=2%2E5&FL_VAL_E0=10&FL_ITEM1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29&FL_VAL_S1=5000&FL_VAL_E1=900000&FL_ITEM3=%E5%9D%87%E7%B7%9A%E4%B9%96%E9%9B%A2%28%25%29%E2%80%93%E5%AD%A3&FL_VAL_S3=%2D5&FL_VAL_E3=5&FL_ITEM4=K%E5%80%BC+%28%E9%80%B1%29&FL_VAL_S4=0&FL_VAL_E4=50&FL_RULE0=KD%7C%7C%E9%80%B1K%E5%80%BC+%E2%86%97%40%40%E9%80%B1KD%E8%B5%B0%E5%8B%A2%40%40K%E5%80%BC+%E2%86%97&FL_RULE1=%E5%9D%87%E7%B7%9A%E4%BD%8D%E7%BD%AE%7C%7C%E6%9C%88%2F%E5%AD%A3%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E5%9D%87%E5%83%B9%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E6%9C%88%2F%E5%AD%A3&FL_FD0=K%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7CD%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0&FL_FD1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7C%E6%98%A8%E6%97%A5%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%2E3%7C%7C0&FL_FD2=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD3=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD4=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD5=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&IS_RELOAD_REPORT=T"

    # --- 2. 嚴格檢查環境變數 ---
    cookie = os.getenv('GOODINFO_COOKIE_MY_STOCK')
    if not cookie:
        print("❌ 錯誤：未找到 GOODINFO_COOKIE_MY_STOCK。請確認已在 Streamlit secrets 中設定。")
        return None

    # ⚠️ 關鍵修正：不設定 Accept-Encoding，讓 requests 自行處理解壓縮。
    # 手動設定 Accept-Encoding: gzip 會讓伺服器送回壓縮 body，
    # 但在某些雲端環境（Streamlit Cloud）requests 無法正確解壓，導致 response.text 為空字串。
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://goodinfo.tw/tw/StockListFilter/StockList.asp",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cookie": cookie,
        # Accept-Encoding 刻意省略，由 requests 預設處理
    }

    container_selector = "#tblStockList"
    # 比對用欄位清單：統一使用無空白字串，防止 Goodinfo 微調 HTML 空白字元
    columns_to_keep = ['代號', '名稱', '市場', '股價日期', '成交', '漲跌價', '漲跌幅', '成交張數']

    print("🔄 正在從 Goodinfo (我的選股) 爬取資料...")

    # --- [修改重點] 加入隨機延遲，模擬人類行為（與 monthly_revenue_scraper 一致）---
    delay = random.uniform(1.0, 2.5)
    print(f"⏳ 等待 {delay:.1f} 秒後發送請求...")
    time.sleep(delay)

    session = requests.Session()
    session.headers.update(headers)

    try:
        response = session.get(url, timeout=25)
        response.raise_for_status()

        # ⚠️ 關鍵修正：使用 response.content（bytes）搭配明確 UTF-8 解碼，
        # 避免 response.text 在壓縮內容未正確解壓時回傳空字串。
        raw_bytes = response.content
        print(f"✅ HTTP 回應：{response.status_code}，原始 bytes 長度：{len(raw_bytes):,}")

        if len(raw_bytes) == 0:
            print("❌ 嚴重錯誤：伺服器回傳空 body（0 bytes）。可能原因：IP 被封鎖、請求頻率過快，或 Streamlit Cloud 出口 IP 被 Goodinfo 封鎖。")
            return None

        # 嘗試 UTF-8 解碼；若失敗則退回 big5（Goodinfo 早期頁面有時用 big5）
        try:
            html_content = raw_bytes.decode('utf-8')
        except UnicodeDecodeError:
            print("⚠️ UTF-8 解碼失敗，改用 big5 解碼...")
            html_content = raw_bytes.decode('big5', errors='replace')

        print(f"ℹ️ 解碼後內容長度：{len(html_content):,} 字元")

    except requests.exceptions.RequestException as e:
        print(f"❌ 請求 Goodinfo (我的選股) 失敗: {e}")
        return None

    # --- [修改重點] 加入首頁重定向檢查（與 monthly_revenue_scraper 一致）---
    if "<title>Goodinfo! 台灣股市資訊網 - 首頁</title>" in html_content:
        print("❌ 警告：被重定向回首頁。Cookie 已失效，請從瀏覽器重新複製並更新 GOODINFO_COOKIE_MY_STOCK。")
        return None

    # --- [修改重點] 加入登入頁檢查 ---
    if "請先登入" in html_content or "login" in html_content.lower()[:2000]:
        print("❌ 警告：Goodinfo 要求登入。Cookie 可能已過期或格式錯誤。")
        return None

    # --- 3. 解析資料並轉換為 DataFrame ---
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        data_table = soup.select_one(container_selector)

        if not data_table:
            # [修改重點] 印出頁面 title 幫助診斷實際跑到哪個頁面
            title_tag = soup.find('title')
            page_title = title_tag.text.strip() if title_tag else '(無 title)'
            print(f"❌ 錯誤：在 Goodinfo 頁面中找不到指定的表格 (Selector: {container_selector})")
            print(f"👉 頁面 <title>：{page_title}")
            print(f"👉 頁面前 500 字元：\n{html_content[:500]}")
            return None

        print(f"✅ 找到目標表格，開始解析...")

        try:
            dfs = pd.read_html(io.StringIO(str(data_table)), flavor='lxml')
        except Exception as e:
            print(f"❌ pandas read_html 自動判斷表頭失敗: {e}")
            return None

        if not dfs:
            print(f"❌ 錯誤：在容器 {container_selector} 中找不到任何表格 (<table>)。")
            return None

        df = dfs[0]
        print(f"ℹ️ 原始 DataFrame：{len(df)} 列 x {len(df.columns)} 欄")

        if isinstance(df.columns, pd.MultiIndex):
            print("ℹ️ 偵測到多層標頭，取最後一層...")
            df.columns = df.columns.get_level_values(-1)

        # 先正規化欄位名稱（去除所有空白字元），再進行比對與篩選
        df.columns = df.columns.astype(str).str.replace(r'\s+', '', regex=True)
        print(f"ℹ️ 正規化後欄位：{df.columns.tolist()}")

        missing_columns = [col for col in columns_to_keep if col not in df.columns]
        if missing_columns:
            print(f"❌ 篩選欄位失敗：找不到以下原始欄位 {missing_columns}")
            print(f"👉 抓取到的所有欄位名稱: {df.columns.tolist()}")
            return None

        df_filtered = df[columns_to_keep].copy()

        # 清理「名稱」欄位
        if '名稱' in df_filtered.columns and df_filtered['名稱'].dtype == 'object':
            df_filtered['名稱'] = df_filtered['名稱'].astype(str).str.replace(r' (市|櫃)$', '', regex=True)

        # 統一將「代號」改名為「代碼」
        if '代號' in df_filtered.columns:
            df_filtered.rename(columns={'代號': '代碼'}, inplace=True)

        # 移除無效資料列（代碼無法轉為數字的列，例如標頭重複列）
        if '代碼' in df_filtered.columns:
            before = len(df_filtered)
            df_filtered = df_filtered[pd.to_numeric(df_filtered['代碼'], errors='coerce').notna()]
            removed = before - len(df_filtered)
            if removed > 0:
                print(f"ℹ️ 已移除 {removed} 列無效資料（代碼非數字）。")

        # 正規化欄位名稱：移除所有空白字元，以符合 streamlit_app.py 的期望
        df_filtered.columns = df_filtered.columns.astype(str).str.replace(r'\s+', '', regex=True)

        df_filtered = df_filtered.reset_index(drop=True)
        print(f"✅ 資料清理完成，共 {len(df_filtered)} 筆有效資料。")
        return df_filtered

    except Exception as e:
        print(f"❌ 錯誤：解析 Goodinfo (我的選股) 資料時發生意外 - {e}")
        import traceback
        traceback.print_exc()
        return None
