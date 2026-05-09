# streamlit_app.py (已整合月營收選股功能、修正時區問題，並加入表格下載CSV功能)
# [修改] 我的選股 / 月營收選股 改為 FinMind API 版本，移除 Goodinfo Cookie 依賴

import streamlit as st
import pandas as pd
import os
import re
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
import twstock
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
import plotly.graph_objects as go
import plotly.express as px
import plotly.io as pio
from plotly.subplots import make_subplots

try:
    from finmind_my_stock_scraper import scrape_goodinfo
    from finmind_monthly_revenue_scraper import scrape_goodinfo as scrape_monthly_revenue
    from yahoo_scraper import scrape_yahoo_stock_rankings
    from stock_analyzer import analyze_stock
    from stock_information_plot import plot_stock_revenue_trend, plot_stock_major_shareholders, get_stock_code
    from concentration_1day import fetch_stock_concentration_data, filter_stock_data

except ImportError as e:
    st.error(f"無法導入必要的模組。請確認所有 .py 檔案都位於同一個資料夾中。")
    st.error(f"詳細錯誤： {e}")
    st.stop()

# --------------------------------------------------------------------------------
# App 設定
# --------------------------------------------------------------------------------
st.set_page_config(page_title="台股分析儀", layout="wide", initial_sidebar_state="expanded")

try:
    # 雲端部署時從 Streamlit secrets 讀取；本機執行時略過（可用環境變數或 .streamlit/secrets.toml）
    if 'FINMIND_API_TOKEN' in st.secrets:
        os.environ['FINMIND_API_TOKEN'] = st.secrets['FINMIND_API_TOKEN']
    else:
        st.warning("在 Streamlit secrets 中找不到 FinMind API token。部分圖表可能無法生成。")
except Exception:
    # 本機執行且無 secrets.toml 時，嘗試從環境變數取得
    if not os.getenv('FINMIND_API_TOKEN'):
        st.warning("未設定 FinMind API token（環境變數或 secrets.toml）。部分圖表可能無法生成。")

# --------------------------------------------------------------------------------
# 交易時間感知 TTL
# --------------------------------------------------------------------------------
def is_trading_hours() -> bool:
    """判斷目前是否在台股交易時間內（週一至週五 09:00~13:30 台北時間）"""
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    if now.weekday() >= 5:          # 六日
        return False
    t = now.time()
    return dtime(9, 0) <= t <= dtime(13, 30)

def market_ttl(intraday_sec: int, offhours_sec: int = 3600) -> int:
    """盤中使用 intraday_sec，盤後/假日使用 offhours_sec"""
    return intraday_sec if is_trading_hours() else offhours_sec

# --------------------------------------------------------------------------------
# Figure 快取改用 JSON 序列化，大幅降低記憶體佔用
# --------------------------------------------------------------------------------
def _fig_to_cache(fig) -> str | None:
    """Plotly Figure → JSON 字串，供 st.cache_data 序列化"""
    return pio.to_json(fig) if fig is not None else None

def _fig_from_cache(json_str: str | None):
    """JSON 字串 → Plotly Figure"""
    return pio.from_json(json_str) if json_str else None

# --------------------------------------------------------------------------------
# Cached Data Fetching Functions（動態 TTL 版）
# --------------------------------------------------------------------------------
@st.cache_data(ttl=3600)
def cached_scrape_goodinfo():
    return scrape_goodinfo()

@st.cache_data(ttl=21600)
def cached_scrape_monthly_revenue():
    return scrape_monthly_revenue()

@st.cache_data(ttl=600)
def cached_fetch_concentration_data():
    return fetch_stock_concentration_data()

@st.cache_data(ttl=300)
def cached_scrape_yahoo_rankings(url):
    return scrape_yahoo_stock_rankings(url)

@st.cache_data(ttl=3600)
def cached_analyze_stock(stock_id: str) -> dict:
    """
    回傳值中的 chart_figure 已序列化為 JSON 字串，
    避免 Plotly Figure 物件佔用大量快取記憶體。
    """
    result = analyze_stock(stock_id)
    if result.get('status') == 'success' and 'chart_figure' in result:
        result['chart_json'] = _fig_to_cache(result.pop('chart_figure'))
    return result

@st.cache_data(ttl=86400)
def cached_plot_revenue(stock_id: str):
    fig, err = plot_stock_revenue_trend(stock_id)
    return _fig_to_cache(fig), err

@st.cache_data(ttl=86400)
def cached_plot_shareholders(stock_id: str):
    fig, err = plot_stock_major_shareholders(stock_id)
    return _fig_to_cache(fig), err

# --------------------------------------------------------------------------------
# 輔助函式
# --------------------------------------------------------------------------------
def show_analysis_error(stock_name: str, result: dict):
    """根據 error_type 顯示具體的錯誤提示，取代通用錯誤訊息。"""
    error_type = result.get('error_type', 'unknown')
    msg = result.get('message', '未知錯誤')
    if error_type == 'rate_limit':
        st.warning(
            f"⏳ **{stock_name}**：FinMind API 請求頻率超限（HTTP 429）。"
            f"請稍後再試，或在 [FinMind](https://finmindtrade.com/) 升級方案。"
        )
    elif error_type == 'network':
        st.warning(f"🌐 **{stock_name}**：網路連線失敗，請確認網路狀態後重試。")
    elif error_type == 'no_data':
        st.info(f"📭 **{stock_name}**：FinMind 無此股票資料（可能已下市或代碼有誤）。")
    elif error_type == 'insufficient_data':
        st.info(f"📊 **{stock_name}**：上市未滿60日，資料不足無法繪製技術分析圖。")
    else:
        st.error(f"❌ **{stock_name}** 分析失敗：{msg}")

