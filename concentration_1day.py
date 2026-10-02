# 1日籌碼集中度.py (已修改欄位顯示)

import pandas as pd
from io import StringIO
from bs4 import BeautifulSoup

from scrape_utils import ScrapeError, fetch_html

REQUIRED_COLUMNS = ['代碼', '5日集中度', '10日集中度', '20日集中度', '10日均量']


def fetch_stock_concentration_data() -> pd.DataFrame:
    """
    爬取股票籌碼集中度資料並進行數據清理。

    Returns:
        pd.DataFrame: 清理後的股票集中度資料。
    Raises:
        ScrapeError: 連線失敗／被擋／網頁改版／沒有資料（kind 欄位標示原因）。
    """
    url = 'http://asp.peicheng.com.tw/main/report/dream_report/%E7%B1%8C%E7%A2%BC%E9%9B%86%E4%B8%AD%E5%BA%A61%E6%97%A5%E6%8E%92%E8%A1%8C.htm'
    html = fetch_html(url, label="籌碼集中度網站（peicheng）", encoding='big5')

    soup = BeautifulSoup(html, 'lxml')
    target_table = soup.select_one(r'#籌碼集中度排行轉網頁\.\(排程\)_3148')
    try:
        if target_table is not None:
            dfs = pd.read_html(StringIO(str(target_table)), flavor='lxml')
        else:
            # 表格 ID 變了：改從整頁所有表格中找含「代碼」的那一張
            print("警告：找不到指定的表格 ID，改為掃描整頁表格。")
            dfs = [t for t in pd.read_html(StringIO(html)) if '代碼' in t.to_string()]
    except ValueError:                              # pandas: No tables found
        dfs = []
    if not dfs:
        raise ScrapeError("layout", "籌碼集中度網頁中找不到含「代碼」的表格")

    df0 = dfs[0]
    df0.columns = df0.columns.get_level_values(0)

    header_row_index = -1
    for i, row in df0.iterrows():
        if '代碼' in str(row.to_string()):
            header_row_index = i
            break
    if header_row_index == -1:
        raise ScrapeError("layout", "籌碼集中度表格中找不到「代碼」標頭列")

    df1 = df0.iloc[header_row_index + 1:].copy()
    df1.columns = df0.iloc[header_row_index].values
    df1.reset_index(drop=True, inplace=True)

    # 修正可能的命名差異 (例如 "股票名稱" vs "名稱")
    if '名稱' in df1.columns and '股票名稱' not in df1.columns:
        df1.rename(columns={'名稱': '股票名稱'}, inplace=True)

    missing = [c for c in REQUIRED_COLUMNS if c not in df1.columns]
    if missing:
        raise ScrapeError("layout", f"籌碼集中度表格缺少欄位 {missing}；目前欄位：{list(df1.columns)[:15]}")

    last_valid_index = df1['代碼'].apply(pd.to_numeric, errors='coerce').last_valid_index()
    if last_valid_index is not None:
        df1 = df1.iloc[:last_valid_index + 1]

    numeric_columns = ['1日集中度', '5日集中度', '10日集中度', '20日集中度', '60日集中度', '120日集中度', '10日均量']
    numeric_columns = [c for c in numeric_columns if c in df1.columns]
    for col in numeric_columns:
        df1[col] = pd.to_numeric(df1[col], errors='coerce')
    df1 = df1.dropna(subset=numeric_columns)

    if df1.empty:
        raise ScrapeError("empty", "籌碼集中度表格沒有任何有效資料（網站可能尚未更新）")

    print(f"籌碼集中度資料獲取並清理成功，共 {len(df1)} 筆。")
    return df1


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
    try:
        stock_data = fetch_stock_concentration_data()
    except ScrapeError as e:
        print(f"失敗：{e}")
        stock_data = None

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