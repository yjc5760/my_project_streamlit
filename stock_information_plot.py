import pandas as pd
import numpy as np
import datetime
import twstock

import finmind_client
from scrape_utils import ScrapeError, disk_memo, fetch_html
from bs4 import BeautifulSoup
from io import StringIO

# --- 新增 Plotly 相關導入 ---
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def get_stock_code(stock_identifier):
    """
    根據股票代碼或名稱查找股票代碼。(此函式邏輯不變)
    """
    try:
        identifier_str = str(stock_identifier).strip()
        if identifier_str in twstock.codes:
            return identifier_str
        for code, stock_info in twstock.codes.items():
            if stock_info.name == identifier_str:
                return code
        for code, stock_info in twstock.codes.items():
            if identifier_str in stock_info.name:
                return code
        return None
    except Exception as e:
        print(f"在 twstock 中查找 '{stock_identifier}' 時發生錯誤: {e}")
        return None

HOLDERS_URL = 'https://norway.twsthr.info/StockHolders.aspx?stock={code}'
HOLDERS_PCT_COL = '>400張大股東持有百分比'


@disk_memo("holders", 12 * 3600)                    # 週資料：12 小時內不重抓
def fetch_major_holders(stock_code):
    """
    抓集保大戶（持股 > 400 張）週資料。回傳 DataFrame（新到舊）：資料日期, >400張大股東持有百分比。
    連線失敗／被擋 raise ScrapeError（已重試、已分類）；頁面／欄位結構異常 raise ValueError。
    繪圖（plot_stock_major_shareholders）與選股（quality_screener）共用。
    """
    stock_code = str(stock_code).strip()
    html = fetch_html(HOLDERS_URL.format(code=stock_code), label="大戶持股（norway.twsthr.info）",
                      encoding='utf-8')

    soup = BeautifulSoup(html, 'lxml')
    table = soup.select_one('#Details')
    if not table:
        raise ValueError(f"在股票 {stock_code} 的資料頁面中找不到持股資料表。")

    df = pd.read_html(StringIO(str(table)))[0]

    # 處理 MultiIndex：攤平為單層
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)

    # 提升第一列為欄位名稱
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True).dropna(how='all')

    # 去除最後一列若為「顏色識別」說明列
    if not df.empty:
        last_row_str = ' '.join(str(v) for v in df.iloc[-1].values)
        if '顏色識別' in last_row_str:
            df = df.iloc[:-1]

    # 正規化欄位名稱：強制轉 str 再去空白，避免 float NaN 欄位名稱導致崩潰
    df.columns = [str(c).strip().replace(' ', '').replace('\xa0', '') for c in df.columns]

    # 用名稱關鍵字找目標欄位（取代硬編碼 iloc[:, [2, 7]]）
    date_col = next((c for c in df.columns if '日期' in c), None)
    pct_col = next((c for c in df.columns if '持有' in c and ('%' in c or '百分比' in c)), None)
    if pct_col is None:
        pct_col = next((c for c in df.columns if '%' in c and c != date_col), None)
    if date_col is None or pct_col is None:
        raise ValueError(
            f"股票 {stock_code} 持股表格欄位結構異常，找不到日期欄（{date_col}）或持有百分比欄（{pct_col}）。"
            f"現有欄位：{df.columns.tolist()}")

    df = df[[date_col, pct_col]].reset_index(drop=True)
    df.columns = ['資料日期', HOLDERS_PCT_COL]
    df['資料日期'] = pd.to_datetime(df['資料日期'], format='%Y%m%d', errors='coerce')
    df[HOLDERS_PCT_COL] = pd.to_numeric(df[HOLDERS_PCT_COL], errors='coerce')
    df = df.dropna()
    if df.empty:
        raise ValueError(f"股票 {stock_code} 清理後無有效的大戶持股資料。")
    return df.sort_values('資料日期', ascending=False).reset_index(drop=True)