def process_ranking_analysis(stock_df: pd.DataFrame) -> list:
    if stock_df is None or stock_df.empty:
        st.error("無法從目標網站獲取任何股票資料。")
        return []

    params       = st.session_state.get('filter_params', {})
    min_price    = params.get('min_price',  35)
    min_change   = params.get('min_change',  2.0)
    vol_ratio    = params.get('vol_ratio',   2.0)

    results_list = []
    try:
        for col in ['Price', 'Change Percent', 'Estimated Volume']:
            if col in stock_df.columns:
                stock_df[col] = pd.to_numeric(stock_df[col], errors='coerce')
        condition = (stock_df['Price'] > min_price) & (stock_df['Change Percent'] > min_change)
        filtered_df = stock_df[condition].copy().dropna(subset=['Price', 'Change Percent', 'Estimated Volume'])

        if filtered_df.empty:
            st.warning("沒有任何股票符合初步篩選條件 (成交價 > 35, 漲跌幅 > 2%)。")
            return []

        st.info(f"初步篩選後有 {len(filtered_df)} 檔股票，開始進行併發分析...")
        progress_bar = st.progress(0)
        total_stocks = len(filtered_df)
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_stock = {
                executor.submit(cached_analyze_stock, str(stock_info['Stock Symbol']).strip()): stock_info
                for stock_info in filtered_df.to_dict('records')
            }
            
            for i, future in enumerate(as_completed(future_to_stock)):
                stock_info = future_to_stock[future]
                result_item = {'stock_info': stock_info}
                try:
                    analysis_result = future.result()
                    if analysis_result['status'] == 'success':
                        indicators = analysis_result.get('indicators', {})
                        avg_vol_5_lots = indicators.get('avg_vol_5', 0) / 1000 if indicators.get('avg_vol_5') else 0
                        estimated_volume_lots = stock_info.get('Estimated Volume', 0)

                        if pd.notna(estimated_volume_lots) and pd.notna(avg_vol_5_lots) and avg_vol_5_lots > 0 and estimated_volume_lots > (vol_ratio * avg_vol_5_lots):
                            result_item.update({
                                'error': None,
                                'chart_json': analysis_result.get('chart_json'),
                                'indicators': indicators,
                                'estimated_volume_lots': estimated_volume_lots,
                                'avg_vol_5_lots': avg_vol_5_lots
                            })
                            results_list.append(result_item)
                    else:
                        result_item['error'] = analysis_result.get('message', '未知錯誤')
                        results_list.append(result_item)

                except Exception as exc:
                    result_item['error'] = f"分析時發生例外: {exc}"
                    results_list.append(result_item)
                
                progress_bar.progress((i + 1) / total_stocks)
        
        if not any(not r.get('error') for r in results_list):
            st.info("分析完成。沒有任何股票通過最終篩選條件。")

    except Exception as e:
        st.error(f"在篩選或分析過程中發生錯誤： {e}")

    return sorted(results_list, key=lambda x: x['stock_info'].get('Rank', 999))


# --------------------------------------------------------------------------------
# Streamlit UI 介面佈局
# --------------------------------------------------------------------------------

