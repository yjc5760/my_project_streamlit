# monthly_revenue_scraper.py (修正版 v2 - 整合 st.secrets)
import requests
import pandas as pd
from io import StringIO # 導入 StringIO
import streamlit as st # ⭐️ 新增：導入 streamlit

def fetch_stock_data(url: str, headers: dict, table_id: str) -> pd.DataFrame | None:
    """
    訪問目標 URL 並爬取指定的表格資料。

    Args:
        url (str): 要爬取的網址。
        headers (dict): 包含 User-Agent 和 Cookie 的請求標頭。
        table_id (str): 要抓取的 <table> 標籤的 id。

    Returns:
        pd.DataFrame | None: 成功則回傳 DataFrame，失敗則回傳 None。
    """
    print(f"🔄 正在嘗試連線到目標網址...")
    
    # 1. 使用 requests.Session() 來保持連線狀態 (Cookies)
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        # 2. 發送 GET 請求 (設定 timeout 避免無止盡等待)
        response = session.get(url, timeout=15)
        
        # 檢查 HTTP 狀態碼，4xx 或 5xx 會觸發例外
        response.raise_for_status()
        
        # 3. 確保回應內容為 UTF-8 編碼
        response.encoding = 'utf-8'
        html_content = response.text
        
        print("✅ 連線成功，正在解析表格...")

        # 4. [關鍵] 解析資料 
        # 使用 StringIO 包裝 html_content 來消除 FutureWarning
        tables = pd.read_html(
            StringIO(html_content), 
            flavor='lxml', 
            attrs={'id': table_id}
        )
        
        # 5. 取得 DataFrame
        df = tables[0]
        
        print(f"🎉 成功解析資料！共 {len(df)} 筆。")
        return df

    # 6. 完整的錯誤處理
    except requests.exceptions.HTTPError as http_err:
        print(f"❌ HTTP 錯誤: {http_err}")
        if response.status_code == 403:
            print("👉 (403 Forbidden) 存取被拒。請檢查您的 Cookie 是否正確或已失效。")
            st.error("❌ (403) Goodinfo 存取被拒，請檢查 Cookie。")
        elif response.status_code == 404:
            print("👉 (404 Not Found) 找不到頁面，請檢查 URL。")
            st.error("❌ (404) 找不到爬蟲頁面。")
    except requests.exceptions.ConnectionError as conn_err:
        print(f"❌ 連線錯誤: {conn_err}")
        st.error(f"❌ 爬蟲連線錯誤: {conn_err}")
    except requests.exceptions.Timeout:
        print("❌ 請求超時 (Timeout)。")
        st.error("❌ 爬蟲請求超時 (Timeout)。")
    except IndexError:
        print(f"❌ 嚴重錯誤：找不到表格 (list index out of range)。")
        print(f"👉 這幾乎可以肯定是 Cookie 失效，導致爬蟲抓到「登入頁」而非資料頁。")
        st.error("❌ 爬蟲解析失敗 (IndexError)，這通常代表 Cookie 已失效，請更新 st.secrets。")
    except Exception as e:
        print(f"❌ 發生未知錯誤: {e}")
        st.error(f"❌ 爬蟲發生未知錯誤: {e}")
        
    return None

