# streamlit_app.py (完整修正版，已為 Goodinfo 選股加入篩選條件說明)

import streamlit as st

# --- 偵錯碼開始 ---
# st.header("偵錯資訊：檢查 Secrets")
# if 'goodinfo' in st.secrets and 'cookie' in st.secrets['goodinfo']:
#     st.success("✅ 成功讀取到 Goodinfo Cookie！")
#     # 為了安全，只顯示 Cookie 的一小部分
#     st.write("Cookie 前15個字元:", st.secrets['goodinfo']['cookie'][:15], "...")
# else:
#     st.error("❌ 讀取 Goodinfo Cookie 失敗！")
#     st.write("目前的 secrets 內容：")
#     st.json(st.secrets.to_dict()) # 顯示所有讀取到的 secrets
# --- 偵錯碼結束 ---

import pandas as pd
import os
from datetime import datetime, timedelta
import twstock
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed # OPTIMIZATION: For concurrent analysis

# --- OPTIMIZATION: Updated imports for consolidated scraper ---
try:
    from scraper import scrape_goodinfo
    # 使用新的通用 yahoo_scraper 模組
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

if 'FINMIND_API_TOKEN' in st.secrets:
    os.environ['FINMIND_API_TOKEN'] = st.secrets['FINMIND_API_TOKEN']
else:
    st.warning("在 Streamlit secrets 中找不到 FinMind API token。部分圖表可能無法生成。")

# --------------------------------------------------------------------------------
# OPTIMIZATION: Cached Data Fetching Functions
# --------------------------------------------------------------------------------
@st.cache_data(ttl=600) # 快取10分鐘
def cached_scrape_goodinfo():
    return scrape_goodinfo()

@st.cache_data(ttl=600)
def cached_fetch_concentration_data():
    return fetch_stock_concentration_data()

@st.cache_data(ttl=300) # 盤中資料快取5分鐘
def cached_scrape_yahoo_rankings(url):
    return scrape_yahoo_stock_rankings(url)

@st.cache_data(ttl=3600) # 個股分析資料快取1小時
def cached_analyze_stock(stock_id):
    return analyze_stock(stock_id)

@st.cache_data(ttl=86400) # 每日更新一次即可
def cached_plot_revenue(stock_id):
    return plot_stock_revenue_trend(stock_id)

@st.cache_data(ttl=86400) # 每週更新一次即可
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
        # 初步篩選
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
        
        # --- OPTIMIZATION: Concurrent analysis using ThreadPoolExecutor ---
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
                         # 即使分析失敗也加入列表，以便後續顯示錯誤訊息
                        result_item['error'] = analysis_result.get('message', '未知錯誤')
                        results_list.append(result_item)

                except Exception as exc:
                    result_item['error'] = f"分析時發生例外: {exc}"
                    results_list.append(result_item)
                
                progress_bar.progress((i + 1) / total_stocks)
        
        if not any(not r.get('error') for r in results_list):
            st.success("分析完成。沒有任何股票通過最終篩選條件。")

    except Exception as e:
        st.error(f"在篩選或分析過程中發生錯誤： {e}")

    return sorted(results_list, key=lambda x: x['stock_info'].get('Rank', 999))


# --------------------------------------------------------------------------------
# Streamlit UI 介面佈局 (將每個 action 拆分成獨立函式)
# --------------------------------------------------------------------------------

# ==============================================================================
# 【主要修改處】: 修改 display_concentration_results 函式
# ==============================================================================
def display_concentration_results():
    st.header("📊 1日籌碼集中度選股結果")
    with st.spinner("正在獲取並篩選籌碼集中度資料..."):
        stock_data = cached_fetch_concentration_data()
        if stock_data is not None:
            filtered_stocks = filter_stock_data(stock_data) # 預設10日均量 > 2000
            
            if filtered_stocks is not None and not filtered_stocks.empty:
                # --- 新增開始: 進行技術指標分析 ---
                st.success(f"找到 {len(filtered_stocks)} 檔符合條件的股票，正在進行技術指標分析...")
                
                k_values = []
                d_values = []
                i_values = []
                
                progress_bar = st.progress(0, text="分析進度")
                total_stocks = len(filtered_stocks)

                # 遍歷篩選出的股票，逐一獲取技術指標
                for i, stock_row in enumerate(filtered_stocks.itertuples()):
                    stock_code = str(stock_row.代碼)
                    analysis_result = cached_analyze_stock(stock_code)
                    
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

                # 將計算出的指標新增為新的欄位
                filtered_stocks['KD'] = [f"K:{k} D:{d}" for k, d in zip(k_values, d_values)]
                filtered_stocks['I值'] = i_values
                # --- 新增結束 ---

                st.info("""
                **篩選條件：**
                1.  5日集中度 > 10日集中度
                2.  10日集中度 > 20日集中度
                3.  5日與10日集中度皆 > 0
                4.  10日均量 > 2000 張
                """)

                # --- 修改開始: 調整顯示欄位的順序 ---
                display_columns = [
                    '編號', '代碼', '股票名稱', 'KD', 'I值', '1日集中度', '5日集中度',
                    '10日集中度', '20日集中度', '60日集中度', '120日集中度', '10日均量'
                ]
                # 確保所有要顯示的欄位都存在於 DataFrame 中
                final_display_columns = [col for col in display_columns if col in filtered_stocks.columns]
                st.dataframe(filtered_stocks[final_display_columns])
                # --- 修改結束 ---
                
                for _, stock in filtered_stocks.iterrows():
                    stock_code = str(stock['代碼'])
                    stock_name = stock['股票名稱']
                    with st.expander(f"查看 {stock_name} ({stock_code}) 的技術分析圖"):
                        analysis_result = cached_analyze_stock(stock_code)
                        if analysis_result['status'] == 'success':
                            st.plotly_chart(analysis_result['chart_figure'], use_container_width=True)
                        else:
                            st.error(f"為 {stock_name} 生成圖表時出錯: {analysis_result.get('message', '未知錯誤')}")
            else:
                st.warning("沒有找到或篩選出符合條件的股票。")
        else:
            st.error("無法獲取籌碼集中度資料。")
