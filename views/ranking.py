"""漲幅排行榜（上市／上櫃）。"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import services
from viz import REF_LINE, SERIES, add_i_scatter, i_small_multiples, legend_top
from views.common import (cached_analyze_stock, cached_scrape_yahoo_rankings, render_chart_picker,
                          show_analysis_error)


def process_ranking_analysis(stock_df: pd.DataFrame) -> list:
    """漲幅排行榜篩選（規則在 services.screen_ranking）。回傳通過者與分析失敗者，依排名排序。"""
    if stock_df is None or stock_df.empty:      # 失敗原因已由 cached_scrape_yahoo_rankings 顯示
        return []
    params = st.session_state.get('filter_params', {})
    min_price = params.get('min_price', 35)
    min_change = params.get('min_change', 2.0)
    vol_ratio = params.get('vol_ratio', 2.0)

    progress_bar = st.progress(0, text="分析進度")
    res = services.screen_ranking(
        stock_df, cached_analyze_stock, min_price, min_change, vol_ratio,
        progress_cb=lambda i, n, c: progress_bar.progress(i / n, text=f"正在分析: {c} ({i}/{n})"))
    progress_bar.empty()

    if res.excluded:
        st.caption(f"已排除 ETF／ETN 等非普通股 {len(res.excluded)} 檔：{'、'.join(res.excluded)}")
    if res.n_prelim == 0:
        st.warning(f"沒有任何股票符合初步篩選條件（成交價 > {min_price}、漲跌幅 > {min_change}%）。")
        return []
    st.info(f"初步篩選後有 {res.n_prelim} 檔股票，已完成量能分析。")
    if res.no_data:
        st.caption(f"已略過 FinMind 查無資料的 {len(res.no_data)} 檔：{'、'.join(res.no_data)}")
    if not res.passed:
        st.info("分析完成。沒有任何股票通過最終篩選條件。")
    passed = [{**r, 'error': None} for r in res.passed]
    return passed + res.errors


def display_ranking_visualization(summary_df: pd.DataFrame):
    """
    整合遠端 stock-trend-analyzer 的漲幅排行視覺化到本地：
    - 統計卡片（最佳漲幅、最高量比、超賣數、強訊號數）
    - 量比 Top15 水平柱狀圖
    - K值 vs 漲跌幅% 散佈圖（依 I 訊號分色，泡泡=量比）
    - I 訊號四象限分析（2×2 子圖）
    """
    st.markdown("---")
    st.subheader("📊 漲幅排行視覺化分析")

    viz_df = summary_df.copy()

    # --- 數值轉型 ---
    for col in ['K', 'D', '因子', '漲跌幅(%)', '成交價', '預估量(張)', '5日均量(張)']:
        if col in viz_df.columns:
            viz_df[col] = pd.to_numeric(viz_df[col], errors='coerce')

    viz_df['_I'] = pd.to_numeric(viz_df['I訊號'], errors='coerce')

    # 量比：直接用 預估量÷5日均量 計算，避免「因子」欄位預設值 1.0 導致泡泡等大
    if '預估量(張)' in viz_df.columns and '5日均量(張)' in viz_df.columns:
        viz_df['_量比'] = (
            viz_df['預估量(張)'] / viz_df['5日均量(張)'].replace(0, np.nan)
        ).round(2)
    else:
        viz_df['_量比'] = viz_df['因子']

    # --- 統計卡片 ---
    c1, c2, c3, c4 = st.columns(4)

    if '漲跌幅(%)' in viz_df.columns and not viz_df['漲跌幅(%)'].isna().all():
        top_idx = viz_df['漲跌幅(%)'].idxmax()
        c1.metric("🏆 最佳漲幅",
                  viz_df.loc[top_idx, '名稱'],
                  f"+{viz_df.loc[top_idx, '漲跌幅(%)']:.2f}%")

    if '_量比' in viz_df.columns and not viz_df['_量比'].isna().all():
        vol_idx = viz_df['_量比'].idxmax()
        c2.metric("📦 最高量比",
                  viz_df.loc[vol_idx, '名稱'],
                  f"{viz_df.loc[vol_idx, '_量比']:.1f}x")

    if 'K' in viz_df.columns:
        oversold = int((viz_df['K'] < 20).sum())
        c3.metric("🟢 超賣股數 (K<20)", oversold)

    strong = int((viz_df['_I'] == 3).sum())
    c4.metric("🔴 強訊號 (I=3)", strong)

    # --- Tabs ---
    tabs = st.tabs(["📊 量比 Top15", "📈 K值 vs 漲跌幅%", "🎯 依 I 值分組"])

    # ── 量比 Top15 水平柱狀圖 ─────────────────────────────
    with tabs[0]:
        if '_量比' not in viz_df.columns or viz_df['_量比'].isna().all():
            st.warning("找不到量比欄位。")
        else:
            top15 = viz_df.nlargest(15, '_量比')[['名稱', '代碼', '_量比', '漲跌幅(%)']].copy()
            top15['股票'] = top15['名稱'] + '(' + top15['代碼'].astype(str) + ')'
            top15 = top15.sort_values('_量比')   # 水平圖由小到大排列更直覺

            fig_bar = go.Figure(go.Bar(
                x=top15['_量比'],
                y=top15['股票'],
                orientation='h',
                marker_color=SERIES[0],
                text=top15['_量比'].round(1).astype(str) + 'x',
                textposition='outside'
            ))
            x_max = top15['_量比'].max() * 1.15 if (not top15.empty and not top15['_量比'].isna().all()) else 10
            fig_bar.update_layout(
                title='量比 Top 15（預估量 / 5日均量）',
                xaxis_title='量比',
                yaxis_title='',
                xaxis=dict(range=[1, x_max]),
                height=max(400, len(top15) * 28 + 80),
                margin=dict(l=140)
            )
            st.plotly_chart(fig_bar, width="stretch")

    # ── K值 vs 漲跌幅% 散佈圖：顏色 = I 值，泡泡 = 量比 ──
    with tabs[1]:
        sc = viz_df[viz_df['K'].notna() & viz_df['漲跌幅(%)'].notna()].copy()
        if sc.empty:
            st.warning("無有效 K 值資料。")
        else:
            fig_sc = go.Figure()
            add_i_scatter(fig_sc, sc, 'K', '漲跌幅(%)', '名稱', x_label='K值', y_label='漲跌幅',
                          y_suffix='%', size_col='_量比', size_label='量比(x)')
            fig_sc.add_vline(x=20, annotation_text="超賣 20", annotation_position="bottom right", **REF_LINE)
            fig_sc.add_vline(x=80, annotation_text="超買 80", annotation_position="bottom left", **REF_LINE)
            fig_sc.update_layout(title='K值 vs 漲跌幅%（顏色 = I 值；泡泡大小 = 量比）',
                                 xaxis_title='K值 (0–100)', yaxis_title='漲跌幅 (%)',
                                 xaxis=dict(range=[0, 100]), height=530)
            st.plotly_chart(legend_top(fig_sc), width="stretch")

    # ── 依 I 值拆開的小倍數圖 ─────────────────────
    with tabs[2]:
        sc2 = viz_df[viz_df['K'].notna() & viz_df['漲跌幅(%)'].notna()].copy()
        fig_q = i_small_multiples(sc2, 'K', '漲跌幅(%)', '名稱', x_label='K值', y_label='漲跌幅',
                                  y_suffix='%', size_col='_量比', size_label='量比(x)',
                                  title='依 I 值分組：K值 vs 漲跌幅%（泡泡大小 = 量比）')
        if fig_q is None:
            st.warning("無有效資料可繪製。")
        else:
            st.plotly_chart(fig_q, width="stretch")


def display_ranking_results(market_type: str):
    st.header(f"🚀 漲幅排行榜 ({market_type})")
    fp = st.session_state.get('filter_params', {})
    st.info(f"篩選條件：\n1. 成交價 > {fp.get('min_price', 35)}元\n2. 漲跌幅 > {fp.get('min_change', 2.0)}%\n"
            f"3. 預估成交量 > {fp.get('vol_ratio', 2.0)} 倍前5日均量（側邊欄可調整）")

    url = services.YAHOO_RANK_URL[market_type]
    with st.spinner(f"正在爬取 Yahoo Finance ({market_type}) 的資料..."):
        stock_df = cached_scrape_yahoo_rankings(url)
    
    yahoo_results = process_ranking_analysis(stock_df)

    if yahoo_results:
        st.subheader("篩選結果摘要")
        display_data = []
        
        for result in yahoo_results:
            if not result.get('error'):
                stock_info = result['stock_info']
                indicators = result.get('indicators', {})
                
                display_data.append({
                    "排名": stock_info.get('Rank', ''),
                    "代碼": stock_info.get('Stock Symbol', ''),
                    "名稱": stock_info.get('Stock Name', ''),
                    "成交價": stock_info.get('Price', ''),
                    "漲跌幅(%)": stock_info.get('Change Percent', ''),
                    "預估量(張)": int(result.get('estimated_volume_lots', 0)),
                    "5日均量(張)": int(result.get('avg_vol_5_lots', 0)),
                    "因子": round(stock_info.get('Factor', 1.0), 2),
                    # 存數字（算不出來 = None），顯示格式交給 column_config
                    "K": indicators.get('k'),
                    "D": indicators.get('d'),
                    "I訊號": indicators.get('i_value'),
                })
        
        if not display_data:
             st.warning("所有符合條件的股票在後續分析中被過濾，無最終結果可顯示。")
        else:
            summary_df = pd.DataFrame(display_data)
            for col in ("K", "D", "I訊號"):
                summary_df[col] = pd.to_numeric(summary_df[col], errors="coerce")

            # 定義樣式函式：僅用於顯示顏色（台股慣例：正紅負綠）
            def highlight_signal(val):
                if pd.isna(val):
                    return ''
                if val > 0:
                    return 'color: red; font-weight: bold;'
                if val < 0:
                    return 'color: green; font-weight: bold;'
                return ''

            # 套用樣式
            styled_df = summary_df.style.map(highlight_signal, subset=['I訊號'])

            # 使用 st.dataframe 顯示，這樣滑鼠移上去時右上角會出現 CSV 下載按鈕
            # 並且使用 column_config 來格式化數字 (例如不顯示逗號或指定精度)
            st.dataframe(
                styled_df,
                width="stretch",
                column_config={
                    "排名": st.column_config.NumberColumn(format="%d"),
                    "代碼": st.column_config.TextColumn(), # 防止代碼被當成數字加逗號
                    "成交價": st.column_config.NumberColumn(format="%.2f"),
                    "漲跌幅(%)": st.column_config.NumberColumn(format="%.2f"),
                    "預估量(張)": st.column_config.NumberColumn(format="%d"),
                    "5日均量(張)": st.column_config.NumberColumn(format="%d"),
                    "K": st.column_config.NumberColumn(format="%.2f"),
                    "D": st.column_config.NumberColumn(format="%.2f"),
                    "I訊號": st.column_config.NumberColumn(format="%d"),
                }
            )

            # ── 整合遠端視覺化服務：直接在本地產生統計卡片與圖表 ──
            display_ranking_visualization(summary_df)

        render_chart_picker([(str(r['stock_info']['Stock Symbol']), str(r['stock_info']['Stock Name']))
                             for r in yahoo_results if not r.get('error')], key=f"rank_{market_type}")
        for result in yahoo_results:
            if result.get('error'):
                stock_name = result['stock_info'].get('Stock Name', '未知股票')
                show_analysis_error(stock_name, {'error_type': result.get('error_type', 'unknown'),
                                                 'message': result.get('error', '')})