# --- [新函式] 符合 streamlit_app.py 導入需求的函式 ---
def scrape_goodinfo() -> pd.DataFrame | None:
    """
    專門用於爬取 Goodinfo「月營收選股」頁面的主函式。
    此函式會自動帶入固定的 URL, Headers, 和 Table ID。
    
    Returns:
        pd.DataFrame | None: 爬取到的資料，失敗則為 None。
    """
    
    # 1. 目標網址
    TARGET_URL = "https://goodinfo.tw/tw/StockList/StockList.asp?STEP=DATA&SEARCH_WORD=&SHEET=%E5%B9%B4%E7%8D%B2%E5%88%A9%E8%83%BD%E5%A_A&SHEET2=%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&RPT_TIME=%E6%9C%80%E6%96%B0%E8%B3%87%E6%96%99&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6&STOCK_CODE=&RANK=0&SORT_FIELD=&SORT=&FL_SHEET=%E5%B9%B4%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&FL_SHEET2=%E7%8D%B2%E5%88%A9%E8%83%BD%E5_A&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83&FL_ITEM0=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E7%95%B6%E6%9C%88&FL_VAL_S0=15&FL_VAL_E0=&FL_VAL_CHK0=&FL_ITEM1=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D1%E6%9C%88&FL_VAL_S1=10&FL_VAL_E1=&FL_VAL_CHK1=&FL_ITEM2=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D2%E6%9C%88&FL_VAL_S2=10&FL_VAL_E2=&FL_VAL_CHK2=&FL_ITEM3=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D3%E6%9C%88&FL_VAL_S3=10&FL_VAL_E3=&FL_VAL_CHK3=&FL_ITEM4=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D4%E6%9C%88&FL_VAL_S4=10&FL_VAL_E4=&FL_VAL_CHK4=&FL_ITEM5=&FL_VAL_S5=&FL_VAL_E5=&FL_VAL_CHK5=&FL_ITEM6=&FL_VAL_S6=&FL_VAL_E6=&FL_VAL_CHK6=&FL_ITEM7=&FL_VAL_S7=&FL_VAL_E7=&FL_VAL_CHK7=&FL_ITEM8=&FL_VAL_S8=&FL_VAL_E8=&FL_VAL_CHK8=&FL_ITEM9=&FL_VAL_S9=&FL_VAL_E9=&FL_VAL_CHK9=&FL_ITEM10=&FL_VAL_S10=&FL_VAL_E10=&FL_VAL_CHK10=&FL_ITEM11=&FL_VAL_S11=&FL_VAL_E11=&FL_VAL_CHK11=&FL_RULE0=%E6%9C%88%E7%87%9F%E6%94%B6%7C%7C%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%AD%B7%E5%B9%B4%E5%90%8C%E6%9C%9F%E5%89%8D3%E9%AB%98%40%40%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%8E%92%E5%90%8D%E7%B4%80%E9%8C%84%40%40%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%AD%B7%E5%B9%B4%E5%90%8C%E6%9C%9F%E5%89%8D3%E9%AB%98&FL_RULE_CHK0=&FL_RULE1=&FL_RULE_CHK1=&FL_RULE2=&FL_RULE_CHK2=&FL_RULE3=&FL_RULE_CHK3=&FL_RULE4=&FL_RULE_CHK4=&FL_RULE5=&FL_RULE_CHK5=&FL_RANK0=&FL_RANK1=&FL_RANK2=&FL_RANK3=&FL_RANK4=&FL_RANK5=&FL_FD0=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD1=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD2=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD3=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD4=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD5=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&MY_FL_RULE_NM=%E7%87%9F%E6%94%B6%E9%81%B8%E8%82%A102"
    
    # 2. 目標表格 ID
    TABLE_ID = "tblStockList"
    
    # --- ⭐️ 關鍵修正：從 st.secrets 獲取 Cookie ---
    if 'GOODINFO_COOKIE' not in st.secrets:
        # 這個 print 會顯示在雲端日誌中
        print("❌ 錯誤：找不到 Goodinfo Cookie。請在 Streamlit secrets 中設定 'GOODINFO_COOKIE'。")
        # st.error 會顯示在 App 介面上
        st.error("❌ 找不到 Goodinfo Cookie。請在 Streamlit secrets 中設定 'GOODINFO_COOKIE'。")
        return None
        
    goodinfo_cookie = st.secrets['GOODINFO_COOKIE']
    # -----------------------------------------------

    # 3. 請求標頭
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        "Cookie": goodinfo_cookie # ⭐️ 使用從 secrets 讀取的值
    }
    
    # 4. 執行爬蟲
    data_df = fetch_stock_data(TARGET_URL, HEADERS, TABLE_ID)
    
    # 5. [關鍵修復] 處理 Goodinfo 特有的多層標頭 (MultiIndex)
    if data_df is not None:
        try:
            # Goodinfo 的表格常有 2 層標頭 (MultiIndex)
            # 我們只需要第二層 (level 1)，它才包含 '代碼', '名稱'
            if isinstance(data_df.columns, pd.MultiIndex):
                print("ℹ️ 檢測到多層標頭 (MultiIndex)，正在進行簡化...")
                new_columns = data_df.columns.get_level_values(1)
                
                # 處理重複欄位
                if not new_columns.is_unique:
                    print("⚠️ 檢測到重複的欄位名稱，將嘗試去重複。")
                    seen = set()
                    final_cols = []
                    for col in new_columns:
                        if col in seen:
                            i = 1
                            new_col = f"{col}_{i}"
                            while new_col in seen:
                                i += 1
                                new_col = f"{col}_{i}"
                            final_cols.append(new_col)
                            seen.add(new_col)
                        else:
                            final_cols.append(col)
                            seen.add(col)
                    data_df.columns = final_cols
                else:
                    data_df.columns = new_columns
                
                print(f"✅ 標頭已簡化。新標頭 (前10): {data_df.columns.to_list()[:10]}")

            # [--- 關鍵修復點 ---]
            # 檢查 '代號' 是否存在，如果存在，將其改名為 '代碼'
            if '代號' in data_df.columns and '代碼' not in data_df.columns:
                print("ℹ️ 偵測到欄位 '代號'，將其重新命名為 '代碼' 以符合 App 需求。")
                data_df = data_df.rename(columns={'代號': '代碼'})
            
            # [關鍵修復] 移除 "合計" 或 "總計" 列
            if '名稱' in data_df.columns:
                 data_df = data_df[~data_df['名稱'].astype(str).str.contains('合計|總計', na=False)]

            # 最終檢查 '代碼' 和 '名稱' 是否存在
            if '代碼' not in data_df.columns or '名稱' not in data_df.columns:
                print(f"❌ 嚴重警告：簡化後的標頭中仍缺少 '代碼' 或 '名稱'。")
                print(f"👉 目前標頭: {data_df.columns.to_list()}")
                st.error(f"❌ 爬蟲解析錯誤：簡化後的標頭中缺少 '代碼' 或 '名稱'。")
                return None # 回傳 None 讓 Streamlit 知道出錯了
                
        except Exception as e:
            print(f"❌ 在處理多層標頭時發生錯誤: {e}")
            print(f"👉 原始標頭: {data_df.columns}")
            st.error(f"❌ 處理多層標頭時發生錯誤: {e}")
            return None # 處理失敗，回傳 None
            
    # 6. 回傳結果 (DataFrame 或 None)
    return data_df


