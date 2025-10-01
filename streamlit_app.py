import streamlit as st
import pandas as pd
import os
from datetime import datetime
import twstock

# --- 導入所有必要的模組 ---
try:
    from scraper import scrape_goodinfo
    from yahoo_stock import scrape_yahoo_stock_rankings as scrape_yahoo_listed
    from yahoo_stock_otc import scrape_yahoo_stock_rankings as scrape_yahoo_otc
    from stock_analyzer import analyze_stock
    # 【修改】導入新的繪圖函式
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

# 【移除】不再需要 'static' 資料夾
# if not os.path.exists('static'):
#     os.makedirs('static')

# --------------------------------------------------------------------------------
# 輔助函式
# --------------------------------------------------------------------------------
def process_ranking_analysis(stock_df):
    if stock_df is None or stock_df.empty:
        st.error("無法從目標網站獲取任何股票資料。")
        return []

    results_list = []
    try:
        # 資料清理與初步篩選
        for col in ['Price', 'Change Percent', 'Estimated Volume']:
            if col in stock_df.columns:
                stock_df[col] = pd.to_numeric(stock_df[col], errors='coerce')
        condition = (stock_df['Price'] > 35) & (stock_df['Change Percent'] > 2)
        filtered_df = stock_df[condition].copy()

        if filtered_df.empty:
            st.warning("沒有任何股票符合初步篩選條件 (成交價 > 35, 漲跌幅 > 2%)。")
            return []

        st.info(f"初步篩選後有 {len(filtered_df)} 檔股票，開始逐一分析...")
        progress_bar = st.progress(0)
        total_stocks = len(filtered_df)

        for i, stock_info in enumerate(filtered_df.to_dict('records')):
            stock_id = str(stock_info.get('Stock Symbol')).strip()
            if not stock_id or stock_id == '0':
                continue

            st.write(f"正在分析： {stock_id} {stock_info.get('Stock Name')}...")
            analysis_result = analyze_stock(stock_id)
            result_item = {'stock_info': stock_info}

            if analysis_result.get('status') == 'success':
                indicators = analysis_result.get('indicators', {})
                avg_vol_5_lots = indicators.get('avg_vol_5', 0) / 1000 if indicators.get('avg_vol_5') else 0
                estimated_volume_lots = stock_info.get('Estimated Volume', 0)

                if pd.notna(estimated_volume_lots) and pd.notna(avg_vol_5_lots) and avg_vol_5_lots > 0 and estimated_volume_lots > (2 * avg_vol_5_lots):
                    st.write(f"  -> ✅ **符合條件**: {stock_id}")
                    result_item['error'] = None
                    # 【修改】儲存圖表物件而非路徑
                    result_item['chart_figure'] = analysis_result['chart_figure']
                    result_item['indicators'] = indicators
                    result_item['estimated_volume_lots'] = estimated_volume_lots
                    result_item['avg_vol_5_lots'] = avg_vol_5_lots
                    results_list.append(result_item)
                else:
                    st.write(f"  -> ❌ **不符條件**: {stock_id} - 預估量未達標")
            else:
                st.write(f"  -> ⚠️ **分析失敗**: {stock_id}: {analysis_result.get('message', '未知錯誤')}")

            progress_bar.progress((i + 1) / total_stocks)
        
        if not results_list:
            st.success("分析完成。沒有任何股票通過最終篩選條件。")

    except Exception as e:
        st.error(f"在篩選或分析過程中發生錯誤： {e}")

    return results_list

# --------------------------------------------------------------------------------
# Streamlit UI 介面佈局
# --------------------------------------------------------------------------------

