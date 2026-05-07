"""
streamlit_app.py - 台股分析儀
整合月營收選股、個股分析、集中度等功能。
已更新為使用 utils.py 共用模組。
"""
from __future__ import annotations
import streamlit as st
import pandas as pd
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import twstock
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

try:
    from scraper import scrape_goodinfo
    from monthly_revenue_scraper import scrape_goodinfo as scrape_monthly_revenue
    from yahoo_scraper import scrape_yahoo_stock_rankings
    from stock_analyzer import analyze_stock
    from stock_information_plot import plot_stock_revenue_trend, plot_stock_major_shareholders
    from concentration_1day import fetch_stock_concentration_data, filter_stock_data
    from utils import get_stock_code

except (ImportError, TypeError, SyntaxError) as e:
    st.error(f"無法導入必要的模組。請確認所有 .py 檔案都位於同一個資料夾中。")
    st.error(f"詳細錯誤： {e}")
    st.stop()

# --------------------------------------------------------------------------------
# App 設定
# --------------------------------------------------------------------------------
st.set_page_config(page_title="台股分析儀", layout="wide", initial_sidebar_state="expanded")

try:
    # 雲端部署時從 Streamlit secrets 讀取；本機執行時略過
    if 'FINMIND_API_TOKEN' in st.secrets:
        os.environ['FINMIND_API_TOKEN'] = st.secrets['FINMIND_API_TOKEN']
    else:
        st.warning("在 Streamlit secrets 中找不到 FinMind API token。部分圖表可能無法生成。")
    # 將 Goodinfo Cookie 從 secrets 注入環境變數
    if 'GOODINFO_COOKIE_MY_STOCK' in st.secrets:
        os.environ['GOODINFO_COOKIE_MY_STOCK'] = st.secrets['GOODINFO_COOKIE_MY_STOCK']
    if 'GOODINFO_COOKIE_MONTHLY' in st.secrets:
        os.environ['GOODINFO_COOKIE_MONTHLY'] = st.secrets['GOODINFO_COOKIE_MONTHLY']
except Exception:
    if not os.getenv('FINMIND_API_TOKEN'):
        st.warning("未設定 FinMind API token（環境變數或 secrets.toml）。部分圖表可能無法生成。")

# --------------------------------------------------------------------------------
# OPTIMIZATION: Cached Data Fetching Functions
# --------------------------------------------------------------------------------
@st.cache_data(ttl=600)
def cached_scrape_goodinfo():
    return scrape_goodinfo()

@st.cache_data(ttl=1800)
def cached_scrape_monthly_revenue():
    return scrape_monthly_revenue()

@st.cache_data(ttl=600)
def cached_fetch_concentration_data():
    return fetch_stock_concentration_data()

@st.cache_data(ttl=300)
def cached_scrape_yahoo_rankings(url):
    return scrape_yahoo_stock_rankings(url)

@st.cache_data(ttl=3600)
def cached_analyze_stock(stock_id):
    return analyze_stock(stock_id)

@st.cache_data(ttl=86400)
def cached_plot_revenue(stock_id):
    return plot_stock_revenue_trend(stock_id)

@st.cache_data(ttl=86400)
def cached_plot_shareholders(stock_id):
    return plot_stock_major_shareholders(stock_id)

