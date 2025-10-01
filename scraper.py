# --- START OF FILE scraper.py ---

import requests
from bs4 import BeautifulSoup
import pandas as pd
from io import StringIO
import traceback

def scrape_goodinfo():
    """
    從 Goodinfo! 台灣股市資訊網爬取符合特定篩選條件的股票列表。

    Returns:
        pandas.DataFrame: 包含股票代號、名稱等資訊的 DataFrame。
                          如果爬取失敗或沒有資料，則回傳一個空的 DataFrame。
    """
    rel = 'https://goodinfo.tw/tw2/StockList.asp?SEARCH_WORD=&SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&SHEET2=%E6%97%A5&RPT_TIME=%E6%9C%80%E6%96%B0%E8%B3%87%E6%96%99&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6&STOCK_CODE=&RANK=0&SORT_FIELD=&SORT=&FL_SHEET=%E4%BA%A4%E6%98%93%E7%8B%80%E6%B3%81&FL_SHEET2=%E6%97%A5&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83&FL_ITEM0=%E7%95%B6%E6%97%A5%EF%BC%9A%E7%B4%85K%E6%A3%92%E6%A3%92%E5%B9%85%28%25%29&FL_VAL_S0=2%2E5&FL_VAL_E0=10&FL_VAL_CHK0=&FL_ITEM1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29&FL_VAL_S1=5000&FL_VAL_E1=900000&FL_VAL_CHK1=&FL_ITEM2=&FL_VAL_S2=&FL_VAL_E2=&FL_VAL_CHK2=&FL_ITEM3=%E5%9D%87%E7%B7%9A%E4%B9%96%E9%9B%A2%28%25%29%E2%80%93%E5%AD%A3&FL_VAL_S3=%2D5&FL_VAL_E3=5&FL_VAL_CHK3=&FL_ITEM4=K%E5%80%BC+%28%E9%80%B1%29&FL_VAL_S4=0&FL_VAL_E4=50&FL_VAL_CHK4=&FL_ITEM5=&FL_VAL_S5=&FL_VAL_E5=&FL_VAL_CHK5=&FL_ITEM6=&FL_VAL_S6=&FL_VAL_E6=&FL_VAL_CHK6=&FL_ITEM7=&FL_VAL_S7=&FL_VAL_E7=&FL_VAL_CHK7=&FL_ITEM8=&FL_VAL_S8=&FL_VAL_E8=&FL_VAL_CHK8=&FL_ITEM9=&FL_VAL_S9=&FL_VAL_E9=&FL_VAL_CHK9=&FL_ITEM10=&FL_VAL_S10=&FL_VAL_E10=&FL_VAL_CHK10=&FL_ITEM11=&FL_VAL_S11=&FL_VAL_E11=&FL_VAL_CHK11=&FL_RULE0=KD%7C%7C%E9%80%B1K%E5%80%BC+%E2%86%97%40%40%E9%80%B1KD%E8%B5%B0%E5%8B%A2%40%40K%E5%80%BC+%E2%86%97&FL_RULE_CHK0=&FL_RULE1=%E5%9D%87%E7%B7%9A%E4%BD%8D%E7%BD%AE%7C%7C%E6%9C%88%2F%E5%AD%A3%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E5%9D%87%E5%83%B9%E7%B7%9A%E7%A9%BA%E9%A0%AD%E6%8E%92%E5%88%97%40%40%E6%9C%88%2F%E5%AD%A3&FL_RULE_CHK1=&FL_RULE2=&FL_RULE_CHK2=&FL_RULE3=&FL_RULE_CHK3=&FL_RULE4=&FL_RULE_CHK4=&FL_RULE5=&FL_RULE_CHK5=&FL_RANK0=&FL_RANK1=&FL_RANK2=&FL_RANK3=&FL_RANK4=&FL_RANK5=&FL_FD0=K%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7CD%E5%80%BC+%28%E6%97%A5%29%7C%7C1%7C%7C0&FL_FD1=%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%7C%7C0%7C%7C%3E%7C%7C%E6%98%A8%E6%97%A5%E6%88%90%E4%BA%A4%E5%BC%B5%E6%95%B8+%28%E5%BC%B5%29%7C%7C1%2E3%7C%7C0&FL_FD2=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD3=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD4=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD5=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&MY_FL_RULE_NM=%E9%81%B8%E8%82%A1103'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
        'Cookie': 'CLIENT%5FID=20240528214234554%5F114%2E37%2E208%2E80; _ga=GA1.1.324892192.1682147720; CLIENT%5FID=20240528214234554%5F114%2E37%2E208%2E80; LOGIN=EMAIL=yjc5760%40gmail%2Ecom&USER%5FNM=%E9%99%B3%E7%9B%8A%E7%A6%8E&ACCOUNT%5FID=107359590931917990151&ACCOUNT%5FVENDOR=Google&NO%5FEXPIRE=T; TW_STOCK_BROWSE_LIST=9941%7C1316%7C2330; SCREEN_SIZE=WIDTH=914&HEIGHT=832; IS_TOUCH_DEVICE=F; __gads=ID=1940bede5199c2cb:T=1708824409:RT=1718547228:S=ALNI_MaWGUZ9M1Cdq99AluoEvAPfGTPV2Q; __gpi=UID=00000d140c1181b2:T=1708824409:RT=1718547228:S=ALNI_MbCI3_skouX3wvwLB2-DlL6HaplQg; __eoi=ID=ea163367123141f9:T=1708824409:RT=1718547228:S=AA-AfjaKpwDpQL6MD1eSTlZ4MwFm; FCNEC=%5B%5B%22AKsRol_3ueFYccsSvPj2tev8DbpQxazXgW-pPqyQ_kShBO7xD_uKm5vjnDG0gDH7J6jfsUOTfWDDVJfRYyVto8HVHA6b_d9dnTZCp0fTMt8blXABxWyrRuqGCDNFa_8iLxD1o1hHAvspPcT15FvdfQVPy_e7TANvrw%3D%3D%22%5D%5D; _ga_0LP5MLQS7E=GS1.1.1718546679.19.1.1718547331.36.0.0'
    }
    
    try:
        res = requests.get(rel, headers=headers, timeout=15)
        res.raise_for_status() # 確保請求成功
        res.encoding = 'utf-8'
        
        soup = BeautifulSoup(res.text, 'lxml')
        data_div = soup.select_one('#txtStockListData')
        
        if not data_div:
            print("警告: 在頁面中找不到 ID 為 'txtStockListData' 的元素。")
            return pd.DataFrame()

        # 使用 pd.read_html 讀取表格
        dfs = pd.read_html(StringIO(data_div.prettify()))
        
        if len(dfs) < 2:
            print("警告: 讀取到的表格數量不足。")
            return pd.DataFrame()
            
        df = dfs[1]
        df.columns = df.columns.get_level_values(0)
        
        # 選擇需要的欄位
        required_cols = ['代號', '名稱', '市  場', '股價  日期', '成交', '漲跌  價', '漲跌  幅', '成交  張數']
        df = df[required_cols]
        
        # 清理資料
        df['成交'] = pd.to_numeric(df['成交'], errors='coerce')
        df.dropna(subset=['代號', '名稱'], inplace=True)
        
        print(f"成功爬取到 {len(df)} 筆股票資料。")
        return df

    except requests.exceptions.RequestException as e:
        print(f"爬取 Goodinfo 網站時發生網路錯誤: {e}")
        return pd.DataFrame()
    except Exception as e:
        print(f"處理爬取資料時發生未預期錯誤: {e}")
        traceback.print_exc() # 印出詳細錯誤堆疊
        return pd.DataFrame()

# 如果直接執行此腳本，則進行測試
if __name__ == '__main__':
    print("正在測試爬蟲功能...")
    stock_df = scrape_goodinfo()
    if not stock_df.empty:
        print("爬取結果預覽：")
        print(stock_df.head())
    else:
        print("爬蟲未回傳任何資料。")