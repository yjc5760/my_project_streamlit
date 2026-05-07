# 1日籌碼集中度.py (已修改欄位顯示)

import requests
import pandas as pd
from io import StringIO
from bs4 import BeautifulSoup

def fetch_stock_concentration_data():
    """
    爬取股票籌碼集中度資料並進行數據清理。
    此版本使用 BeautifulSoup 增強解析的穩定性。
    
    Returns:
        pd.DataFrame or None: 清理後的股票集中度資料，或在發生錯誤時返回 None。
    """
    url = 'http://asp.peicheng.com.tw/main/report/dream_report/%E7%B1%8C%E7%A2%BC%E9%9B%86%E4%B8%AD%E5%BA%A61%E6%97%A5%E6%8E%92%E8%A1%8C.htm'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive',
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        response.encoding = 'big5'

        soup = BeautifulSoup(response.text, 'lxml')
        target_table = soup.select_one('#籌碼集中度排行轉網頁\.\(排程\)_3148')

        if not target_table:
            print("錯誤：使用 BeautifulSoup 找不到指定的表格 ID。網站結構可能已變更。")
            dfs = pd.read_html(StringIO(response.text))
        else:
            dfs = pd.read_html(StringIO(str(target_table)), flavor='lxml')

        if not dfs:
            print("錯誤：pandas 無法從 HTML 中解析出任何表格。")
            return None

        df0 = dfs[0]
        df0.columns = df0.columns.get_level_values(0)
        
        header_row_index = -1
        for i, row in df0.iterrows():
            if '代碼' in str(row.to_string()):
                header_row_index = i
                break
        
        if header_row_index == -1:
            print("錯誤：在表格中找不到包含 '代碼' 的標頭行。")
            return None

        df1 = df0.iloc[header_row_index + 1:].copy()
        df1.columns = df0.iloc[header_row_index].values
        df1.reset_index(drop=True, inplace=True)

        last_valid_index = df1['代碼'].apply(pd.to_numeric, errors='coerce').last_valid_index()
        if last_valid_index is not None:
            df1 = df1.iloc[:last_valid_index + 1]

        # 確保所有需要的欄位都存在
        all_columns = ['編號', '代碼', '股票名稱', '1日集中度', '5日集中度', '10日集中度', '20日集中度', '60日集中度', '120日集中度', '10日均量']
        # 檢查欄位是否存在，並修正可能的命名差異 (例如 "股票名稱" vs "名稱")
        if '名稱' in df1.columns and '股票名稱' not in df1.columns:
            df1.rename(columns={'名稱': '股票名稱'}, inplace=True)
            
        numeric_columns = ['1日集中度', '5日集中度', '10日集中度', '20日集中度', '60日集中度', '120日集中度', '10日均量']
        for col in numeric_columns:
            if col in df1.columns:
                df1[col] = pd.to_numeric(df1[col], errors='coerce')
        
        df1.dropna(subset=numeric_columns, inplace=True)
        
        print("籌碼集中度資料獲取並清理成功。")
        return df1

    except requests.exceptions.Timeout:
        print(f"錯誤：請求超時。目標網站 '{url}' 回應過慢。")
        return None
    except requests.exceptions.RequestException as e:
        print(f"錯誤：爬取網頁時發生網路錯誤: {e}")
        return None
    except Exception as e:
        print(f"錯誤：處理資料時發生未知錯誤: {e}")
        return None

def filter_stock_data(df, min_volume=2000):
    """
    篩選符合特定條件的股票，並只回傳指定的欄位。
    """
    if df is None:
        return None
    try:
        # 步驟 1: 根據條件篩選股票 (邏輯不變)
        filtered_df = df[
            (df['5日集中度'] > df['10日集中度']) &
            (df['10日集中度'] > df['20日集中度']) &
            (df['5日集中度'] > 0) &
            (df['10日集中度'] > 0) &
            (df['10日均量'] > min_volume)
        ].copy()

        # 步驟 2: 定義想要顯示的欄位列表
        display_columns = [
            '編號', '代碼', '股票名稱', '1日集中度', '5日集中度', 
            '10日集中度', '20日集中度', '60日集中度', '120日集中度', '10日均量'
        ]
        
        # 步驟 3: 從篩選後的結果中，只選取這些欄位並回傳
        # 確保所有要顯示的欄位都存在於 DataFrame 中，避免出錯
        final_columns = [col for col in display_columns if col in filtered_df.columns]
        
        return filtered_df[final_columns]
    
    except KeyError as e:
        print(f"篩選時發生欄位不存在的錯誤：{e}")
        return None

if __name__ == '__main__':
    """用於獨立測試腳本"""
    print("正在獲取籌碼集中度資料...")
    stock_data = fetch_stock_concentration_data()

    if stock_data is not None:
        print("\n資料獲取成功，開始篩選股票...")
        filtered_stocks = filter_stock_data(stock_data)

        if filtered_stocks is not None and not filtered_stocks.empty:
            print("\n篩選後的股票 (僅顯示指定欄位)：")
            print(filtered_stocks)
        elif filtered_stocks is not None:
            print("\n沒有找到符合篩選條件的股票。")
        else:
            print("\n篩選過程中發生錯誤。")