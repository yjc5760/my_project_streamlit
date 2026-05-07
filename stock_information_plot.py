import pandas as pd
import numpy as np
import os
import datetime
import requests
import twstock
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

def plot_stock_major_shareholders(stock_identifier):
    """
    【重大修改】動態爬取大戶持股資料並用 Plotly 繪製圖表。
    返回 (figure, error_message)
    """
    try:
        stock_info = twstock.codes[str(stock_identifier)]
        stock_code = stock_info.code
        stock_name = stock_info.name
    except KeyError:
        return None, f"錯誤: 在 twstock 資料庫中找不到股票 '{stock_identifier}'"

    url = f'https://norway.twsthr.info/StockHolders.aspx?stock={stock_code}'
    headers = {'User-Agent': 'Mozilla/5.0'}

    try:
        print(f"正在從網路抓取股票 {stock_code} 的大戶持股資料...")
        res = requests.get(url, headers=headers, timeout=20)
        res.raise_for_status()
        res.encoding = 'utf-8'

        soup = BeautifulSoup(res.text, 'lxml')
        table = soup.select_one('#Details')
        if not table:
            return None, f"錯誤：在股票 {stock_code} 的資料頁面中找不到持股資料表。"

        # 資料清理 (與原版類似)
        df = pd.read_html(StringIO(str(table)))[0]
        df.columns = df.iloc[0]
        df = df.iloc[1:].reset_index(drop=True).dropna(how='all')
        if not df.empty and '顏色識別' in ' '.join(map(str, df.iloc[-1].values)):
            df = df.iloc[:-1]
        df = df.iloc[:, [2, 7]].reset_index(drop=True)
        df.columns = ['資料日期', '>400張大股東持有百分比']
        df['資料日期'] = pd.to_datetime(df['資料日期'], format='%Y%m%d', errors='coerce')
        df['>400張大股東持有百分比'] = pd.to_numeric(df['>400張大股東持有百分比'], errors='coerce')
        df.dropna(inplace=True)
        
        if df.empty:
            return None, f"錯誤：股票 {stock_code} 清理後無有效的大戶持股資料。"

        # 繪製圖表 (近12週)
        df_plot = df.head(12).iloc[::-1].reset_index(drop=True)
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_plot['資料日期'], 
            y=df_plot['>400張大股東持有百分比'],
            mode='lines+markers+text',
            name='大戶持股比例',
            line_shape='hv', # 階梯線
            text=[f'{v:.2f}%' for v in df_plot['>400張大股東持有百分比']],
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

    except requests.exceptions.RequestException as e:
        return None, f"錯誤：抓取股票 {stock_code} 大戶持股資料時發生網路錯誤: {e}"
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
        # 獲取資料 (與原版相同)
        finmind_api_token = os.getenv('FINMIND_API_TOKEN')
        finmind_url = "https://api.finmindtrade.com/api/v4/data"
        start_date = f"{datetime.date.today().year - 3}-01-01"
        end_date = datetime.date.today().strftime('%Y-%m-%d')
        params = {"dataset": "TaiwanStockMonthRevenue", "data_id": stock_code, "start_date": start_date, "end_date": end_date}
        headers = {"Authorization": f"Bearer {finmind_api_token}"} if finmind_api_token else {}
        response = requests.get(finmind_url, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        raw_data = response.json()
        if raw_data.get("status") != 200:
             raise ValueError(f"FinMind API 錯誤: {raw_data.get('msg', '未知錯誤')}")
        revenue_df = pd.DataFrame(raw_data.get('data'))
        if revenue_df.empty:
            raise ValueError("FinMind API 未回傳營收資料。")

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

    except requests.exceptions.RequestException as e:
        return None, f"錯誤: 連線 FinMind API 時發生錯誤: {e}"
    except ValueError as e:
        return None, f"錯誤: 處理 FinMind API 資料時發生錯誤: {e}"
    except Exception as e:
        return None, f"錯誤: 獲取營收資料時發生未預期錯誤: {e}"