def display_concentration_visualization(df: pd.DataFrame):
    """
    籌碼集中度視覺化：
    - 統計卡片（最高1日集中度、最高10日均量、股票總數）
    - K/D 散佈圖（X=K值, Y=10日均量, 泡泡=1日集中度）
    - 四象限分析（依 I 值分 4 圖, K值 vs 1日集中度）
    - 個股集中度長條圖（依選擇顯示各周期集中度）
    """
    st.markdown("---")
    st.subheader("📊 籌碼集中度視覺化分析")

    viz_df = df.copy()

    def parse_k(kd_str):
        try:
            m = re.search(r'K:([\d.]+)', str(kd_str))
            return float(m.group(1)) if m else None
        except Exception:
            return None

    def parse_d(kd_str):
        try:
            m = re.search(r'D:([\d.]+)', str(kd_str))
            return float(m.group(1)) if m else None
        except Exception:
            return None

    def parse_i(i_str):
        try:
            v = str(i_str).strip()
            return float(v) if v not in ('N/A', '錯誤', 'nan', '') else None
        except Exception:
            return None

    viz_df['_K'] = viz_df['KD'].apply(parse_k)
    viz_df['_D'] = viz_df['KD'].apply(parse_d)
    viz_df['_I'] = viz_df['I值'].apply(parse_i)

    conc_cols = ['1日集中度', '5日集中度', '10日集中度', '20日集中度', '60日集中度', '120日集中度']
    vol_col = '10日均量'
    name_col = '股票名稱' if '股票名稱' in viz_df.columns else '名稱'

    for col in conc_cols + [vol_col]:
        if col in viz_df.columns:
            viz_df[col] = pd.to_numeric(viz_df[col], errors='coerce')

    c1, c2, c3 = st.columns(3)
    c3.metric("📈 股票總數", len(viz_df))

    if '1日集中度' in viz_df.columns and not viz_df['1日集中度'].isna().all():
        best_idx = viz_df['1日集中度'].abs().idxmax()
        c1.metric(
            "最高 1日集中度",
            viz_df.loc[best_idx, name_col],
            f"{viz_df.loc[best_idx, '1日集中度']:.2f}%"
        )

    if vol_col in viz_df.columns and not viz_df[vol_col].isna().all():
        vol_idx = viz_df[vol_col].idxmax()
        c2.metric(
            "最高 10日均量",
            viz_df.loc[vol_idx, name_col],
            f"{int(viz_df.loc[vol_idx, vol_col]):,} 張"
        )

    tab_defs = []
    if viz_df['_K'].notna().any():
        tab_defs.append(("📈 K/D 散佈圖", "kd"))
    if '1日集中度' in viz_df.columns:
        tab_defs.append(("🎯 四象限分析", "quad"))
    tab_defs.append(("📊 個股集中度", "bar"))

    if not tab_defs:
        return

    tabs = st.tabs([t[0] for t in tab_defs])

    for tab, (_, tab_type) in zip(tabs, tab_defs):
        with tab:

            if tab_type == "kd":
                sc = viz_df[viz_df['_K'].notna()].copy()

                if '1日集中度' in sc.columns and not sc['1日集中度'].isna().all():
                    max_abs = sc['1日集中度'].abs().max()
                    sc['_sz'] = (sc['1日集中度'].abs().fillna(0) / max_abs * 30 + 6).clip(6, 36)
                else:
                    sc['_sz'] = 12

                i_colors = {-3: '#10b981', 1: '#3b82f6', 2: '#eab308', 3: '#ef4444'}
                fig_kd = go.Figure()

                for i_val, color in i_colors.items():
                    sub = sc[sc['_I'] == i_val]
                    if sub.empty:
                        continue
                    y_vals = sub[vol_col] if vol_col in sub.columns else pd.Series([0] * len(sub))
                    fig_kd.add_trace(go.Scatter(
                        x=sub['_K'],
                        y=y_vals,
                        mode='markers+text',
                        name=f'I={i_val}',
                        marker=dict(
                            color=color, size=sub['_sz'].tolist(),
                            opacity=0.75, line=dict(width=1, color='white')
                        ),
                        text=sub[name_col],
                        textposition='top center',
                        textfont=dict(size=9),
                        hovertemplate=(
                            '<b>%{text}</b><br>'
                            'K值: %{x:.1f}<br>'
                            '10日均量: %{y:,.0f} 張'
                            '<extra></extra>'
                        )
                    ))

                others = sc[~sc['_I'].isin([-3, 1, 2, 3])]
                if not others.empty:
                    y_vals = others[vol_col] if vol_col in others.columns else pd.Series([0] * len(others))
                    fig_kd.add_trace(go.Scatter(
                        x=others['_K'], y=y_vals,
                        mode='markers', name='其他',
                        marker=dict(color='#9ca3af', size=10, opacity=0.5),
                        text=others[name_col],
                        hovertemplate='<b>%{text}</b><br>K值: %{x:.1f}<extra></extra>'
                    ))

                fig_kd.add_vline(x=20, line_dash="dash", line_color="green", opacity=0.6,
                                  annotation_text="超賣(20)", annotation_position="top right")
                fig_kd.add_vline(x=80, line_dash="dash", line_color="red", opacity=0.6,
                                  annotation_text="超買(80)", annotation_position="top left")

                fig_kd.update_layout(
                    title='K值 vs 10日均量（泡泡大小 = 1日集中度）',
                    xaxis_title='K值 (0–100)',
                    yaxis_title='10日均量 (張)',
                    xaxis=dict(range=[0, 100]),
                    height=520,
                    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1)
                )
                st.plotly_chart(fig_kd, use_container_width=True)

            elif tab_type == "quad":
                i_config = {
                    -3: ("空頭下跌 (I=-3)", "#10b981"),
                    1:  ("打底反轉 (I=1)",  "#3b82f6"),
                    2:  ("盤整蓄積 (I=2)",  "#eab308"),
                    3:  ("多頭上漲 (I=3)",  "#ef4444"),
                }

                sc2 = viz_df[viz_df['_K'].notna() & viz_df['1日集中度'].notna()].copy()
                max_vol_g = sc2[vol_col].max() if vol_col in sc2.columns and not sc2[vol_col].isna().all() else 1

                titles = [
                    f"{name} ({len(sc2[sc2['_I']==i_val])}檔)"
                    for i_val, (name, _) in i_config.items()
                ]
                fig_quad = make_subplots(
                    rows=2, cols=2,
                    subplot_titles=titles,
                    vertical_spacing=0.14,
                    horizontal_spacing=0.08
                )

                for (i_val, (name, color)), (row, col) in zip(
                    i_config.items(), [(1, 1), (1, 2), (2, 1), (2, 2)]
                ):
                    sub = sc2[sc2['_I'] == i_val]
                    if sub.empty:
                        continue

                    if vol_col in sub.columns and not sub[vol_col].isna().all():
                        sizes = ((sub[vol_col].fillna(0) / max_vol_g * 30) + 6).tolist()
                    else:
                        sizes = 10

                    fig_quad.add_trace(
                        go.Scatter(
                            x=sub['_K'],
                            y=sub['1日集中度'],
                            mode='markers+text',
                            name=name,
                            showlegend=False,
                            marker=dict(
                                color=color, size=sizes,
                                opacity=0.75, line=dict(width=1, color='white')
                            ),
                            text=sub[name_col],
                            textposition='top center',
                            textfont=dict(size=8),
                            hovertemplate=(
                                '<b>%{text}</b><br>'
                                'K值: %{x:.1f}<br>'
                                '1日集中度: %{y:.2f}%'
                                '<extra></extra>'
                            )
                        ),
                        row=row, col=col
                    )
                    fig_quad.add_vline(x=50, line_dash="dot", line_color="gray",
                                       opacity=0.3, row=row, col=col)
                    fig_quad.add_hline(y=0, line_dash="dot", line_color="gray",
                                       opacity=0.3, row=row, col=col)

                fig_quad.update_xaxes(range=[0, 100], title_text='K值')
                fig_quad.update_yaxes(title_text='1日集中度(%)')
                fig_quad.update_layout(
                    title='四象限分析：K值 vs 1日集中度（泡泡大小 = 10日均量）',
                    height=720,
                    showlegend=False
                )
                st.plotly_chart(fig_quad, use_container_width=True)

            elif tab_type == "bar":
                avail_conc = [c for c in conc_cols if c in viz_df.columns]
                if not avail_conc:
                    st.warning("找不到集中度欄位。")
                else:
                    labels_map = {
                        '1日集中度': '1日', '5日集中度': '5日',
                        '10日集中度': '10日', '20日集中度': '20日',
                        '60日集中度': '60日', '120日集中度': '120日'
                    }
                    stock_labels = [
                        f"{row[name_col]}({row['代碼']})"
                        for _, row in viz_df.iterrows()
                    ]
                    selected = st.selectbox("選擇股票", stock_labels, key="conc_bar_select")

                    if selected:
                        idx = stock_labels.index(selected)
                        row = viz_df.iloc[idx]
                        vals = [float(row[c]) if pd.notna(row.get(c)) else None for c in avail_conc]
                        x_labels = [labels_map.get(c, c) for c in avail_conc]
                        bar_colors = [
                            'crimson' if (v is not None and v > 0) else 'seagreen'
                            for v in vals
                        ]
                        fig_bar = go.Figure(go.Bar(
                            x=x_labels,
                            y=vals,
                            marker_color=bar_colors,
                            text=[f"{v:.2f}%" if v is not None else "N/A" for v in vals],
                            textposition='outside'
                        ))
                        fig_bar.add_hline(y=0, line_color="gray", opacity=0.5)
                        fig_bar.update_layout(
                            title=f'{row[name_col]}（{row["代碼"]}）— 各週期籌碼集中度',
                            xaxis_title='時間週期',
                            yaxis_title='集中度 (%)',
                            height=420
                        )
                        st.plotly_chart(fig_bar, use_container_width=True)


def display_concentration_results():
    st.header("📊 1日籌碼集中度選股結果")
    with st.spinner("正在獲取並篩選籌碼集中度資料..."):
        stock_data = cached_fetch_concentration_data()
        if stock_data is not None:
            _conc_params  = st.session_state.get('filter_params', {})
            _min_vol_conc = _conc_params.get('min_vol_conc', 2000)
            filtered_stocks = filter_stock_data(stock_data, min_volume=_min_vol_conc)
            
            if filtered_stocks is not None and not filtered_stocks.empty:
                st.success(f"找到 {len(filtered_stocks)} 檔符合條件的股票，正在進行技術指標分析...")
                
                k_values = []
                d_values = []
                i_values = []
                
                progress_bar = st.progress(0, text="分析進度")
                total_stocks = len(filtered_stocks)

                concentration_cache = {}
                for i, stock_row in enumerate(filtered_stocks.itertuples()):
                    stock_code = str(stock_row.代碼)
                    analysis_result = cached_analyze_stock(stock_code)
                    concentration_cache[stock_code] = analysis_result
                    
                    if analysis_result['status'] == 'success':
                        indicators = analysis_result.get('indicators', {})
                        k_val = indicators.get('k')
                        d_val = indicators.get('d')
                        i_val = indicators.get('i_value')
                        
                        k_values.append(f"{k_val:.2f}" if k_val is not None else "N/A")
                        d_values.append(f"{d_val:.2f}" if d_val is not None else "N/A")
                        i_values.append(i_val if i_val is not None else "N/A")
                    else:
                        k_values.append("錯誤")
                        d_values.append("錯誤")
                        i_values.append("錯誤")
                    
                    progress_bar.progress((i + 1) / total_stocks, text=f"正在分析: {stock_code}")
                
                progress_bar.empty()

                filtered_stocks['KD'] = [f"K:{k} D:{d}" for k, d in zip(k_values, d_values)]
                filtered_stocks['I值'] = i_values

                st.info(
                    f"**篩選條件：**\n"
                    f"1. 5日集中度 > 10日集中度\n"
                    f"2. 10日集中度 > 20日集中度\n"
                    f"3. 5日與10日集中度皆 > 0\n"
                    f"4. 10日均量 > {_min_vol_conc:,} 張（可在側邊欄調整）"
                )

                display_columns = [
                    '編號', '代碼', '股票名稱', 'KD', 'I值', '1日集中度', '5日集中度',
                    '10日集中度', '20日集中度', '60日集中度', '120日集中度', '10日均量'
                ]
                final_display_columns = [col for col in display_columns if col in filtered_stocks.columns]
                st.dataframe(filtered_stocks[final_display_columns])

                display_concentration_visualization(filtered_stocks)

                st.markdown("---")
                st.subheader("🔍 個股技術分析圖")
                for _, stock in filtered_stocks.iterrows():
                    stock_code = str(stock['代碼'])
                    stock_name = stock['股票名稱']
                    with st.expander(f"查看 {stock_name} ({stock_code}) 的技術分析圖"):
                        analysis_result = concentration_cache.get(stock_code) or cached_analyze_stock(stock_code)
                        if analysis_result['status'] == 'success':
                            st.plotly_chart(_fig_from_cache(analysis_result['chart_json']), use_container_width=True)
                        else:
                            show_analysis_error(stock_name, analysis_result)
            else:
                st.warning("沒有找到或篩選出符合條件的股票。")
        else:
            st.error("無法獲取籌碼集中度資料。")


