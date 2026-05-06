"""
concentration_1day.py (已修正縮排與語法對齊)
爬取籌碼集中度排行資料，已加入 retry 重試機制。
"""

import requests
import pandas as pd
from io import StringIO
from bs4 import BeautifulSoup
from utils import fetch_html_with_retry

_URL = (
    'http://asp.peicheng.com.tw/main/report/dream_report/'
    '%E7%B1%8C%E7%A2%BC%E9%9B%86%E4%B8%AD%E5%BA%A61%E6%97%A5%E6%8E%92%E8%A1%8C.htm'
)

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/108.0.0.0 Safari/537.36'
    ),
    'Accept': (
        'text/html,application/xhtml+xml,application/xml;q=0.9,'
        'image/avif,image/webp,image/apng,*/*;q=0.8,'
        'application/signed-exchange;v=b3;q=0.9'
    ),
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Connection': 'keep-alive',
}


def fetch_stock_concentration_data() -> pd.DataFrame | None:
    """
    爬取股票籌碼集中度資料並進行數據清理。
    此版本使用 BeautifulSoup 增強解析的穩定性，並加入 retry 重試機制。
    """
    session = requests.Session()
    session.headers.update(_HEADERS)

    # 使用 retry 機制抓取（最多重試 3 次）
    html_text = fetch_html_with_retry(_URL, session, retries=3, delay=2.0)
    if html_text is None:
        return None

    # 集中度頁面使用 big5 編碼，需重新解碼
    try:
        raw_bytes = html_text.encode('latin-1')
        html_text = raw_bytes.decode('big5', errors='replace')
    except Exception:
        pass  # 若已是正確編碼則略過

    try:
        soup = BeautifulSoup(html_text, 'lxml')
        target_table = soup.select_one('#籌碼集中度排行轉網頁\\.\\(排程\\)_3148')

        if not target_table:
            print("錯誤：使用 BeautifulSoup 找不到指定的表格 ID。網站結構可能已變更。")
            dfs = pd.read_html(StringIO(html_text))
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
            print("錯誤：在表格中找不到包含 '代碼' 的標頭列。")
            return None

        df1 = df0.iloc[header_row_index + 1:].copy()
        df1.columns = df0.iloc[header_row_index].values
        df1.reset_index(drop=True, inplace=True)

        last_valid_index = df1['代碼'].apply(pd.to_numeric, errors='coerce').last_valid_index()
        if last_valid_index is not None:
            df1 = df1.iloc[:last_valid_index + 1]

        # 確保所有需要的欄位都存在
        all_columns = [
            '編號', '代碼', '股票名稱', '1日集中度', '5日集中度',
            '10日集中度', '20日集中度', '60日集中度', '120日集中度', '10日均量'
        ]

        # 修正可能的命名差異（例如 "股票名稱" vs "名稱"）
        if '名稱' in df1.columns and '股票名稱' not in df1.columns:
            df1.rename(columns={'名稱': '股票名稱'}, inplace=True)

        numeric_columns = [
            '1日集中度', '5日集中度', '10日集中度', '20日集中度',
            '60日集中度', '120日集中度', '10日均量'
        ]
        for col in numeric_columns:
            if col in df1.columns:
                df1[col] = pd.to_numeric(df1[col], errors='coerce')

        df1.dropna(subset=numeric_columns, inplace=True)

        print("籌碼集中度資料獲取並清理成功。")
        return df1

    except Exception as e:
        print(f"錯誤：處理資料時發生未知錯誤: {e}")
        return None


def filter_stock_data(df, min_volume=2000) -> pd.DataFrame | None:
    """
    篩選符合特定條件的股票，並只回傳指定的欄位。
    """
    if df is None:
        return None
        
    try:
        # 步驟 1: 根據條件篩選股票
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

        # 步驟 3: 確保所有要顯示的欄位都存在於 DataFrame 中，避免出錯
        final_columns = [col for col in display_columns if col in filtered_df.columns]

        return filtered_df[final_columns]

    except KeyError as e:
        print(f"篩選時發生欄位不存在的錯誤：{e}")
        return None
    except Exception as e:
        print(f"篩選資料時發生未知錯誤：{e}")
        return None
