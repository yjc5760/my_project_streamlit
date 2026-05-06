"""
stock_information_plot.py (優化版)
繪製個股月營收趨勢圖與主要股東持股比例圖。
get_stock_code 已改為從 utils.py 匯入，統一維護。
"""

import pandas as pd
import numpy as np
import os
import datetime
import requests
import twstock
from bs4 import BeautifulSoup
from io import StringIO

import plotly.graph_objects as go
from plotly.subplots import make_subplots

# 從 utils.py 匯入共用的股票代碼查詢函式
from utils import get_stock_code


def plot_stock_major_shareholders(stock_identifier):
        """
            根據股票代碼或名稱，爬取並繪製主要股東持股比例圖。
                """
        stock_code = get_stock_code(stock_identifier)
        if not stock_code:
                    print(f"找不到股票：{stock_identifier}")
                    return None

        url = f"https://goodinfo.tw/tw/StockBigHolder.asp?STOCK_ID={stock_code}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            ),
            "Referer": "https://goodinfo.tw/",
        }

        cookie = os.getenv('GOODINFO_COOKIE_MY_STOCK', '')
        if cookie:
            headers["Cookie"] = cookie

        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            table = soup.find('table', {'id': 'tblStockHolder'})
            if table is None:
                            print(f"找不到 {stock_code} 的股東資料表格")
                            return None
    
            dfs = pd.read_html(StringIO(str(table)))
            if not dfs:
                            return None
    
            df = dfs[0]
            if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(-1)
    
            df.columns = df.columns.astype(str).str.replace(r'\s+', '', regex=True)
    
            holder_col = next((c for c in df.columns if '股東' in c or '名稱' in c), None)
            ratio_col = next((c for c in df.columns if '持股' in c or '比例' in c or '%' in c), None)
    
            if not holder_col or not ratio_col:
                            print(f"無法識別 {stock_code} 股東資料的欄位: {df.columns.tolist()}")
                            return None
    
            df_plot = df[[holder_col, ratio_col]].copy()
            df_plot[ratio_col] = pd.to_numeric(df_plot[ratio_col], errors='coerce')
            df_plot = df_plot.dropna(subset=[ratio_col]).head(15)
    
            if df_plot.empty:
                            return None
    
            stock_info = twstock.codes.get(stock_code)
            stock_name = stock_info.name if stock_info else stock_code
    
            fig = go.Figure(go.Bar(
                            x=df_plot[ratio_col],
                            y=df_plot[holder_col],
                            orientation='h',
                            marker_color='steelblue',
                            text=df_plot[ratio_col].apply(lambda x: f'{x:.2f}%'),
                            textposition='outside',
            ))
            fig.update_layout(
                            title=f'{stock_name} ({stock_code}) 主要股東持股比例',
                            xaxis_title='持股比例 (%)',
                            yaxis=dict(autorange='reversed'),
                            height=500,
                            margin=dict(l=200, r=50, t=60, b=50),
            )
            return fig

        except Exception as e:
            print(f"繪製 {stock_code} 股東圖時發生錯誤: {e}")
            return None


def plot_stock_revenue_trend(stock_identifier):
        """
            根據股票代碼或名稱，爬取並繪製月營收趨勢圖。
                """
        stock_code = get_stock_code(stock_identifier)
        if not stock_code:
                    print(f"找不到股票：{stock_identifier}")
                    return None

        url = (
            f"https://goodinfo.tw/tw/StockMonthlyRevenue.asp"
            f"?STOCK_ID={stock_code}"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            ),
            "Referer": "https://goodinfo.tw/",
        }

        cookie = os.getenv('GOODINFO_COOKIE_MY_STOCK', '')
        if cookie:
            headers["Cookie"] = cookie

        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            response.encoding = 'utf-8'

        dfs = pd.read_html(StringIO(response.text), flavor='lxml')
        if not dfs:
                        print(f"找不到 {stock_code} 的月營收資料")
                        return None

        # 尋找包含年度/月份資訊的表格
        df = None
        for d in dfs:
                        cols = d.columns.get_level_values(-1) if isinstance(d.columns, pd.MultiIndex) else d.columns
                        cols_str = ' '.join(str(c) for c in cols)
                        if '月' in cols_str or '營收' in cols_str or '年' in cols_str:
                                            df = d
                                            break

                    if df is None:
                                    df = dfs[0]

        if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(-1)

        df.columns = df.columns.astype(str).str.replace(r'\s+', '', regex=True)

        # 尋找年份欄和數值欄
        year_col = next((c for c in df.columns if '年' in c or 'year' in c.lower()), None)
        revenue_cols = [
                        c for c in df.columns
                        if c not in ([year_col] if year_col else [])
                        and pd.to_numeric(df[c], errors='coerce').notna().sum() > 0
        ]

        if not year_col or not revenue_cols:
                        print(f"無法識別 {stock_code} 月營收資料的欄位: {df.columns.tolist()}")
                        return None

        stock_info = twstock.codes.get(stock_code)
        stock_name = stock_info.name if stock_info else stock_code

        fig = make_subplots(specs=[[{"secondary_y": False}]])
        for col in revenue_cols[:12]:  # 最多顯示 12 個月
                        y_data = pd.to_numeric(df[col], errors='coerce')
                        fig.add_trace(go.Bar(
                            name=col,
                            x=df[year_col].astype(str),
                            y=y_data,
                        ))

        fig.update_layout(
                        title=f'{stock_name} ({stock_code}) 月營收趨勢',
                        xaxis_title='年份',
                        yaxis_title='營收 (千元)',
                        barmode='group',
                        height=500,
        )
        return fig

        except Exception as e:
            print(f"繪製 {stock_code} 月營收圖時發生錯誤: {e}")
            return None