def display_goodinfo_results():
    # [修改] header 更新為 FinMind 版本
    st.header("⭐ 我的選股結果 (FinMind 技術面篩選)")
    with st.spinner("正在從 FinMind 篩選股票，首次執行約需 3~5 分鐘..."):
        scraped_df = cached_scrape_goodinfo()

    # [修改] 區分三種狀態：API 失敗 / 無結果 / 有結果
    if scraped_df is None:
        st.error("❌ 無法取得資料，請確認 FinMind API 連線是否正常，或稍後再試。")
        return

    if scraped_df.empty:
        st.info("✅ 篩選完成，今日沒有股票符合條件。")
        return

    st.success(f"找到 {len(scraped_df)} 檔符合條件的股票，正在進行技術指標分析...")

    k_values = []
    d_values = []
    i_values = []
    analysis_cache = {}

    progress_bar = st.progress(0, text="分析進度")
    total_stocks = len(scraped_df)

    for i, stock_row in enumerate(scraped_df.itertuples()):
        stock_code = str(stock_row.代碼).strip()
        if not stock_code or stock_code == 'nan':
            k_values.append("N/A")
            d_values.append("N/A")
            i_values.append("N/A")
            continue

        analysis_result = cached_analyze_stock(stock_code)
        analysis_cache[stock_code] = analysis_result

        if analysis_result['status'] == 'success':
            indicators = analysis_result.get('indicators', {})
            k_val = indicators.get('k')
            d_val = indicators.get('d')
            i_val = indicators.get('i_value')

            k_values.append(f"{k_val:.2f}" if k_val is not None else "N/A")
            d_values.append(f"{d_val:.2f}" if d_val is not None else "N/A")
            i_values.append(i_val if i_val is not None else "N/A")
        else:
            k_values.append("錯誤")
            d_values.append("錯誤")
            i_values.append("錯誤")

        progress_bar.progress((i + 1) / total_stocks, text=f"正在分析: {stock_code}")

    progress_bar.empty()

    scraped_df['KD'] = [f"K:{k} D:{d}" for k, d in zip(k_values, d_values)]
    scraped_df['I值'] = i_values

    # [修改] 篩選條件說明更新
    st.info("""
    **篩選條件（FinMind + 本地計算，對應原 Goodinfo 選股103）：**
    1. 今日紅K棒幅 **2.5%~10%**（收盤 > 開盤）
    2. 成交張數 **5,000~900,000 張**
    3. 股價與 60日均線（季線）乖離 **-5%~+5%**（近季線）
    4. 日 **K 值 0~50** 且今日 K > 昨日 K（低檔向上）
    5. 月線 (20MA) **>** 季線 (60MA)（多頭排列）
    """)

    # [修改] display_columns 對應新 scraper 欄位
    display_columns = [
        '代碼', '名稱', 'KD', 'I值', '市場', '股價日期',
        '成交', '漲跌幅', '成交張數', '季線乖離%', '日K值', '日D值'
    ]
    final_display_columns = [col for col in display_columns if col in scraped_df.columns]
    st.dataframe(scraped_df[final_display_columns])

    for _, stock in scraped_df.iterrows():
        stock_code = str(stock['代碼']).strip()
        stock_name = str(stock['名稱']).strip()
        if not stock_code or stock_code == 'nan':
            continue

        with st.expander(f"查看 {stock_name} ({stock_code}) 的技術分析圖"):
            analysis_result = analysis_cache.get(stock_code) or cached_analyze_stock(stock_code)
            if analysis_result['status'] == 'success':
                st.plotly_chart(_fig_from_cache(analysis_result['chart_json']), use_container_width=True)
            else:
                show_analysis_error(stock_name, analysis_result)


