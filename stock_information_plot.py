"""
stock_information_plot.py (已修正縮排)
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
    根據股票代碼或名稱，爬取 Goodinfo 月營收頁面並繪製趨勢圖。

    Goodinfo 月營收表格結構（pivot table）：
      - 列 = 年份（民國年，例如 113、112）
      - 欄 = 月份（1月～12月）+ 年增率、月增率等衍生欄

    解析流程：
      1. 以 BeautifulSoup 找 id="tblDetail" 的 <table>，精準定位，避免誤抓其他表格。
      2. 用 pandas 展平 MultiIndex 後取最底層欄位名稱。
      3. 辨識「年度欄」與「1月～12月」數值欄，其餘欄位捨棄。
      4. melt 成長格式（年份 × 月份 → 單月營收），再依時間排序。
      5. 繪製柱狀圖（單月營收）+ 折線圖（年增率，若存在）複合圖。
    """
    stock_code = get_stock_code(stock_identifier)
    if not stock_code:
        print(f"找不到股票：{stock_identifier}")
        return None

    url = f"https://goodinfo.tw/tw/StockMonthlyRevenue.asp?STOCK_ID={stock_code}"
    # Cookie 優先使用 GOODINFO_COOKIE_MONTHLY，回退到 GOODINFO_COOKIE_MY_STOCK
    cookie = os.getenv('GOODINFO_COOKIE_MONTHLY', '') or os.getenv('GOODINFO_COOKIE_MY_STOCK', '')
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={stock_code}",
    }
    if cookie:
        headers["Cookie"] = cookie

    try:
        import time, random
        time.sleep(random.uniform(0.5, 1.2))  # 避免被擋

        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = 'utf-8'
        html = response.text

        # 被重定向回首頁時提早返回
        if '<title>Goodinfo! 台灣股市資訊網 - 首頁</title>' in html:
            print(f"{stock_code} 月營收：Cookie 失效，被導回首頁。")
            return None

        # --- 步驟 1：精準定位表格 ---
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')

        # 嘗試以常見 id 直接定位
        table_tag = (
            soup.find('table', id='tblDetail') or
            soup.find('table', id='tblStockRevenue') or
            soup.find('table', id='tblRevenue')
        )

        if table_tag:
            dfs = pd.read_html(StringIO(str(table_tag)), flavor='lxml')
        else:
            # 退而求其次：讀出所有表格，挑最可能的那張
            dfs = pd.read_html(StringIO(html), flavor='lxml')

        if not dfs:
            print(f"找不到 {stock_code} 的月營收資料")
            return None

        # --- 步驟 2：挑出包含月份欄位的表格 ---
        MONTH_LABELS = [f'{m}月' for m in range(1, 13)]

        df_raw = None
        for d in dfs:
            cols = d.columns.get_level_values(-1) if isinstance(d.columns, pd.MultiIndex) else d.columns
            cols_flat = [str(c).replace(' ', '') for c in cols]
            if any(m in cols_flat for m in MONTH_LABELS):
                df_raw = d
                break

        # 若找不到月份欄，退回最大的表格
        if df_raw is None:
            df_raw = max(dfs, key=lambda d: d.shape[0] * d.shape[1])

        # --- 步驟 3：展平 MultiIndex，標準化欄名 ---
        if isinstance(df_raw.columns, pd.MultiIndex):
            df_raw.columns = df_raw.columns.get_level_values(-1)

        df_raw.columns = df_raw.columns.astype(str).str.replace(r'\s+', '', regex=True)

        # --- 步驟 4：辨識年度欄與月份欄 ---
        year_col = next(
            (c for c in df_raw.columns if c in ('年度', '年份', '會計年度') or
             (('年' in c) and len(c) <= 4)),
            None
        )
        # 若還找不到，取第一欄當年度欄
        if not year_col:
            year_col = df_raw.columns[0]

        month_cols = [c for c in df_raw.columns if c in MONTH_LABELS]

        # 若月份欄找不到，找純數字欄（1～12）
        if not month_cols:
            month_cols = [
                c for c in df_raw.columns
                if c != year_col and c.isdigit() and 1 <= int(c) <= 12
            ]

        # 年增率欄（可選）
        yoy_col = next(
            (c for c in df_raw.columns if '年增' in c and '%' in c and '前' not in c and '累' not in c),
            None
        )

        if not month_cols:
            print(f"無法識別 {stock_code} 月份欄位：{df_raw.columns.tolist()}")
            return None

        # --- 步驟 5：清理資料，移除非年份列 ---
        df_work = df_raw[[year_col] + month_cols + ([yoy_col] if yoy_col else [])].copy()
        df_work[year_col] = pd.to_numeric(df_work[year_col], errors='coerce')
        df_work = df_work.dropna(subset=[year_col])
        df_work[year_col] = df_work[year_col].astype(int)

        for col in month_cols + ([yoy_col] if yoy_col else []):
            df_work[col] = pd.to_numeric(df_work[col], errors='coerce')

        # --- 步驟 6：melt 成長格式 ---
        id_vars = [year_col] + ([yoy_col] if yoy_col else [])
        df_long = df_work.melt(id_vars=id_vars, value_vars=month_cols,
                               var_name='月份', value_name='單月營收')
        df_long = df_long.dropna(subset=['單月營收'])

        # 月份數字化，方便排序
        df_long['月份數'] = df_long['月份'].str.replace('月', '').astype(int)
        df_long = df_long.sort_values([year_col, '月份數'])

        # 組合時間標籤（例如 "113/1"）
        df_long['期間'] = df_long[year_col].astype(str) + '/' + df_long['月份數'].astype(str)

        # --- 步驟 7：繪圖 ---
        stock_info = twstock.codes.get(stock_code)
        stock_name = stock_info.name if stock_info else stock_code

        has_yoy = yoy_col and not df_work[yoy_col].isna().all()

        if has_yoy:
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.08,
                row_heights=[0.65, 0.35],
                subplot_titles=['單月營收 (千元)', '年增率 (%)']
            )
        else:
            fig = make_subplots(rows=1, cols=1)

        # 柱狀圖：單月營收，依年份分色
        colors = [
            '#3b82f6', '#ef4444', '#10b981', '#f59e0b',
            '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16'
        ]
        for i, year in enumerate(sorted(df_long[year_col].unique())):
            sub = df_long[df_long[year_col] == year]
            fig.add_trace(
                go.Bar(
                    name=f'{year}年',
                    x=sub['期間'],
                    y=sub['單月營收'],
                    marker_color=colors[i % len(colors)],
                    opacity=0.85,
                ),
                row=1, col=1
            )

        # 折線圖：年增率
        if has_yoy:
            # 年增率是每列一個值（對應整年），重複展開到每個月份
            yoy_map = df_work.set_index(year_col)[yoy_col].to_dict()
            df_long['年增率'] = df_long[year_col].map(yoy_map)
            fig.add_trace(
                go.Scatter(
                    name='年增率',
                    x=df_long['期間'],
                    y=df_long['年增率'],
                    mode='lines+markers',
                    line=dict(color='#f97316', width=2),
                    marker=dict(size=5),
                ),
                row=2, col=1
            )
            fig.add_hline(y=0, line_dash='dash', line_color='gray',
                          opacity=0.5, row=2, col=1)

        fig.update_layout(
            title=f'{stock_name} ({stock_code}) 月營收趨勢',
            xaxis_title='期間（民國年/月）',
            barmode='group',
            height=560 if has_yoy else 420,
            legend=dict(orientation='h', yanchor='bottom', y=1.02,
                        xanchor='right', x=1),
        )
        fig.update_yaxes(title_text='營收 (千元)', row=1, col=1)
        if has_yoy:
            fig.update_yaxes(title_text='年增率 (%)', row=2, col=1)

        print(f"成功繪製 {stock_code} 月營收圖，共 {len(df_long)} 筆資料。")
        return fig

    except Exception as e:
        print(f"繪製 {stock_code} 月營收圖時發生錯誤: {e}")
        return None
