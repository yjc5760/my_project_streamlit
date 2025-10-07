# scraper.py (替換為此版本)

import streamlit as st
import requests
import pandas as pd
from bs4 import BeautifulSoup
from io import StringIO

@st.cache_data(ttl=1800) # 優化：增加快取，每30分鐘刷新一次資料
def scrape_goodinfo():
    """
    爬取 Goodinfo 網站上符合特定條件的股票資料，並回傳一個 DataFrame。
    此版本專為抓取名為 '選股03' 的自訂篩選條件。
    """
    url = 'https://goodinfo.tw/tw/StockListFilter/StockList.asp?STEP=DATA&SEARCH_WORD=&SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&SHEET2=%E6%97%A5&RPT_TIME=%E6%9C%80%E6%96%B0%E8%B3%87%E6%96%99&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6&STOCK_CODE=&RANK=0&SORT_FIELD=&SORT=&FL_SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&FL_SHEET2=%E6%97%A5&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83&FL_ITEM0=%E7%95%B6%E6%97%A5%EF%BC%9A%E7%B4%85K%E6%A3%92%E6%A3%92%E5%B9%85%28%25%29&FL_VAL_S0=2%2E5&FL_VAL_E0=10&FL_VAL_CHK0=&FL_ITEM1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29&FL_VAL_S1=5000&FL_VAL_E1=900000&FL_VAL_CHK1=&FL_ITEM2=&FL_VAL_S2=&FL_VAL_E2=&FL_VAL_CHK2=&FL_ITEM3=%E5%9D%87%E7%B7%9A%E4%B9%96%E9%9B%A2%28%25%29%E2%80%93%E5%AD%A3&FL_VAL_S3=%2D5&FL_VAL_E3=5&FL_VAL_CHK3=&FL_ITEM4=K%E5%80%BC+%28%E9%80%B1%29&FL_VAL_S4=0&FL_VAL_E4=50&FL_VAL_CHK4=&FL_ITEM5=&FL_VAL_S5=&FL_VAL_E5=&FL_VAL_CHK5=&FL_ITEM6=&FL_VAL_S6=&FL_VAL_E6=&FL_VAL_CHK6=&FL_ITEM7=&FL_VAL_S7=&FL_VAL_E7=&FL_VAL_CHK7=&FL_ITEM8=&FL_VAL_S8=&FL_VAL_E8=&FL_VAL_CHK8=&FL_ITEM9=&FL_VAL_S9=&FL_VAL_E9=&FL_VAL_CHK9=&FL_ITEM10=&FL_VAL_S10=&FL_VAL_E10=&FL_VAL_CHK10=&FL_ITEM11=&FL_VAL_S11=&FL_VAL_E11=&FL_VAL_CHK11=&FL_RULE0=KD%7C%7C%E9%80%B1K%E5%80%BC+%E2%86%97%40%40%E9%80%B1KD%E8%B5%B0%E5%8B%A2%40%40K%E5%80%BC+%E2%86%97&FL_RULE_CHK0=&FL_RULE1=%E5%9D%87%E7%B7%9A%E4%BD%8D%E7%BD%AE%7C%7C%E6%9C%88%2F%E5%AD%A3%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E5%9D%87%E5%83%B9%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E6%9C%88%2F%E5%AD%A3&FL_RULE_CHK1=&FL_RULE2=&FL_RULE_CHK2=&FL_RULE3=&FL_RULE_CHK3=&FL_RULE4=&FL_RULE_CHK4=&FL_RULE5=&FL_RULE_CHK5=&FL_RANK0=&FL_RANK1=&FL_RANK2=&FL_RANK3=&FL_RANK4=&FL_RANK5=&FL_FD0=K%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7CD%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0&FL_FD1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7C%E6%98%A8%E6%97%A5%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%2E3%7C%7C0&FL_FD2=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD3=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD4=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD5=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&MY_FL_RULE_NM=%E9%81%B8%E8%82%A103'

    # --- 重要提示 ---
    # Goodinfo 需要登入後的 Cookie 才能存取自訂選股頁面。
    # 這個 Cookie 有時效性，如果爬取失敗，很可能是 Cookie 過期了。
    # 請手動登入 Goodinfo，然後使用瀏覽器的開發者工具 (F12) -> Network -> 找到任何一個請求 -> Headers -> 複製新的 Cookie 字串來取代下面的內容。
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko/120.0.0.0 Safari/537.36',
        'Cookie': 'CLIENT%5FID=20250309065220414%5F114%2E37%2E196%2E176; _ga=GA1.1.464844450.1741474343; LOGIN=EMAIL=yjc5760%40gmail%2Ecom&USER%5FNM=%E9%99%B3%E7%9B%8A%E7%A6%8E&ACCOUNT%5FID=107359590931917990151&ACCOUNT%5FVENDOR=Google&NO_EXPIRE=T; AviviD_uuid=9a203aa2-c00e-4a36-a041-35d20a792aa4; AviviD_refresh_uuid_status=1; IS_TOUCH_DEVICE=F; SCREEN_SIZE=WIDTH=1536&HEIGHT=864; _cc_id=daaca99c1bd8458dc4eb99371d075914; panoramaId_expiry=1759622298330; panoramaId=428bd4c8af83c2f970ed9ee62e89185ca02c7c2d09b06798a5bcd42ea1926aad; panoramaIdType=panoDevice; cto_bidid=KYK-MV8wSjQyaWZ4WHhFRnNDR3JubTZ2U3QxeGN1YW02am50eSUyRmNIamJoYzd0dVBFTTUxYnEzT2ZndVlVYWFqMXVoUDFtNURpdGdMNTJNSGtkY3JNR0JNTVRLemJYMFJIJTJGbFZyYnVCbm1BSVlFJTJGOCUzRA; __gads=ID=3810118b4179caf9:T=1741474345:RT=1759498761:S=ALNI_Mb1fKBItFi7PSu9wpg-NsuGfTWCow; __gpi=UID=00001059cb19339e:T=1741474345:RT=1759498761:S=ALNI_MZbJJhh_8_iZvm8khCzW1rm4FSrEw; __eoi=ID=3154a636990f44e0:T=1759017499:RT=1759498761:S=AA-AfjZXkqJPzKHCxv2holLFAC-B; FCNEC=%5B%5B%22AKsRol85Xg6A7fNcHiD8oeQa2GzasAGww9dQzCbQ9rHOizhV6rria8saPrOsQHPp0tYL0j7gcQKmQH5AB1J4XZeTFtox-PjZjJp8V-7L3qeal6m5AMQDqCHoeE9OkLJlPid_J9rAau1fKNh2fnODTx6ZWBvkLRlaLQ%3D%3D%22%5D%5D; cto_bundle=wECa0V8lMkIyT2FGcFglMkJUY3JxVWt2MlNrc2YlMkI2UzdoaHEyRzEwa0hrcjJtRiUyRmRNVmNWZmFjQmNpNFFicGNkJTJGakEzT3oxVHVRdmplRXl3QnVOTjVpU0prV1lLMXdHS1RORzBRT2l5JTJGRDBUdFJoTUhMcUl1VE13ZmlRNkxGNnRGVkFBbXd2WFkxVWJKMmtXRmdPTXBnRW5qN2xSTGclM0QlM0Q; SESSION%5FVAL=78095992%2E41; _ga_0LP5MLQS7E=GS2.1.s1759498760$o4$g1$t1759498901$j12$l0$h0'
    }

    try:
        # 由於此函式被 st.cache_data 快取，這個訊息只會在第一次執行或快取過期時顯示
        # st.write("正在從 Goodinfo 爬取資料...") # 在主程式已有 spinner，這裡可省略
        res = requests.get(url, headers=headers, timeout=20)
        res.raise_for_status()
        res.encoding = 'utf-8'

        soup = BeautifulSoup(res.text, 'lxml')
        table = soup.select_one('#tblStockList')

        if not table:
            st.error("在 Goodinfo 頁面中找不到指定的表格 (ID: tblStockList)。請檢查 Cookie 是否已過期，或網站結構已變更。")
            return None
            
        dfs = pd.read_html(StringIO(str(table)))
        if not dfs:
            st.error("Pandas 無法從 Goodinfo 的 HTML 表格中解析出資料。")
            return None
        
        df = dfs[0]
        # 清理多層級的欄位名稱
        df.columns = df.columns.get_level_values(0)
        # 移除欄位名稱中的所有空白字元
        df.columns = df.columns.str.replace(r'\s+', '', regex=True)
        
        # 統一欄位名稱，將 '代號' 改為 '代碼' 以便後續處理
        if '代號' in df.columns:
            df.rename(columns={'代號': '代碼'}, inplace=True)

        # 選擇並回傳需要的欄位
        columns_to_keep = ['代碼', '名稱', '市場', '股價日期', '成交', '漲跌價', '漲跌幅', '成交張數']
        # 確保只選擇存在的欄位，避免因網站改版找不到欄位而出錯
        existing_columns = [col for col in columns_to_keep if col in df.columns]
        
        return df[existing_columns]

    except requests.exceptions.RequestException as e:
        st.error(f"請求 Goodinfo 失敗: {e}")
        return None
    except Exception as e:
        st.error(f"處理 Goodinfo 資料時發生未預期錯誤: {e}")
        return None