def plot_stock_major_shareholders(stock_identifier):
    """
    動態爬取大戶持股資料並用 Plotly 繪製圖表（近 12 週）。
    返回 (figure, error_message)
    """
    try:
        stock_info = twstock.codes[str(stock_identifier)]
        stock_code = stock_info.code
        stock_name = stock_info.name
    except KeyError:
        return None, f"錯誤: 在 twstock 資料庫中找不到股票 '{stock_identifier}'"

    try:
        print(f"正在從網路抓取股票 {stock_code} 的大戶持股資料...")
        df = fetch_major_holders(stock_code)

        # 繪製圖表 (近12週)
        df_plot = df.head(12).iloc[::-1].reset_index(drop=True)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_plot['資料日期'],
            y=df_plot[HOLDERS_PCT_COL],
            mode='lines+markers+text',
            name='大戶持股比例',
            line_shape='hv', # 階梯線
            text=[f'{v:.2f}%' for v in df_plot[HOLDERS_PCT_COL]],
            textposition="top center"
        ))

        fig.update_layout(
            title=f"{stock_name} ({stock_code}) 大戶股權變化圖 (持股>400張，近12週)",
            xaxis_title='日期 (週為單位)',
            yaxis_title='大戶股權比例 (%)',
            xaxis_tickformat='%Y-%m-%d'
        )
        print(f"大戶持股圖表物件已成功生成: {stock_code}")
        return fig, None

    except ScrapeError as e:
        return None, f"錯誤：抓取股票 {stock_code} 大戶持股資料失敗：{e}"
    except ValueError as e:
        return None, f"錯誤：{e}"
    except Exception as e:
        return None, f"錯誤：處理股票 {stock_code} 大戶持股資料時發生未預期錯誤: {e}"

def plot_stock_revenue_trend(stock_identifier):
    """
    【重大修改】從 FinMind API 讀取資料並用 Plotly 繪製營收趨勢圖。
    返回 (figure, error_message)
    """
    try:
        stock_info = twstock.codes[str(stock_identifier)]
        stock_code = stock_info.code
        stock_name = stock_info.name
    except KeyError:
        return None, f"錯誤: 在 twstock 資料庫中找不到股票 '{stock_identifier}'"

    try:
        # 經由 finmind_client 取得（共用連線、額度保護）
        today = datetime.date.today()
        revenue_df = finmind_client.month_revenue(stock_code, datetime.date(today.year - 3, 1, 1), today)

        # 數據處理 (與原版相同)
        revenue_df['date'] = pd.to_datetime(revenue_df['date'])
        revenue_df.sort_values('date', inplace=True)
        revenue_df['Revenue'] = revenue_df['revenue'] / 1000
        revenue_df['Year'] = pd.to_numeric(revenue_df['revenue_year'])
        revenue_df['Month'] = pd.to_numeric(revenue_df['revenue_month'])
        revenue_df.sort_values(by=['Month', 'Year'], inplace=True)
        revenue_df['YoY'] = revenue_df.groupby('Month')['Revenue'].pct_change(periods=1) * 100

        # 繪圖部分
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        years = sorted(revenue_df['Year'].unique())
        current_year = datetime.date.today().year

        for year in years:
            data = revenue_df[revenue_df['Year'] == year]
            if not data.empty:
                fig.add_trace(
                    go.Scatter(x=data['Month'], y=data['Revenue'], mode='lines+markers', name=f'{year}年'),
                    secondary_y=False,
                )
        
        current_year_data = revenue_df[revenue_df['Year'] == current_year].copy()
        if not current_year_data.empty:
            fig.add_trace(
                go.Bar(x=current_year_data['Month'], y=current_year_data['YoY'], name=f'{current_year} YoY', opacity=0.3),
                secondary_y=True,
            )

        fig.update_layout(
            title_text=f"{stock_code} {stock_name} 營收變化圖",
            xaxis=dict(tickmode='array', tickvals=list(range(1, 13)), ticktext=[f'{i}月' for i in range(1, 13)])
        )
        fig.update_yaxes(title_text="月營收 (千元)", secondary_y=False)
        fig.update_yaxes(title_text="年增率 (%)", secondary_y=True)

        return fig, None

    except finmind_client.FinMindError as e:
        return None, f"錯誤: {e}"
    except Exception as e:
        return None, f"錯誤: 處理營收資料時發生未預期錯誤: {e}"
