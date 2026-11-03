# streamlit_app.py：台股分析儀進入點（側邊欄、功能分派）。各功能的畫面在 views/。

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

try:
    from market_calendar import is_trading_hours
    from views.concentration import display_concentration_results
    from views.quality import display_quality_results
    from views.ranking import display_ranking_results
    from views.revenue import display_monthly_revenue_results
    from views.screen103 import display_my_103_results
    from views.sidebar import render_filter_params
    from views.single_stock import display_single_stock_analysis

except ImportError as e:
    st.error("無法導入必要的模組。請確認所有 .py 檔案與 views/ 資料夾都在同一個專案資料夾中。")
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
    # 我的選股103、月營收選股皆已改為本機計算，不再需要 Goodinfo Cookie
    # 選用：上次成功結果備份到私人 GitHub Gist（Streamlit Cloud 重新部署後不會消失），見 README
    for _k in ('LKG_GIST_ID', 'LKG_GITHUB_TOKEN'):
        if _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    # 本機執行且無 secrets.toml 時，嘗試從環境變數取得
    if not os.getenv('FINMIND_API_TOKEN'):
        st.warning("未設定 FinMind API token（環境變數或 secrets.toml）。部分圖表可能無法生成。")


# --- 主程式進入點 ---
def main():
    st.title("📈 台股互動分析儀")

    now_tw = datetime.now(ZoneInfo('Asia/Taipei'))
    trading = is_trading_hours()
    market_status = "🟢 盤中" if trading else "🔴 盤後/休市"
    st.caption(f"台北時間: {now_tw.strftime('%Y-%m-%d %H:%M:%S')}　{market_status}")

    # ── 改善 1：側邊欄連線狀態燈號 ──────────────────────────────────────
    st.sidebar.header("🔌 連線狀態")
    _finmind    = os.getenv('FINMIND_API_TOKEN', '')

    if _finmind:
        st.sidebar.success("✅ FinMind API Token 已設定")
    else:
        # 改善 3：FinMind 未設定時在 UI 顯示明確警告，不只 print log
        st.sidebar.warning("⚠️ FinMind Token 未設定，使用匿名存取（每日有請求上限，個股圖表可能失敗）")

    # ── 改善 2：顯示交易時間狀態 ─────────────────────────────────────────
    st.sidebar.info(f"市場狀態：{market_status}")

    render_filter_params()

    # ── 選股策略按鈕 ──────────────────────────────────────────────────────
    st.sidebar.header("選股策略")
    if st.sidebar.button("1日籌碼集中度選股"):
        st.session_state.action = "concentration_pick"
    if st.sidebar.button("我的選股103（本機計算）"):
        st.session_state.action = "my_stock_picks"
    if st.sidebar.button("月營收選股（本機計算）"):
        st.session_state.action = "monthly_revenue_pick"
    if st.sidebar.button("四條件選股（營收＋均線＋大戶＋現金流）"):
        st.session_state.action = "quality_pick"

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

    # ── 內容顯示路由 ──────────────────────────────────────────────────────
    routes = {
        "concentration_pick":    display_concentration_results,
        "my_stock_picks":        display_my_103_results,
        "monthly_revenue_pick":  display_monthly_revenue_results,
        "quality_pick":          display_quality_results,
        "rank_listed":           lambda: display_ranking_results("上市"),
        "rank_otc":              lambda: display_ranking_results("上櫃"),
        "single_stock_analysis": lambda: display_single_stock_analysis(st.session_state.stock_id),
    }
    page = routes.get(st.session_state.get("action"))
    if page:
        page()

if __name__ == "__main__":
    main()