def display_monthly_revenue_visualization(df: pd.DataFrame):
    """
    月營收視覺化：
    - 統計指標卡片
    - 年增率 / 月增率 Top 10 柱狀圖
    - K值 vs 年增率 四象限散佈圖 (依 I 值分類)
    """
    st.markdown("---")
    st.subheader("📊 月營收視覺化分析")

    viz_df = df.copy()

    def parse_k(kd_str):
        try:
            m = re.search(r'K:([\d.]+)', str(kd_str))
            return float(m.group(1)) if m else None
        except Exception:
            return None

    def parse_i(i_str):
        try:
            v = str(i_str).strip()
            return float(v) if v not in ('N/A', '錯誤', 'nan', '') else None
        except Exception:
            return None

    viz_df['_K值'] = viz_df['KD'].apply(parse_k) if 'KD' in viz_df.columns else None
    viz_df['_I值'] = viz_df['I值'].apply(parse_i) if 'I值' in viz_df.columns else None

    # 偵測年增率欄位（FinMind 版欄位名稱：年增率%(當月)）
    yoy_col = None
    mom_col = None
    vol_col = None

    for col in viz_df.columns:
        c = col.replace(' ', '').replace('\xa0', '')
        if yoy_col is None and ('年增率%(當月)' in c or ('年增' in c and '%' in c and '累計' not in c and '前' not in c)):
            yoy_col = col
        elif mom_col is None and ('月增率%' in c or ('月增' in c and '%' in c and '累計' not in c and '前' not in c)):
            mom_col = col
        elif vol_col is None and any(k in c for k in ['成交張數', '單日張數', '成交量', '月營收']):
            vol_col = col

    # 備用：寬鬆比對
    for col in viz_df.columns:
        c = col.replace(' ', '').replace('\xa0', '')
        if yoy_col is None and any(k in c for k in ['YoY', 'yoy']):
            yoy_col = col
        if mom_col is None and any(k in c for k in ['MoM', 'mom']):
            mom_col = col

    for col in [yoy_col, mom_col, vol_col]:
        if col:
            viz_df[col] = pd.to_numeric(viz_df[col], errors='coerce')

    if vol_col and not viz_df[vol_col].isna().all():
        viz_filtered = viz_df[viz_df[vol_col] > 5000].copy()
    else:
        viz_filtered = viz_df.copy()

    n = len(viz_filtered)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📈 分析股票數", n)

    if yoy_col and n > 0 and not viz_filtered[yoy_col].isna().all():
        avg_yoy = viz_filtered[yoy_col].mean()
        c2.metric("平均年增率", f"{avg_yoy:.1f}%")
        best_idx = viz_filtered[yoy_col].idxmax()
        c4.metric("最高年增率", viz_filtered.loc[best_idx, '名稱'])

    if mom_col and n > 0 and not viz_filtered[mom_col].isna().all():
        avg_mom = viz_filtered[mom_col].mean()
        c3.metric("平均月增率", f"{avg_mom:.1f}%")

    if not yoy_col and not mom_col:
        skip_cols = {'代碼', '名稱', 'KD', 'I值', '_K值', '_I值'}
        available = [c for c in viz_df.columns if c not in skip_cols]
        st.warning(
            f"⚠️ 未偵測到年增率／月增率欄位，無法繪製圖表。\n\n"
            f"可用欄位：`{'`、`'.join(available[:20])}`"
        )
        return

    tab_defs = []
    if yoy_col and n > 0 and not viz_filtered[yoy_col].isna().all():
        tab_defs.append(("📊 年增率 Top10", "yoy"))
    if mom_col and n > 0 and not viz_filtered[mom_col].isna().all():
        tab_defs.append(("📊 月增率 Top10", "mom"))
    if viz_filtered['_K值'].notna().any() and yoy_col:
        tab_defs.append(("🎯 四象限分析", "quad"))

    if not tab_defs:
        return

    tabs = st.tabs([t[0] for t in tab_defs])

    for tab, (_, tab_type) in zip(tabs, tab_defs):
        with tab:

            if tab_type == "yoy":
                top10 = viz_filtered.nlargest(10, yoy_col)[['名稱', '代碼', yoy_col]].copy()
                top10['股票'] = top10['名稱'] + '\n(' + top10['代碼'].astype(str) + ')'
                fig = px.bar(
                    top10, x='股票', y=yoy_col,
                    title='月營收年增率 Top 10',
                    labels={yoy_col: '年增率(%)'},
                    color=yoy_col,
                    color_continuous_scale='RdYlGn',
                    text=top10[yoy_col].round(1).astype(str) + '%'
                )
                fig.update_traces(textposition='outside')
                fig.update_layout(xaxis_tickangle=-30, showlegend=False)
                st.plotly_chart(fig, use_container_width=True)

            elif tab_type == "mom":
                top10m = viz_filtered.nlargest(10, mom_col)[['名稱', '代碼', mom_col]].copy()
                top10m['股票'] = top10m['名稱'] + '\n(' + top10m['代碼'].astype(str) + ')'
                fig_m = px.bar(
                    top10m, x='股票', y=mom_col,
                    title='月營收月增率 Top 10',
                    labels={mom_col: '月增率(%)'},
                    color=mom_col,
                    color_continuous_scale='RdYlGn',
                    text=top10m[mom_col].round(1).astype(str) + '%'
                )
                fig_m.update_traces(textposition='outside')
                fig_m.update_layout(xaxis_tickangle=-30, showlegend=False)
                st.plotly_chart(fig_m, use_container_width=True)

            elif tab_type == "quad":
                i_config = {
                    -3: ("優質股 (I=-3)", "#10b981"),
                    1:  ("反轉股 (I=1)",  "#3b82f6"),
                    2:  ("成長股 (I=2)",  "#eab308"),
                    3:  ("高風險 (I=3)",  "#ef4444"),
                }

                scatter_df = viz_filtered[
                    viz_filtered['_K值'].notna() & viz_filtered[yoy_col].notna()
                ].copy()

                fig_q = go.Figure()

                for i_val, (name, color) in i_config.items():
                    sub = scatter_df[scatter_df['_I值'] == i_val]
                    if sub.empty:
                        continue

                    if vol_col and vol_col in sub.columns and not sub[vol_col].isna().all():
                        max_vol = scatter_df[vol_col].max()
                        sizes = ((sub[vol_col].fillna(0) / max_vol * 35) + 8).tolist()
                    else:
                        sizes = 14

                    fig_q.add_trace(go.Scatter(
                        x=sub['_K值'],
                        y=sub[yoy_col],
                        mode='markers+text',
                        name=name,
                        marker=dict(
                            color=color, size=sizes, opacity=0.75,
                            line=dict(width=1, color='white')
                        ),
                        text=sub['名稱'],
                        textposition='top center',
                        textfont=dict(size=9),
                        hovertemplate=(
                            '<b>%{text}</b><br>'
                            'K值: %{x:.1f}<br>'
                            '年增率: %{y:.1f}%'
                            '<extra></extra>'
                        )
                    ))

                others = scatter_df[~scatter_df['_I值'].isin([-3, 1, 2, 3])]
                if not others.empty:
                    fig_q.add_trace(go.Scatter(
                        x=others['_K值'],
                        y=others[yoy_col],
                        mode='markers',
                        name='其他',
                        marker=dict(color='#9ca3af', size=12, opacity=0.5),
                        text=others['名稱'],
                        hovertemplate=(
                            '<b>%{text}</b><br>'
                            'K值: %{x:.1f}<br>'
                            '年增率: %{y:.1f}%'
                            '<extra></extra>'
                        )
                    ))

                fig_q.add_hline(y=0,  line_dash="dash", line_color="gray", opacity=0.4)
                fig_q.add_vline(x=50, line_dash="dash", line_color="gray", opacity=0.4)

                if not scatter_df.empty:
                    y_max = scatter_df[yoy_col].max()
                    y_label = y_max * 0.88 if y_max > 0 else 10
                    for x_pos, label in [(22, "低K高增率<br>(潛力強勢)"), (78, "高K高增率<br>(強勢持續)")]:
                        fig_q.add_annotation(
                            x=x_pos, y=y_label, text=label,
                            showarrow=False,
                            font=dict(color="gray", size=10),
                            opacity=0.6
                        )

                fig_q.update_layout(
                    title='K值 vs 月營收年增率 四象限分析（泡泡大小 = 成交量）',
                    xaxis_title='K值 (0–100)',
                    yaxis_title='年增率 (%)',
                    xaxis=dict(range=[0, 100]),
                    height=620,
                    legend=dict(
                        orientation='h',
                        yanchor='bottom', y=1.02,
                        xanchor='right',  x=1
                    )
                )
                st.plotly_chart(fig_q, use_container_width=True)