st.title("📈 台股互動分析儀")
st.caption(f"系統時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

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

# --- 主要內容區域 ---
if 'action' in st.session_state:
    action = st.session_state.action

    if action == "concentration_pick":
        st.header("📊 1日籌碼集中度選股結果")
        with st.spinner("正在獲取並篩選籌碼集中度資料..."):
            stock_data = fetch_stock_concentration_data()
            if stock_data is not None:
                filtered_stocks = filter_stock_data(stock_data)
                if filtered_stocks is not None and not filtered_stocks.empty:
                    st.success(f"找到 {len(filtered_stocks)} 檔符合條件的股票。")
                    st.dataframe(filtered_stocks)
                    
                    for _, stock in filtered_stocks.iterrows():
                        stock_code = str(stock['代碼'])
                        stock_name = stock['股票名稱']
                        st.subheader(f"技術分析圖: {stock_name} ({stock_code})")
                        with st.spinner(f"正在為 {stock_name} 生成圖表..."):
                            analysis_result = analyze_stock(stock_code)
                            if analysis_result['status'] == 'success':
                                # 【修改】直接顯示 Plotly 圖表
                                st.plotly_chart(analysis_result['chart_figure'], use_container_width=True)
                            else:
                                st.error(f"為 {stock_name} 生成圖表時出錯: {analysis_result.get('message', '未知錯誤')}")
                else:
                    st.warning("沒有找到或篩選出符合條件的股票。")
            else:
                st.error("無法獲取籌碼集中度資料。")

    elif action == "my_stock_picks":
        st.header("⭐ 我的選股 結果 (from Goodinfo)")
        with st.spinner("正在從 Goodinfo! 網站爬取資料..."):
            scraped_df = scrape_goodinfo()
            if scraped_df is not None and not scraped_df.empty:
                st.success(f"成功爬取到 {len(scraped_df)} 筆資料。")
                st.dataframe(scraped_df)
            else:
                st.warning("未爬取到任何資料。")

    elif action in ["rank_listed", "rank_otc"]:
        market_type = "上市" if action == "rank_listed" else "上櫃"
        st.header(f"🚀 漲幅排行榜 ({market_type})")
        with st.spinner(f"正在爬取 Yahoo Finance ({market_type}) 的資料..."):
            url = "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI" if action == "rank_listed" else "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO"
            stock_df = scrape_yahoo_listed(url) if action == "rank_listed" else scrape_yahoo_otc(url)
        
        yahoo_results = process_ranking_analysis(stock_df)
        if yahoo_results:
            st.subheader("篩選結果摘要")
            summary_df = pd.DataFrame([{
                "排名": res['stock_info'].get('Rank'), "代號": res['stock_info'].get('Stock Symbol'),
                "名稱": res['stock_info'].get('Stock Name'), "成交價": res['stock_info'].get('Price'),
                "漲跌幅(%)": res['stock_info'].get('Change Percent'), "預估成交量(張)": res.get('estimated_volume_lots'),
                "5日均量(張)": res.get('avg_vol_5_lots'), "KD值": f"{res['indicators'].get('k'):.2f} / {res['indicators'].get('d'):.2f}",
                "I值": f"{res['indicators'].get('i_value'):.0f}"
            } for res in yahoo_results])
            st.dataframe(summary_df.style.format({'成交價': '{:.2f}', '漲跌幅(%)': '{:.2f}', '預估成交量(張)': '{:.0f}', '5日均量(張)': '{:.0f}'}))

            st.subheader("個股分析圖表")
            for result in yahoo_results:
                st.subheader(f"{result['stock_info']['Stock Name']} ({result['stock_info']['Stock Symbol']})")
                # 【修改】直接顯示 Plotly 圖表
                st.plotly_chart(result['chart_figure'], use_container_width=True)

    elif action == "single_stock_analysis":
        stock_identifier = st.session_state.stock_id
        st.header(f"🔍 個股分析: {stock_identifier}")
        with st.spinner(f"正在查找股票 '{stock_identifier}'..."):
            stock_code = get_stock_code(stock_identifier)
        
        if not stock_code:
            st.error(f"找不到股票 '{stock_identifier}'。")
        else:
            stock_name = twstock.codes[stock_code].name
            st.subheader(f"{stock_name} ({stock_code})")
            
            # 1. 技術分析圖
            st.subheader("技術分析圖")
            with st.spinner("正在生成技術分析圖..."):
                tech_analysis_result = analyze_stock(stock_code)
                if tech_analysis_result['status'] == 'success':
                    # 【修改】直接顯示 Plotly 圖表
                    st.plotly_chart(tech_analysis_result['chart_figure'], use_container_width=True)
                else:
                    st.error(f"無法生成技術分析圖: {tech_analysis_result.get('message', '未知錯誤')}")
            
            # 2. 月營收趨勢圖
            st.subheader("月營收趨勢圖")
            with st.spinner("正在生成月營收趨勢圖..."):
                # 【修改】接收圖表物件和錯誤
                revenue_fig, revenue_error = plot_stock_revenue_trend(stock_code)
                if not revenue_error:
                    st.plotly_chart(revenue_fig, use_container_width=True)
                else:
                    st.error(f"無法生成營收圖: {revenue_error}")
            
            # 3. 大戶股權變化圖
            st.subheader("大戶股權變化圖")
            with st.spinner("正在生成大戶股權變化圖..."):
                # 【修改】接收圖表物件和錯誤
                shareholder_fig, shareholder_error = plot_stock_major_shareholders(stock_code)
                if not shareholder_error:
                    st.plotly_chart(shareholder_fig, use_container_width=True)
                else:
                    st.error(f"無法生成大戶股權圖: {shareholder_error}")
                    
    if 'action' in st.session_state:
        del st.session_state.action