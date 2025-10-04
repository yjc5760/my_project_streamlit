# scraper.py (優化後完整版)

import requests
import pandas as pd
from bs4 import BeautifulSoup
from io import StringIO
import streamlit as st # 導入 streamlit 以便讀取 secrets

def scrape_goodinfo():
    """
    爬取 Goodinfo 網站上符合特定條件的股票資料，並回傳一個 DataFrame。
    優化：從 st.secrets 讀取 Cookie，避免敏感資訊寫死在程式碼中。
    """
    # 1. 目標網址 (維持不變)
    url = 'https://goodinfo.tw/tw/StockListFilter/StockList.asp?STEP=DATA&SEARCH_WORD=&SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&SHEET2=%E6%97%A5&RPT_TIME=%E6%9C%80%E6%96%B0%E8%B3%87%E6%96%99&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6&STOCK_CODE=&RANK=0&SORT_FIELD=&SORT=&FL_SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&FL_SHEET2=%E6%97%A5&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83&FL_ITEM0=%E7%95%B6%E6%97%A5%EF%BC%9A%E7%B4%85K%E6%A3%92%E6%A3%92%E5%B9%85%28%25%29&FL_VAL_S0=2%2E5&FL_VAL_E0=10&FL_VAL_CHK0=&FL_ITEM1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29&FL_VAL_S1=5000&FL_VAL_E1=900000&FL_VAL_CHK1=&FL_ITEM2=&FL_VAL_S2=&FL_VAL_E2=&FL_VAL_CHK2=&FL_ITEM3=%E5%9D%87%E7%B7%9A%E4%B9%96%E9%9B%A2%28%25%29%E2%80%93%E5%AD%A3&FL_VAL_S3=%2D5&FL_VAL_E3=5&FL_VAL_CHK3=&FL_ITEM4=K%E5%80%BC+%28%E9%80%B1%29&FL_VAL_S4=0&FL_VAL_E4=50&FL_VAL_CHK4=&FL_ITEM5=&FL_VAL_S5=&FL_VAL_E5=&FL_VAL_CHK5=&FL_ITEM6=&FL_VAL_S6=&FL_VAL_E6=&FL_VAL_CHK6=&FL_ITEM7=&FL_VAL_S7=&FL_VAL_E7=&FL_VAL_CHK7=&FL_ITEM8=&FL_VAL_S8=&FL_VAL_E8=&FL_VAL_CHK8=&FL_ITEM9=&FL_VAL_S9=&FL_VAL_E9=&FL_VAL_CHK9=&FL_ITEM10=&FL_VAL_S10=&FL_VAL_E10=&FL_VAL_CHK10=&FL_ITEM11=&FL_VAL_S11=&FL_VAL_E11=&FL_VAL_CHK11=&FL_RULE0=KD%7C%7C%E9%80%B1K%E5%80%BC+%E2%86%97%40%40%E9%80%B1KD%E8%B5%B0%E5%8B%A2%40%40K%E5%80%BC+%E2%86%97&FL_RULE_CHK0=&FL_RULE1=%E5%9D%87%E7%B7%9A%E4%BD%8D%E7%BD%AE%7C%7C%E6%9C%88%2F%E5%AD%A3%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E5%9D%87%E5%83%B9%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E6%9C%88%2F%E5%AD%A3&FL_RULE_CHK1=&FL_RULE2=&FL_RULE_CHK2=&FL_RULE3=&FL_RULE_CHK3=&FL_RULE4=&FL_RULE_CHK4=&FL_RULE5=&FL_RULE_CHK5=&FL_RANK0=&FL_RANK1=&FL_RANK2=&FL_RANK3=&FL_RANK4=&FL_RANK5=&FL_FD0=K%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7CD%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0&FL_FD1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7C%E6%98%A8%E6%97%A5%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%2E3%7C%7C0&FL_FD2=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD3=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD4=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD5=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&MY_FL_RULE_NM=%E9%81%B8%E8%82%A103'

    # 2. 從 Streamlit secrets 讀取 Cookie
    try:
        # 程式會嘗試讀取名為 "goodinfo" 下的 "cookie" 鍵值
        cookie_value = st.secrets["goodinfo"]["cookie"]
    except (KeyError, FileNotFoundError):
        # 如果找不到 secrets 設定，就在 Streamlit 頁面上顯示錯誤訊息並回傳 None
        st.error("找不到 Goodinfo Cookie！請在 Streamlit secrets 中設定 'goodinfo.cookie'。")
        return None

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Cookie': cookie_value
    }

    try:
        print("正在從 Goodinfo 爬取資料...")
        res = requests.get(url, headers=headers, timeout=20) # 增加 timeout 時間
        res.encoding = 'utf-8'
        res.raise_for_status() # 如果請求失敗 (e.g., 403 Forbidden)，會拋出異常

        soup = BeautifulSoup(res.text, 'lxml')
        table = soup.select_one('#tblStockList')

        if table:
            dfs = pd.read_html(StringIO(str(table)))
            if not dfs:
                print("❌ Pandas 無法從 HTML 表格中解析出資料。")
                return None
            
            df = dfs[0]

            # 清理欄位名稱
            df.columns = df.columns.get_level_values(0)
            df.columns = df.columns.str.replace(r'\s+', '', regex=True)
            
            # --- 【關鍵修正處】 ---
            # 將 '代號' 欄位名稱改成 '代碼'，以符合主程式的需求
            if '代號' in df.columns:
                df.rename(columns={'代號': '代碼'}, inplace=True)
            # --- 【修正結束】 ---

            columns_to_keep = ['代碼', '名稱', '市場', '股價日期', '成交', '漲跌價', '漲跌幅', '成交張數']
            
            # 檢查要保留的欄位是否存在，不存在的就忽略
            existing_columns = [col for col in columns_to_keep if col in df.columns]
            
            if not existing_columns:
                print(f"❌ 警告：所有預期欄位 ({columns_to_keep}) 都不存在於爬取到的資料中。")
                return df # 回傳原始 dataframe 以便除錯

            print("✅ 成功從 Goodinfo 抓取並篩選欄位！")
            return df[existing_columns]

        else:
            print("❌ 找不到指定的表格 (ID: tblStockList)，請檢查網頁原始碼或 Cookie 是否有效。")
            return None

    except requests.exceptions.HTTPError as e:
        print(f"❌ 請求失敗，HTTP 錯誤: {e.response.status_code} {e.response.reason}")
        print("   這通常表示您的 Cookie 已過期或無效，請更新 secrets 中的 Cookie。")
        return None
    except requests.exceptions.RequestException as e:
        print(f"❌ 請求失敗，網路連線錯誤: {e}")
        return None
    except Exception as e:
        print(f"❌ 發生未預期的錯誤: {e}")
        return None

if __name__ == '__main__':
    print("--- 開始獨立測試 scraper.py ---")
    # 注意：直接執行此檔案無法讀取 streamlit secrets，僅用於基本語法檢查
    print("警告：獨立執行模式下無法讀取 Streamlit secrets，將無法成功爬取。")
    # my_stock_picks = scrape_goodinfo()
    # if my_stock_picks is not None and not my_stock_picks.empty:
    #     print("\n成功獲取資料：")
    #     print(my_stock_picks.head())
    #     print("\n欄位名稱：")
    #     print(my_stock_picks.columns)
    # else:
    #     print("\n測試未獲取到任何資料。")
    print("--- 測試結束 ---")