def display_monthly_revenue_results():
    # [修改] header 更新為 FinMind 版本
    st.header("📈 月營收強勢股 (FinMind 月營收篩選)")
    with st.spinner("正在從 FinMind 下載並篩選月營收資料，首次執行約需 15~30 秒..."):
        scraped_df = cached_scrape_monthly_revenue()

    # [修改] 區分三種狀態：API 失敗 / 無結果 / 有結果
    if scraped_df is None:
        st.error("❌ 無法取得月營收資料，請確認 FinMind API 連線是否正常，或稍後再試。")
        return

    if scraped_df.empty:
        st.info("✅ 篩選完成，目前沒有股票符合月營收條件。")
        return

    st.success(f"找到 {len(scraped_df)} 筆資料，正在進行技術指標分析...")

    k_values = []
    d_values = []
    i_values = []

    progress_bar = st.progress(0, text="分析進度")
    total_stocks = len(scraped_df)

    revenue_cache = {}
    for i, stock_row in enumerate(scraped_df.itertuples()):
        stock_code = str(stock_row.代碼).strip()
        if not stock_code or stock_code == 'nan':
            k_values.append("N/A")
            d_values.append("N/A")
            i_values.append("N/A")
            continue

        analysis_result = cached_analyze_stock(stock_code)
        revenue_cache[stock_code] = analysis_result

        if analysis_result['status'] == 'success':
            indicators = analysis_result.get('indicators', {})
            k_val = indicators.get('k')
            d_val = indicators.get('d')
            i_val = indicators.get('i_value')

            k_values.append(f"{k_val:.2f}" if k_val is not None else "N/A")
            d_values.append(f"{d_val:.2f}" if d_val is not None else "N/A")
            i_values.append(i_val if i_val is not None else "N/A")
        else:
            k_values.append("錯誤")
            d_values.append("錯誤")
            i_values.append("錯誤")

        progress_bar.progress((i + 1) / total_stocks, text=f"正在分析: {stock_code}")

    progress_bar.empty()

    scraped_df['KD'] = [f"K:{k} D:{d}" for k, d in zip(k_values, d_values)]
    scraped_df['I值'] = i_values

    # [修改] 篩選條件說明更新
    st.info("""
    **篩選條件（FinMind TaiwanStockMonthRevenue，對應原 Goodinfo 月營收選股03）：**
    1. 單月營收年增率 - 當月   **> 15%**
    2. 單月營收年增率 - 前1月  **> 10%**
    3. 單月營收年增率 - 前2月  **> 10%**
    4. 單月營收年增率 - 前3月  **> 10%**
    5. 單月營收年增率 - 前4月  **> 10%**
    6. 另標示「創同期前3高 ✅」欄位（歷年同月份排名 ≤ 3）
    """)

    all_cols = scraped_df.columns.tolist()
    try:
        name_idx = all_cols.index('名稱')
        new_cols = all_cols[:name_idx+1] + ['KD', 'I值'] + [c for c in all_cols[name_idx+1:] if c not in ['KD', 'I值']]
        scraped_df = scraped_df[new_cols]
    except ValueError:
        scraped_df = scraped_df[['代碼', '名稱', 'KD', 'I值'] + [c for c in all_cols if c not in ['代碼', '名稱', 'KD', 'I值']]]

    st.dataframe(scraped_df)

    display_monthly_revenue_visualization(scraped_df)

    st.markdown("---")
    st.subheader("🔍 個股技術分析圖")
    for _, stock in scraped_df.iterrows():
        stock_code = str(stock['代碼']).strip()
        stock_name = str(stock['名稱']).strip()
        if not stock_code or stock_code == 'nan':
            continue

        with st.expander(f"查看 {stock_name} ({stock_code}) 的技術分析圖"):
            analysis_result = revenue_cache.get(stock_code) or cached_analyze_stock(stock_code)
            if analysis_result['status'] == 'success':
                st.plotly_chart(_fig_from_cache(analysis_result['chart_json']), use_container_width=True)
            else:
                show_analysis_error(stock_name, analysis_result)