# ==============================================================================
# 【修改結束】
# ==============================================================================

def display_goodinfo_results():
    st.header("⭐ 我的選股 結果 (from Goodinfo)")
    with st.spinner("正在從 Goodinfo! 網站爬取資料..."):
        scraped_df = cached_scrape_goodinfo()
    
    if scraped_df is not None and not scraped_df.empty:
        st.success(f"成功爬取到 {len(scraped_df)} 筆資料。")

        # --- 【這就是新增的區塊】 ---
        st.info("""
        **篩選條件 (來自 Goodinfo 自訂篩選):**
        1.  紅K棒棒幅 > 2.5%
        2.  成交張數 > 5000張
        3.  與季線乖離 : -5% ~ 5%
        4.  週K值範圍 : 0 ~ 50
        5.  週K值向上
        6.  季線在月線之上 (空頭排列)
        7.  日K值 > 日D值
        8.  今日成交張數 > 1.3 X 昨日成交張數
        """)
        # --- 【新增區塊結束】 ---

        st.dataframe(scraped_df)
        
        for _, stock in scraped_df.iterrows():
            stock_code = str(stock['代碼']).strip()
            stock_name = str(stock['名稱']).strip()
            if not stock_code or stock_code == 'nan': continue
            
            with st.expander(f"查看 {stock_name} ({stock_code}) 的技術分析圖"):
                analysis_result = cached_analyze_stock(stock_code)
                if analysis_result['status'] == 'success':
                    st.plotly_chart(analysis_result['chart_figure'], use_container_width=True)
                else:
                    st.error(f"為 {stock_name} 生成圖表時出錯: {analysis_result.get('message', '未知錯誤')}")
    else:
        st.warning("未爬取到任何資料。請檢查 Cookie 是否有效。")


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
                if i_val is not None:
                    i_color = 'red' if i_val > 0 else 'green'
                    i_text = f'<span style="color:{i_color};">{i_val}</span>'

                display_data.append({
                    "排名": stock_info.get('Rank', ''),
                    "代碼": stock_info.get('Stock Symbol', ''),
                    "名稱": stock_info.get('Stock Name', ''),
                    "成交價": stock_info.get('Price', ''),
                    "漲跌幅(%)": stock_info.get('Change Percent', ''),
                    "預估量(張)": int(result.get('estimated_volume_lots', 0)),
                    "5日均量(張)": int(result.get('avg_vol_5_lots', 0)),
                    "因子": f"{stock_info.get('Factor', 1.0):.2f}",
                    "K": k_val,
                    "D": d_val,
                    "I訊號": i_text
                })
        
        if not display_data:
             st.warning("所有符合條件的股票在後續分析中被過濾，無最終結果可顯示。")
        else:
            summary_df = pd.DataFrame(display_data)
            st.markdown(summary_df.to_html(escape=False, index=False), unsafe_allow_html=True)

        st.subheader("個股分析圖表")
        for result in yahoo_results:
            if not result.get('error'):
                stock_name = result['stock_info']['Stock Name']
                stock_symbol = result['stock_info']['Stock Symbol']
                with st.expander(f"查看 {stock_name} ({stock_symbol}) 的技術分析圖"):
                    st.plotly_chart(result['chart_figure'], use_container_width=True)
            else:
                stock_name = result['stock_info'].get('Stock Name', '未知股票')
                st.error(f"分析 {stock_name} 時發生錯誤: {result.get('error')}")


def display_single_stock_analysis(stock_identifier: str):
    st.header(f"🔍 個股分析: {stock_identifier}")
    with st.spinner(f"正在查找股票 '{stock_identifier}'..."):
        stock_code = get_stock_code(stock_identifier)
    
    if not stock_code:
        st.error(f"找不到股票 '{stock_identifier}'。")
    else:
        stock_name = twstock.codes[stock_code].name
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
                revenue_fig, revenue_error = cached_plot_revenue(stock_code)
                if not revenue_error:
                    st.plotly_chart(revenue_fig, use_container_width=True)
                else:
                    st.error(f"無法生成營收圖: {revenue_error}")
        with tab3:
            with st.spinner("正在生成大戶股權變化圖..."):
                shareholder_fig, shareholder_error = cached_plot_shareholders(stock_code)
                if not shareholder_error:
                    st.plotly_chart(shareholder_fig, use_container_width=True)
                else:
                    st.error(f"無法生成大戶股權圖: {shareholder_error}")

# --- 主程式進入點 ---
def main():
    st.title("📈 台股互動分析儀")
    st.caption(f"台北時間: {(datetime.now() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')}")

    # --- 側邊欄 ---
    st.sidebar.header("選股策略")
    if st.sidebar.button("1日籌碼集中度選股"):
        st.session_state.action = "concentration_pick"
    if st.sidebar.button("我的選股 (Goodinfo)"):
        st.session_state.action = "my_stock_picks"

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

    # --- 內容顯示路由 ---
    if 'action' in st.session_state:
        action = st.session_state.action
        if action == "concentration_pick":
            display_concentration_results()
        elif action == "my_stock_picks":
            display_goodinfo_results()
        elif action == "rank_listed":
            display_ranking_results("上市")
        elif action == "rank_otc":
            display_ranking_results("上櫃")
        elif action == "single_stock_analysis":
            display_single_stock_analysis(st.session_state.stock_id)

if __name__ == "__main__":
    main()