# --- 腳本執行區 (用於獨立測試) ---
if __name__ == "__main__":
    
    # ⭐️ 為了讓本地測試能運作，你需要模擬 st.secrets
    # ⭐️ 執行此腳本前，請先在本地建立一個 .streamlit/secrets.toml 檔案
    # ⭐️ 並在該檔案中加入 [GOODINFO_COOKIE] = "你的Cookie字串"
    # ⭐️ 或者在執行前手動設定環境變數
    
    try:
        # 嘗試加載本地的 secrets.toml (如果存在)
        import toml
        from pathlib import Path
        
        secrets_path = Path(".streamlit/secrets.toml")
        if secrets_path.exists():
            print("--- 偵測到本地 secrets.toml，載入中 ---")
            st.secrets = toml.load(secrets_path)
        else:
            print("--- 未偵測到 .streamlit/secrets.toml ---")
            # 模擬一個空的 st.secrets
            if not hasattr(st, 'secrets'):
                st.secrets = {}
                
    except ImportError:
        print("--- toml 套件未安裝，跳過本地 secrets.toml 載入 ---")
        if not hasattr(st, 'secrets'):
            st.secrets = {}
            
    except Exception as e:
        print(f"--- 載入 secrets.toml 失敗: {e} ---")
        if not hasattr(st, 'secrets'):
            st.secrets = {}

    # 檢查 GOODINFO_COOKIE 是否真的被載入
    if 'GOODINFO_COOKIE' not in st.secrets:
        print("❌ 警告：st.secrets 中未設定 'GOODINFO_COOKIE'。")
        print("👉 本地測試 (python monthly_revenue_scraper.py) 可能會失敗。")
        print("👉 請建立 .streamlit/secrets.toml 檔案來進行測試。")
        # 為了讓測試能繼續，我們暫時設定一個空值
        st.secrets['GOODINFO_COOKIE'] = "TEST_COOKIE_IS_NOT_SET"


    # 1. 執行爬蟲 (呼叫新函式)
    print("\n--- [開始本地測試] ---")
    data_df = scrape_goodinfo()
    
    # 2. 顯示結果
    if data_df is not None:
        print("\n--- 爬取結果 (前 5 筆) ---")
        print(data_df.head())
        print("\n--- 欄位名稱 ---")
        print(data_df.columns.to_list())
        
        # 檢查 '代碼' 和 '名稱'
        if '代碼' in data_df.columns and '名稱' in data_df.columns:
            print("\n✅ 測試成功：'代碼' 和 '名稱' 欄位皆存在。")
        else:
            print("\n❌ 測試失敗：缺少 '代碼' 或 '名稱' 欄位。")
            
    else:
        print("\n❌ 測試執行失敗，未獲取到任何資料。")