def display_ranking_visualization(summary_df: pd.DataFrame):
    """
    漲幅排行視覺化：
    - 統計卡片（最佳漲幅、最高量比、超賣數、強訊號數）
    - 量比 Top15 水平柱狀圖
    - K值 vs 漲跌幅% 散佈圖（依 I 訊號分色，泡泡=量比）
    - I 訊號四象限分析（2×2 子圖）
    """
    st.markdown("---")
    st.subheader("📊 漲幅排行視覺化分析")

    viz_df = summary_df.copy()

    for col in ['K', 'D', '因子', '漲跌幅(%)', '成交價', '預估量(張)', '5日均量(張)']:
        if col in viz_df.columns:
            viz_df[col] = pd.to_numeric(viz_df[col], errors='coerce')

    def parse_i(v):
        try:
            return float(str(v).strip()) if str(v).strip() not in ('N/A', '錯誤', 'nan', '') else None
        except Exception:
            return None

    viz_df['_I'] = viz_df['I訊號'].apply(parse_i)

    if '預估量(張)' in viz_df.columns and '5日均量(張)' in viz_df.columns:
        viz_df['_量比'] = (
            viz_df['預估量(張)'] / viz_df['5日均量(張)'].replace(0, np.nan)
        ).round(2)
    else:
        viz_df['_量比'] = viz_df['因子']

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

    tabs = st.tabs(["📊 量比 Top15", "📈 K值 vs 漲跌幅%", "🎯 四象限分析"])

    with tabs[0]:
        if '_量比' not in viz_df.columns or viz_df['_量比'].isna().all():
            st.warning("找不到量比欄位。")
        else:
            top15 = viz_df.nlargest(15, '_量比')[['名稱', '代碼', '_量比', '漲跌幅(%)']].copy()
            top15['股票'] = top15['名稱'] + '(' + top15['代碼'].astype(str) + ')'
            top15 = top15.sort_values('_量比')

            fig_bar = go.Figure(go.Bar(
                x=top15['_量比'],
                y=top15['股票'],
                orientation='h',
                marker_color='#3b82f6',
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
            st.plotly_chart(fig_bar, use_container_width=True)

    with tabs[1]:
        sc = viz_df[viz_df['K'].notna() & viz_df['漲跌幅(%)'].notna()].copy()
        if sc.empty:
            st.warning("無有效 K 值資料。")
        else:
            if '_量比' in sc.columns and not sc['_量比'].isna().all():
                max_f = sc['_量比'].max()
                sc['_sz'] = ((sc['_量比'].fillna(0) / max_f * 32) + 6).clip(6, 38)
            else:
                sc['_sz'] = 14

            i_color_map = {
                -3: ('#10b981', '空頭強力 (I=-3)'),
                -2: ('#6ee7b7', '空頭弱 (I=-2)'),
                -1: ('#6ee7b7', '空頭弱 (I=-1)'),
                 0: ('#fbbf24', '中性 (I=0)'),
                 1: ('#fca5a5', '多頭弱 (I=1)'),
                 2: ('#fca5a5', '多頭弱 (I=2)'),
                 3: ('#ef4444', '多頭強力 (I=3)'),
            }

            fig_sc = go.Figure()
            plotted = set()
            for i_val, (color, label) in i_color_map.items():
                sub = sc[sc['_I'] == i_val]
                if sub.empty:
                    continue
                show = label not in plotted
                plotted.add(label)
                fig_sc.add_trace(go.Scatter(
                    x=sub['K'],
                    y=sub['漲跌幅(%)'],
                    mode='markers+text',
                    name=label,
                    showlegend=show,
                    marker=dict(color=color, size=sub['_sz'].tolist(),
                                opacity=0.8, line=dict(width=1, color='white')),
                    text=sub['名稱'],
                    textposition='top center',
                    textfont=dict(size=9),
                    hovertemplate=(
                        '<b>%{text}</b><br>'
                        'K值: %{x:.1f}<br>'
                        '漲跌幅: %{y:.2f}%'
                        '<extra></extra>'
                    )
                ))

            others = sc[~sc['_I'].isin(i_color_map.keys())]
            if not others.empty:
                fig_sc.add_trace(go.Scatter(
                    x=others['K'], y=others['漲跌幅(%)'],
                    mode='markers', name='其他',
                    marker=dict(color='#9ca3af', size=12, opacity=0.5),
                    text=others['名稱'],
                    hovertemplate='<b>%{text}</b><br>K: %{x:.1f}<br>漲跌幅: %{y:.2f}%<extra></extra>'
                ))

            fig_sc.add_vline(x=20, line_dash="dash", line_color="#10b981", opacity=0.6,
                             annotation_text="超賣(20)", annotation_position="top right")
            fig_sc.add_vline(x=80, line_dash="dash", line_color="#ef4444", opacity=0.6,
                             annotation_text="超買(80)", annotation_position="top left")

            fig_sc.update_layout(
                title='K值 vs 漲跌幅%（泡泡大小 = 量比）',
                xaxis_title='K值 (0–100)',
                yaxis_title='漲跌幅 (%)',
                xaxis=dict(range=[0, 100]),
                height=530,
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1)
            )
            st.plotly_chart(fig_sc, use_container_width=True)

    with tabs[2]:
        i_quad_config = {
            -3: ("空頭強力 (I=-3)", "#10b981"),
             1: ("多頭弱 (I=1)",   "#fca5a5"),
             2: ("多頭中 (I=2)",   "#f97316"),
             3: ("多頭強力 (I=3)", "#ef4444"),
        }
        sc2 = viz_df[viz_df['K'].notna() & viz_df['漲跌幅(%)'].notna()].copy()

        if sc2.empty:
            st.warning("無有效資料可繪製四象限圖。")
        else:
            max_f2 = sc2['_量比'].max() if '_量比' in sc2.columns and not sc2['_量比'].isna().all() else 1

            titles = [
                f"{name} ({len(sc2[sc2['_I']==i_val])}檔)"
                for i_val, (name, _) in i_quad_config.items()
            ]
            fig_q = make_subplots(
                rows=2, cols=2,
                subplot_titles=titles,
                vertical_spacing=0.14,
                horizontal_spacing=0.08
            )

            for (i_val, (name, color)), (row, col) in zip(
                i_quad_config.items(), [(1, 1), (1, 2), (2, 1), (2, 2)]
            ):
                sub = sc2[sc2['_I'] == i_val]
                if sub.empty:
                    continue

                if '_量比' in sub.columns and not sub['_量比'].isna().all():
                    sizes = ((sub['_量比'].fillna(0) / max_f2 * 30) + 6).tolist()
                else:
                    sizes = 10

                fig_q.add_trace(
                    go.Scatter(
                        x=sub['K'],
                        y=sub['漲跌幅(%)'],
                        mode='markers+text',
                        name=name,
                        showlegend=False,
                        marker=dict(color=color, size=sizes,
                                    opacity=0.8, line=dict(width=1, color='white')),
                        text=sub['名稱'],
                        textposition='top center',
                        textfont=dict(size=8),
                        hovertemplate=(
                            '<b>%{text}</b><br>'
                            'K值: %{x:.1f}<br>'
                            '漲跌幅: %{y:.2f}%'
                            '<extra></extra>'
                        )
                    ),
                    row=row, col=col
                )
                fig_q.add_vline(x=50, line_dash="dot", line_color="gray",
                                opacity=0.3, row=row, col=col)
                fig_q.add_hline(y=0, line_dash="dot", line_color="gray",
                                opacity=0.3, row=row, col=col)

            fig_q.update_xaxes(range=[0, 100], title_text='K值')
            fig_q.update_yaxes(title_text='漲跌幅(%)')
            fig_q.update_layout(
                title='四象限分析：K值 vs 漲跌幅%（泡泡大小 = 量比）',
                height=720,
                showlegend=False
            )
            st.plotly_chart(fig_q, use_container_width=True)


def display_ranking_results(market_type: str):
    st.header(f"🚀 漲幅排行榜 ({market_type})")
    st.info("篩選條件：\n1. 成交價 > 35元\n2. 漲跌幅 > 2%\n3. 預估成交量 > 2 倍前5日均量")
    
    url = "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI" if market_type == "上市" else "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO"
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
                
                k_val = f"{indicators.get('k'):.2f}" if indicators.get('k') is not None else "N/A"
                d_val = f"{indicators.get('d'):.2f}" if indicators.get('d') is not None else "N/A"
                
                i_val = indicators.get('i_value')
                i_text = str(i_val) if i_val is not None else "N/A"

                display_data.append({
                    "排名": stock_info.get('Rank', ''),
                    "代碼": stock_info.get('Stock Symbol', ''),
                    "名稱": stock_info.get('Stock Name', ''),
                    "成交價": stock_info.get('Price', ''),
                    "漲跌幅(%)": stock_info.get('Change Percent', ''),
                    "預估量(張)": int(result.get('estimated_volume_lots', 0)),
                    "5日均量(張)": int(result.get('avg_vol_5_lots', 0)),
                    "因子": round(stock_info.get('Factor', 1.0), 2),
                    "K": k_val,
                    "D": d_val,
                    "I訊號": i_text
                })
        
        if not display_data:
             st.warning("所有符合條件的股票在後續分析中被過濾，無最終結果可顯示。")
        else:
            summary_df = pd.DataFrame(display_data)

            def highlight_signal(val):
                if val == "N/A":
                    return ''
                try:
                    v = float(val)
                    if v > 0:
                        return 'color: red; font-weight: bold;'
                    elif v < 0:
                        return 'color: green; font-weight: bold;'
                    return ''
                except ValueError:
                    return ''

            styled_df = summary_df.style.map(highlight_signal, subset=['I訊號'])

            st.dataframe(
                styled_df,
                use_container_width=True,
                column_config={
                    "排名": st.column_config.NumberColumn(format="%d"),
                    "代碼": st.column_config.TextColumn(),
                    "成交價": st.column_config.NumberColumn(format="%.2f"),
                    "漲跌幅(%)": st.column_config.NumberColumn(format="%.2f"),
                    "預估量(張)": st.column_config.NumberColumn(format="%d"),
                    "5日均量(張)": st.column_config.NumberColumn(format="%d"),
                }
            )

            display_ranking_visualization(summary_df)

        st.markdown("---")
        st.subheader("🔍 個股技術分析圖")
        for result in yahoo_results:
            if not result.get('error'):
                stock_name = result['stock_info']['Stock Name']
                stock_symbol = result['stock_info']['Stock Symbol']
                with st.expander(f"查看 {stock_name} ({stock_symbol}) 的技術分析圖"):
                    st.plotly_chart(_fig_from_cache(result['chart_json']), use_container_width=True)
            else:
                stock_name = result['stock_info'].get('Stock Name', '未知股票')
                show_analysis_error(stock_name, {'error_type': result.get('error_type', 'unknown'), 'message': result.get('error', '')})


def display_single_stock_analysis(stock_identifier: str):
    st.header(f"🔍 個股分析: {stock_identifier}")
    with st.spinner(f"正在查找股票 '{stock_identifier}'..."):
        stock_code = get_stock_code(stock_identifier)
    
    if not stock_code:
        st.error(f"找不到股票 '{stock_identifier}'。")
    else:
        stock_info = twstock.codes.get(stock_code)
        stock_name = stock_info.name if stock_info else stock_code
        st.subheader(f"{stock_name} ({stock_code})")
        
        tab1, tab2, tab3 = st.tabs(["技術分析", "月營收趨勢", "大戶股權變化"])
        with tab1:
            with st.spinner("正在生成技術分析圖..."):
                tech_analysis_result = cached_analyze_stock(stock_code)
                if tech_analysis_result['status'] == 'success':
                    st.plotly_chart(_fig_from_cache(tech_analysis_result['chart_json']), use_container_width=True)
                else:
                    show_analysis_error(stock_name, tech_analysis_result)
        with tab2:
            with st.spinner("正在生成月營收趨勢圖..."):
                revenue_json, revenue_error = cached_plot_revenue(stock_code)
                if not revenue_error:
                    st.plotly_chart(_fig_from_cache(revenue_json), use_container_width=True)
                else:
                    st.error(f"無法生成營收圖: {revenue_error}")
        with tab3:
            with st.spinner("正在生成大戶股權變化圖..."):
                shareholder_json, shareholder_error = cached_plot_shareholders(stock_code)
                if not shareholder_error:
                    st.plotly_chart(_fig_from_cache(shareholder_json), use_container_width=True)
                else:
                    st.error(f"無法生成大戶股權圖: {shareholder_error}")


# --- 主程式進入點 ---
def main():
    st.title("📈 台股互動分析儀")

    now_tw = datetime.now(ZoneInfo('Asia/Taipei'))
    trading = is_trading_hours()
    market_status = "🟢 盤中" if trading else "🔴 盤後/休市"
    st.caption(f"台北時間: {now_tw.strftime('%Y-%m-%d %H:%M:%S')}　{market_status}")

    # [修改] 側邊欄連線狀態：移除 Goodinfo Cookie 燈號，只保留 FinMind
    st.sidebar.header("🔌 連線狀態")
    _finmind = os.getenv('FINMIND_API_TOKEN', '')

    if _finmind:
        st.sidebar.success("✅ FinMind API Token 已設定（選股功能完整）")
    else:
        st.sidebar.warning("⚠️ FinMind Token 未設定，使用匿名存取（每日有請求上限，個股圖表可能失敗）")

    st.sidebar.info(f"市場狀態：{market_status}")

    st.sidebar.header("⚙️ 篩選參數")
    with st.sidebar.expander("排行榜篩選條件", expanded=False):
        filter_min_price    = st.slider("最低股價（元）",      10, 200,  35, key="fp_price")
        filter_min_change   = st.slider("最低漲幅（%）",       0.5, 10.0, 2.0, step=0.5, key="fp_change")
        filter_vol_ratio    = st.slider("預估量 / 5日均量 倍數", 1.0, 5.0, 2.0, step=0.5, key="fp_volr")

    with st.sidebar.expander("籌碼集中度篩選條件", expanded=False):
        filter_min_vol_conc = st.number_input("最低10日均量（張）", value=2000, step=500, key="fp_conc_vol")

    st.session_state['filter_params'] = {
        'min_price':    filter_min_price,
        'min_change':   filter_min_change,
        'vol_ratio':    filter_vol_ratio,
        'min_vol_conc': filter_min_vol_conc,
    }

    # [修改] 側邊欄按鈕文字更新
    st.sidebar.header("選股策略")
    if st.sidebar.button("1日籌碼集中度選股"):
        st.session_state.action = "concentration_pick"
    if st.sidebar.button("我的選股 (FinMind)"):
        st.session_state.action = "my_stock_picks"
    if st.sidebar.button("月營收選股 (FinMind)"):
        st.session_state.action = "monthly_revenue_pick"

    st.sidebar.header("盤中即時排行")
    if st.sidebar.button("漲幅排行榜 (上市)"):
        st.session_state.action = "rank_listed"
    if st.sidebar.button("漲幅排行榜 (上櫃)"):
        st.session_state.action = "rank_otc"

    st.sidebar.header("個股查詢")
    stock_identifier_input = st.sidebar.text_input("輸入股票代碼或名稱", placeholder="例如: 2330 或 台積電")
    if st.sidebar.button("生成個股分析圖"):
        if stock_identifier_input:
            st.session_state.action = "single_stock_analysis"
            st.session_state.stock_id = stock_identifier_input
        else:
            st.sidebar.warning("請輸入股票代碼或名稱")

    if 'action' in st.session_state:
        action = st.session_state.action
        if action == "concentration_pick":
            display_concentration_results()
        elif action == "my_stock_picks":
            display_goodinfo_results()
        elif action == "monthly_revenue_pick":
            display_monthly_revenue_results()
        elif action == "rank_listed":
            display_ranking_results("上市")
        elif action == "rank_otc":
            display_ranking_results("上櫃")
        elif action == "single_stock_analysis":
            display_single_stock_analysis(st.session_state.stock_id)

if __name__ == "__main__":
    main()