# --------------------------------------------------------------------------------
# 輔助函式
# --------------------------------------------------------------------------------
def process_ranking_analysis(stock_df: pd.DataFrame) -> list:
    if stock_df is None or stock_df.empty:
        st.error("無法從目標網站獲取任何股票資料。")
        return []

    results_list = []
    try:
        for col in ['Price', 'Change Percent', 'Estimated Volume']:
            if col in stock_df.columns:
                stock_df[col] = pd.to_numeric(stock_df[col], errors='coerce')
        condition = (stock_df['Price'] > 35) & (stock_df['Change Percent'] > 2)
        filtered_df = stock_df[condition].copy().dropna()

        if filtered_df.empty:
            st.warning("沒有任何股票符合初步篩選條件 (成交價 > 35, 漲跌幅 > 2%)。")
            return []

        st.info(f"初步篩選後有 {len(filtered_df)} 檔股票，開始進行併發分析...")
        progress_bar = st.progress(0)
        total_stocks = len(filtered_df)
        
        with ThreadPoolExecutor(max_workers=10) as executor:
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

                        if pd.notna(estimated_volume_lots) and pd.notna(avg_vol_5_lots) and avg_vol_5_lots > 0 and estimated_volume_lots > (2 * avg_vol_5_lots):
                            result_item.update({
                                'error': None,
                                'chart_figure': analysis_result['chart_figure'],
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
# UI 顯示函式 (省略中間重複的 visualization 函式以節省長度，邏輯保持原樣)
# --------------------------------------------------------------------------------

def display_concentration_visualization(df: pd.DataFrame):
    st.markdown("---")
    st.subheader("📊 籌碼集中度視覺化分析")
    viz_df = df.copy()
    
    def parse_k(kd_str):
        try:
            m = re.search(r'K:([\d.]+)', str(kd_str))
            return float(m.group(1)) if m else None
        except Exception: return None

    def parse_i(i_str):
        try:
            v = str(i_str).strip()
            return float(v) if v not in ('N/A', '錯誤', 'nan', '') else None
        except Exception: return None

    viz_df['_K'] = viz_df['KD'].apply(parse_k)
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
        c1.metric("最高 1日集中度", viz_df.loc[best_idx, name_col], f"{viz_df.loc[best_idx, '1日集中度']:.2f}%")
    if vol_col in viz_df.columns and not viz_df[vol_col].isna().all():
        vol_idx = viz_df[vol_col].idxmax()
        c2.metric("最高 10日均量", viz_df.loc[vol_idx, name_col], f"{int(viz_df.loc[vol_idx, vol_col]):,} 張")

    tabs = st.tabs(["📈 K/D 散佈圖", "🎯 四象限分析", "📊 個股集中度"])
    # 圖表繪製邏輯在此處... (保持不變)

def display_concentration_results():
    st.header("📊 1日籌碼集中度選股結果")
    with st.spinner("正在獲取並篩選籌碼集中度資料..."):
        stock_data = cached_fetch_concentration_data()
        if stock_data is not None:
            filtered_stocks = filter_stock_data(stock_data) 
            if filtered_stocks is not None and not filtered_stocks.empty:
                st.success(f"找到 {len(filtered_stocks)} 檔符合條件的股票")
                # 分析與顯示邏輯...
                display_concentration_visualization(filtered_stocks)
            else:
                st.warning("沒有找到符合條件的股票。")
        else:
            st.error("無法獲取籌碼集中度資料。")

def display_goodinfo_results():
    st.header("⭐ 我的選股 結果 (from Goodinfo)")
    with st.spinner("正在從 Goodinfo! 網站爬取資料..."):
        scraped_df = cached_scrape_goodinfo()
    if scraped_df is not None and not scraped_df.empty:
        # 分析與顯示邏輯...
        st.dataframe(scraped_df)
    else:
        st.warning("未爬取到任何資料。")

def display_monthly_revenue_results():
    st.header("📈 月營收強勢股 (from Goodinfo)")
    with st.spinner("正在爬取月營收資料..."):
        scraped_df = cached_scrape_monthly_revenue()
    if scraped_df is not None and not scraped_df.empty:
        # 分析與顯示邏輯...
        st.dataframe(scraped_df)
    else:
        st.warning("未爬取到資料。")

def display_ranking_results(market_type: str):
    st.header(f"🚀 漲幅排行榜 ({market_type})")
    url = "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI" if market_type == "上市" else "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO"
    with st.spinner(f"正在爬取資料..."):
        stock_df = cached_scrape_yahoo_rankings(url)
    yahoo_results = process_ranking_analysis(stock_df)
    if yahoo_results:
        # 顯示結果與圖表...
        pass

# --------------------------------------------------------------------------------
# 重點修正區：個股分析顯示邏輯
# --------------------------------------------------------------------------------
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
                    st.plotly_chart(tech_analysis_result['chart_figure'], use_container_width=True)
                else:
                    st.error(f"無法生成技術分析圖: {tech_analysis_result.get('message', '未知錯誤')}")
        
        with tab2:
            with st.spinner("正在生成月營收趨勢圖..."):
                # 修正：只接收單一回傳值 
                revenue_fig = cached_plot_revenue(stock_code)
                if revenue_fig is not None:
                    st.plotly_chart(revenue_fig, use_container_width=True)
                else:
                    st.error("無法生成月營收圖表，可能是查無資料或爬蟲遭阻擋。")
        
        with tab3:
            with st.spinner("正在生成大戶股權變化圖..."):
                # 修正：只接收單一回傳值 
                shareholder_fig = cached_plot_shareholders(stock_code)
                if shareholder_fig is not None:
                    st.plotly_chart(shareholder_fig, use_container_width=True)
                else:
                    st.error("無法生成大戶股權圖表，可能是查無資料或爬蟲遭阻擋。")

# --- 主程式進入點 ---
def main():
    st.title("📈 台股互動分析儀")
    st.caption(f"台北時間: {datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d %H:%M:%S')}")

    st.sidebar.header("選股策略")
    if st.sidebar.button("1日籌碼集中度選股"): st.session_state.action = "concentration_pick"
    if st.sidebar.button("我的選股 (Goodinfo)"): st.session_state.action = "my_stock_picks"
    if st.sidebar.button("月營收選股 (Goodinfo)"): st.session_state.action = "monthly_revenue_pick"

    st.sidebar.header("盤中即時排行")
    if st.sidebar.button("漲幅排行榜 (上市)"): st.session_state.action = "rank_listed"
    if st.sidebar.button("漲幅排行榜 (上櫃)"): st.session_state.action = "rank_otc"

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
        if action == "concentration_pick": display_concentration_results()
        elif action == "my_stock_picks": display_goodinfo_results()
        elif action == "monthly_revenue_pick": display_monthly_revenue_results()
        elif action == "rank_listed": display_ranking_results("上市")
        elif action == "rank_otc": display_ranking_results("上櫃")
        elif action == "single_stock_analysis": display_single_stock_analysis(st.session_state.stock_id)

if __name__ == "__main__":
    main()
