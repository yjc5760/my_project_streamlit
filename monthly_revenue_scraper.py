import os
import sys
import requests
import pandas as pd
from io import StringIO
import time
import random

# Windows CP950 不支援 emoji，強制 stdout 使用 UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def fetch_stock_data(url: str, headers: dict, table_id: str) -> pd.DataFrame | None:
    """
    訪問目標 URL 並爬取指定的表格資料。
    """
    print(f"🔄 正在嘗試連線到目標網址...")
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        # 加入隨機延遲，模擬人類行為
        time.sleep(random.uniform(1, 2))
        
        response = session.get(url, timeout=20)
        response.raise_for_status()
        response.encoding = 'utf-8'
        html_content = response.text
        
        # 檢查是否被重定向回首頁 (Goodinfo 常見的擋爬蟲機制)
        if "<title>Goodinfo! 台灣股市資訊網 - 首頁</title>" in html_content:
            print("❌ 警告：似乎被重定向回首頁。您的 Cookie 可能已失效，請更新環境變數中的 Cookie。")
            return None

        print("✅ 連線成功，正在解析表格...")

        # 解析資料
        tables = pd.read_html(
            StringIO(html_content), 
            flavor='lxml', 
            attrs={'id': table_id} 
        )
        
        if not tables:
            print(f"❌ 錯誤：找不到 ID 為 '{table_id}' 的表格。")
            return None

        # 取得 DataFrame
        df = tables[0]
        
        print(f"🎉 成功解析資料！原始列數: {len(df)}。")
        return df

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ HTTP 錯誤: {http_err}")
    except requests.exceptions.ConnectionError as conn_err:
        print(f"❌ 連線錯誤: {conn_err}")
    except requests.exceptions.Timeout:
        print("❌ 請求超時 (Timeout)。")
    except ValueError as ve:
        print(f"❌ 解析錯誤 (可能找不到表格): {ve}")
    except Exception as e:
        print(f"❌ 發生未知錯誤: {e}")
        
    return None

def scrape_goodinfo() -> pd.DataFrame | None:
    """
    專門用於爬取 Goodinfo「月營收選股」頁面的主函式。
    """
    
    # 1. 目標網址
    TARGET_URL = "https://goodinfo.tw/tw/StockListFilter/StockList.asp?STEP=DATA&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6&SHEET=%E7%87%9F%E6%94%B6%E7%8B%80%E6%B3%81&SHEET2=%E6%9C%88%E7%87%9F%E6%94%B6%E7%8B%80%E6%B3%81&FL_SHEET=%E5%B9%B4%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&FL_SHEET2=%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83&MY_FL_RULE_NM=%E6%9C%88%E7%87%9F%E6%94%B6%E9%81%B8%E8%82%A103&FL_ITEM0=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E7%95%B6%E6%9C%88&FL_VAL_S0=15&FL_ITEM1=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D1%E6%9C%88&FL_VAL_S1=10&FL_ITEM2=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D2%E6%9C%88&FL_VAL_S2=10&FL_ITEM3=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D3%E6%9C%88&FL_VAL_S3=10&FL_ITEM4=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D4%E6%9C%88&FL_VAL_S4=10&FL_RULE0=%E6%9C%88%E7%87%9F%E6%94%B6%7C%7C%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%AD%B7%E5%B9%B4%E5%90%8C%E6%9C%9F%E5%89%8D3%E9%AB%98%40%40%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%8E%92%E5%90%8D%E7%B4%80%E9%8C%84%40%40%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%AD%B7%E5%B9%B4%E5%90%8C%E6%9C%9F%E5%89%8D3%E9%AB%98&FL_FD0=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD1=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD2=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD3=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD4=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD5=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&IS_RELOAD_REPORT=T"
    
    # 2. 目標表格 ID
    TABLE_ID = "tblStockList"
    
    # 3. 請求標頭：從環境變數讀取 Cookie 並進行檢查
    cookie = os.getenv('GOODINFO_COOKIE_MONTHLY')
    if not cookie:
        print("❌ 錯誤：未找到環境變數 GOODINFO_COOKIE_MONTHLY。請確認已在 Streamlit Secrets 中設定。")
        return None

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://goodinfo.tw/tw/StockListFilter/StockList.asp",
        "Cookie": cookie
    }
    
    # 4. 執行爬蟲
    data_df = fetch_stock_data(TARGET_URL, HEADERS, TABLE_ID)
    
    # 5. 資料清理與標頭處理
    if data_df is not None:
        try:
            # --- 步驟 A: 處理 MultiIndex (如果有) ---
            if isinstance(data_df.columns, pd.MultiIndex):
                print("ℹ️ 檢測到多層標頭，取最後一層...")
                data_df.columns = data_df.columns.get_level_values(-1)

            # --- 步驟 B: 尋找真正的標頭列 ---
            header_row_idx = None
            for idx, row in data_df.iterrows():
                # 只檢查第一欄，避免股票名稱含「代號」兩字時誤判整列為標頭
                first_cell = str(row.iloc[0]).strip()
                if first_cell in ('代號', '代 號', '代碼', '股票代號', '股票代碼'):
                    header_row_idx = idx
                    break
            
            if header_row_idx is not None:
                # 重新設定標頭
                print(f"ℹ️ 在第 {header_row_idx} 列找到欄位名稱，正在重設標頭...")
                data_df.columns = data_df.iloc[header_row_idx]
                data_df = data_df.iloc[header_row_idx + 1:].reset_index(drop=True)
            
            # --- 步驟 C: 欄位標準化 ---
            # 移除欄位名稱中的特殊空白
            data_df.columns = data_df.columns.astype(str).str.replace(' ', '').str.replace('\xa0', '')
            
            # 將 '代號' 改為 '代碼'
            if '代號' in data_df.columns:
                data_df = data_df.rename(columns={'代號': '代碼'})
            
            # --- 步驟 D: 移除無效資料列 ---
            if '名稱' in data_df.columns:
                 data_df = data_df[~data_df['名稱'].astype(str).str.contains('合計|總計', na=False)]
            
            if '代碼' in data_df.columns:
                data_df = data_df[pd.to_numeric(data_df['代碼'], errors='coerce').notna()]

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
            
    return None

if __name__ == "__main__":
    df = scrape_goodinfo()
    if df is not None:
        print(df.head())
        print(df.columns)
    else:
        print("無法獲取資料。")
