# streamlit_app.py (已整合月營收選股功能、修正時區問題，並加入表格下載CSV功能)

import streamlit as st
import pandas as pd
import os
import re
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo
import twstock
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import plotly.io as pio
from plotly.subplots import make_subplots

try:
    from local_screener import Screen103Params, ScreenerError
    from revenue_screener import RevenueParams
    from yahoo_scraper import scrape_yahoo_stock_rankings
    from stock_analyzer import analyze_stock, build_chart, fetch_price_history, AnalysisError
    from market_calendar import is_trading_hours
    import services
    from stock_information_plot import plot_stock_revenue_trend, plot_stock_major_shareholders, get_stock_code
    from concentration_1day import fetch_stock_concentration_data
    from scrape_utils import ScrapeError, lkg_key
    from viz import (UP_COLOR, DOWN_COLOR, SERIES, REF_LINE, MARGIN_SCALE, REVENUE_SCALE,
                     add_i_scatter, i_small_multiples, legend_top, heat_table, i_value)

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
    # 我的選股103、月營收選股皆已改為本機計算，不再需要 Goodinfo Cookie
except Exception:
    # 本機執行且無 secrets.toml 時，嘗試從環境變數取得
    if not os.getenv('FINMIND_API_TOKEN'):
        st.warning("未設定 FinMind API token（環境變數或 secrets.toml）。部分圖表可能無法生成。")

# --------------------------------------------------------------------------------
# 改善 2：交易時間感知 TTL
# --------------------------------------------------------------------------------
def market_ttl(intraday_sec: int, offhours_sec: int = 3600) -> int:
    """盤中使用 intraday_sec，盤後/假日使用 offhours_sec"""
    return intraday_sec if is_trading_hours() else offhours_sec

# --------------------------------------------------------------------------------
# 改善 4：Figure 快取改用 JSON 序列化，大幅降低記憶體佔用
# --------------------------------------------------------------------------------
def _fig_to_cache(fig) -> str | None:
    """Plotly Figure → JSON 字串，供 st.cache_data 序列化"""
    return pio.to_json(fig) if fig is not None else None

def _fig_from_cache(json_str: str | None):
    """JSON 字串 → Plotly Figure"""
    return pio.from_json(json_str) if json_str else None


# -------------------------------------------------------------------------------
# 輔助解析函式（模組層級，避免在多個視覺化函式內重複定義）
# -------------------------------------------------------------------------------
def _parse_k(kd_str) -> float | None:
    """從 'K:XX.XX D:XX.XX' 字串擷取 K 值"""
    m = re.search(r'K:([\d.]+)', str(kd_str))
    return float(m.group(1)) if m else None

def _parse_d(kd_str) -> float | None:
    """從 'K:XX.XX D:XX.XX' 字串擷取 D 值"""
    m = re.search(r'D:([\d.]+)', str(kd_str))
    return float(m.group(1)) if m else None

def _parse_i(i_str) -> float | None:
    """解析 I 值字串為浮點數，無效值回傳 None"""
    v = str(i_str).strip()
    try:
        return float(v) if v not in ('N/A', '錯誤', 'nan', '') else None
    except Exception:
        return None

def _batch_kd_analyze(
    stock_codes: list[str],
    progress_bar=None,
    label_prefix: str = '正在分析',
    analyze_fn=None,
) -> dict[str, dict]:
    """
    並發分析多檔股票（只算指標、不畫圖）。回傳 {code: analysis_result}。
    progress_bar: st.progress 物件（可選）；analyze_fn 預設為 cached_analyze_stock。
    """
    def _cb(i, total, code):
        if progress_bar is not None:
            progress_bar.progress(i / total, text=f"{label_prefix}: {code} ({i}/{total})")
    return services.batch_analyze(stock_codes, analyze_fn or cached_analyze_stock, _cb)

# --------------------------------------------------------------------------------
# OPTIMIZATION: Cached Data Fetching Functions（動態 TTL 版）
# --------------------------------------------------------------------------------
#
# 失敗結果不快取：快取函式內遇到失敗一律 raise（st.cache_data 不會快取例外），
# 外層包裝再轉回原本的 None / 錯誤 dict，讓既有的顯示邏輯不用改。
# --------------------------------------------------------------------------------
@st.cache_data(ttl=market_ttl(1800, 3600), show_spinner=False)   # 盤中30分鐘；盤後1小時
def _cached_screen_103(params: dict) -> dict:
    """我的選股103（本機計算）。ScreenerError 直接往外拋，不會被快取。"""
    return services.run_screen_103(params)

@st.cache_data(ttl=market_ttl(1800, 21600), show_spinner=False)  # 盤中30分鐘；盤後6小時
def _cached_screen_revenue(params: dict) -> dict:
    """月營收選股（本機計算）。ScreenerError 直接往外拋，不會被快取。"""
    return services.run_screen_revenue(params)

@st.cache_data(ttl=market_ttl(300, 3600))   # 盤中5分鐘；盤後1小時
def _cached_fetch_concentration_data():
    return fetch_stock_concentration_data()          # ScrapeError 往外拋，不進快取

@st.cache_data(ttl=market_ttl(60, 300))     # 盤中1分鐘；盤後5分鐘
def _cached_scrape_yahoo_rankings(url):
    return scrape_yahoo_stock_rankings(url)          # ScrapeError 往外拋，不進快取

@st.cache_data(ttl=3600, show_spinner=False)
def _cached_price_history(stock_id: str) -> pd.DataFrame:
    """
    改善 4（新版）：只快取日線（約 200 列、十幾 KB），指標與圖表都從它現算，
    不再把每檔約 130KB 的圖表 JSON 塞進快取。失敗時 raise AnalysisError，不進快取。
    """
    return fetch_price_history(stock_id)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_chart_json(stock_id: str, price_data: pd.DataFrame | None = None) -> str:
    """只有使用者選擇看圖時才建圖；同一檔一小時內重看不重算。"""
    if price_data is None:
        price_data = _cached_price_history(stock_id)
    return _fig_to_cache(build_chart(stock_id, price_data))


# --------------------------------------------------------------------------------
# 上次成功結果（last-known-good）：抓取成功就存一份；失敗時改顯示上次結果並標註時間
# --------------------------------------------------------------------------------
_with_lkg = services.with_lkg   # 回傳 (data, stale)；stale 為 None 表示是最新資料


def _show_stale(label: str, stale) -> None:
    saved_at, err = stale
    st.warning(f"⚠️ {label}目前抓取失敗：{err}\n\n"
               f"以下改為顯示 **{saved_at:%Y/%m/%d %H:%M}** 的上次成功結果。")


def cached_fetch_concentration_data():
    try:
        data, stale = _with_lkg("concentration", _cached_fetch_concentration_data)
    except ScrapeError as e:
        st.error(f"❌ 籌碼集中度抓取失敗：{e}")
        return None
    if stale:
        _show_stale("籌碼集中度", stale)
    return data

def cached_scrape_yahoo_rankings(url):
    market = "otc" if url.endswith("TWO") else "listed"
    try:
        data, stale = _with_lkg(f"yahoo_rank_{market}", _cached_scrape_yahoo_rankings, url)
    except ScrapeError as e:
        st.error(f"❌ Yahoo 排行榜抓取失敗：{e}")
        return None
    if stale:
        _show_stale("Yahoo 排行榜", stale)
    elif data.attrs.get('parser') == 'fallback':
        st.info("ℹ️ Yahoo 排行榜的主要解析方式失效，已改用備援解析；網頁可能改版，數字請再核對。")
    return data

def cached_analyze_stock(stock_id: str, price_data: pd.DataFrame | None = None) -> dict:
    """
    個股指標（K、D、I 值、5 日均量），不畫圖。
    price_data 有給（例如 103 細篩抓過的日線）就直接用，不再呼叫 FinMind。
    """
    if price_data is None:
        try:
            price_data = _cached_price_history(stock_id)
        except AnalysisError as e:
            return {'status': 'error', 'error_type': e.kind,
                    'message': f"分析過程發生錯誤 ({stock_id}): {e}"}
    res = analyze_stock(stock_id, with_chart=False, price_data=price_data)
    res.pop('price_data', None)
    return res

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
    """依 error_type 顯示具體的錯誤提示。"""
    error_type = result.get('error_type', 'unknown')
    msg = result.get('message', '未知錯誤')
    if error_type == 'rate_limit':
        st.warning(
            f"⏳ **{stock_name}**：FinMind 額度用完或請求太頻繁，請稍後再試，"
            f"或在 [FinMind](https://finmindtrade.com/) 升級方案。"
        )
    elif error_type == 'network':
        st.warning(f"🌐 **{stock_name}**：網路連線失敗，請確認網路狀態後重試。")
    elif error_type == 'no_data':
        st.info(f"📭 **{stock_name}**：FinMind 無此股票資料（可能已下市或代碼有誤）。")
    elif error_type == 'insufficient_data':
        st.info(f"📊 **{stock_name}**：上市未滿60日，資料不足無法繪製技術分析圖。")
    else:
        st.error(f"❌ **{stock_name}** 分析失敗：{msg}")


def render_stock_chart(stock_code: str, stock_name: str, price_data: pd.DataFrame | None = None,
                       key: str | None = None) -> None:
    """畫一張個股技術分析圖（只在需要時建圖）。"""
    with st.spinner(f"正在產生 {stock_name} 的技術分析圖..."):
        try:
            chart_json = _cached_chart_json(stock_code, price_data)
        except AnalysisError as e:
            show_analysis_error(stock_name, {'error_type': e.kind, 'message': str(e)})
            return
        except Exception as e:                 # noqa: BLE001
            show_analysis_error(stock_name, {'error_type': 'unknown', 'message': str(e)})
            return
    st.plotly_chart(_fig_from_cache(chart_json), width="stretch", key=key)


def render_chart_picker(stocks: list[tuple[str, str]], key: str,
                        histories: dict | None = None) -> None:
    """
    以下拉選單挑一檔看技術分析圖。原本每檔一個 expander，所有圖都會先建好；
    改成選了才畫，大幅減少 FinMind 呼叫與記憶體。
    """
    if not stocks:
        return
    st.markdown("---")
    st.subheader("🔍 個股技術分析圖")
    labels = {f"{name} ({code})": (code, name) for code, name in stocks}
    choice = st.selectbox("選擇要查看的股票", ["（請選擇）", *labels], key=f"pick_{key}")
    if choice in labels:
        code, name = labels[choice]
        render_stock_chart(code, name, (histories or {}).get(code), key=f"chart_{key}_{code}")


def _drop_no_data(df: pd.DataFrame, cache: dict, code_col: str = '代碼',
                  name_col: str = '名稱') -> pd.DataFrame:
    """移除 FinMind 查無資料（error_type == 'no_data'）的股票，並在畫面註明略過了哪些。"""
    codes = df[code_col].astype(str).str.strip()
    bad = codes.map(lambda c: cache.get(c, {}).get('error_type') == 'no_data')
    if bad.any():
        skipped = [f"{n}({c})" for c, n in zip(codes[bad], df.loc[bad, name_col])]
        st.caption(f"已略過 FinMind 查無資料的 {len(skipped)} 檔：{'、'.join(skipped)}")
    return df[~bad].copy()


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


# --------------------------------------------------------------------------------
# Streamlit UI 介面佈局
# --------------------------------------------------------------------------------

def display_concentration_visualization(df: pd.DataFrame):
    """
    整合遠端 service-868047938877 的籌碼集中度視覺化到本地：
    - 統計卡片（最高1日集中度、最高10日均量、股票總數）
    - K/D 散佈圖（X=K值, Y=10日均量, 泡泡=1日集中度）
    - 四象限分析（依 I 值分 4 圖, K值 vs 1日集中度）
    - 個股集中度長條圖（依選擇顯示各周期集中度）
    """
    st.markdown("---")
    st.subheader("📊 籌碼集中度視覺化分析")

    viz_df = df.copy()

    # --- 解析輔助欄位（使用模組層級函式） ---
    viz_df['_K'] = viz_df['KD'].apply(_parse_k)
    viz_df['_D'] = viz_df['KD'].apply(_parse_d)
    viz_df['_I'] = viz_df['I值'].apply(_parse_i)

    # 轉型數值欄位
    conc_cols = ['1日集中度', '5日集中度', '10日集中度', '20日集中度', '60日集中度', '120日集中度']
    vol_col = '10日均量'
    name_col = '股票名稱' if '股票名稱' in viz_df.columns else '名稱'

    for col in conc_cols + [vol_col]:
        if col in viz_df.columns:
            viz_df[col] = pd.to_numeric(viz_df[col], errors='coerce')

    # --- 統計卡片 ---
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

    # --- Tabs ---
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

            # ── K/D 散佈圖：y 軸已是量，所以不用泡泡；均量差距大，用對數刻度 ──
            if tab_type == "kd":
                sc = viz_df[viz_df['_K'].notna()].copy()
                fig_kd = go.Figure()
                add_i_scatter(fig_kd, sc, '_K', vol_col, name_col,
                              x_label='K值', y_label='10日均量', y_fmt=',.0f', y_suffix=' 張')
                fig_kd.add_vline(x=20, annotation_text="超賣 20", annotation_position="bottom right", **REF_LINE)
                fig_kd.add_vline(x=80, annotation_text="超買 80", annotation_position="bottom left", **REF_LINE)
                fig_kd.update_layout(title='K值 vs 10日均量（顏色 = I 值）', xaxis_title='K值 (0–100)',
                                     yaxis_title='10日均量（張，對數刻度）', xaxis=dict(range=[0, 100]),
                                     yaxis_type='log', height=520)
                st.plotly_chart(legend_top(fig_kd), width="stretch")

            # ── 依 I 值拆開的小倍數圖：K值 vs 1日集中度，泡泡 = 10日均量 ──
            elif tab_type == "quad":
                sc2 = viz_df[viz_df['_K'].notna() & viz_df['1日集中度'].notna()].copy()
                fig_quad = i_small_multiples(
                    sc2, '_K', '1日集中度', name_col, x_label='K值', y_label='1日集中度',
                    y_suffix='%', size_col=vol_col, size_label='10日均量(張)',
                    title='依 I 值分組：K值 vs 1日集中度（泡泡大小 = 10日均量）')
                if fig_quad is None:
                    st.info("沒有可繪製的資料。")
                else:
                    st.plotly_chart(fig_quad, width="stretch")

            # ── 個股集中度長條圖 ─────────────────────────────
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
                            UP_COLOR if (v is not None and v > 0) else DOWN_COLOR
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
                        st.plotly_chart(fig_bar, width="stretch")


def display_concentration_results():
    st.header("📊 1日籌碼集中度選股結果")
    with st.spinner("正在獲取並篩選籌碼集中度資料..."):
        stock_data = cached_fetch_concentration_data()
        if stock_data is not None:
            _conc_params  = st.session_state.get('filter_params', {})
            _min_vol_conc = _conc_params.get('min_vol_conc', 2000)
            try:
                filtered_stocks = services.filter_concentration(stock_data, _min_vol_conc)
            except ValueError as e:
                st.error(f"❌ {e}")
                return
            
            if filtered_stocks is not None and not filtered_stocks.empty:
                st.success(f"找到 {len(filtered_stocks)} 檔符合條件的股票，正在進行技術指標分析...")

                # 並發分析（以 ThreadPoolExecutor 取代逐筆順序呼叫）
                stock_codes_ordered = [str(r.代碼) for r in filtered_stocks.itertuples()]
                progress_bar = st.progress(0, text="分析進度")
                concentration_cache = _batch_kd_analyze(
                    stock_codes_ordered, progress_bar, label_prefix="正在分析"
                )
                progress_bar.empty()
                filtered_stocks = _drop_no_data(filtered_stocks, concentration_cache, name_col='股票名稱')
                stock_codes_ordered = [str(r.代碼) for r in filtered_stocks.itertuples()]

                k_values, d_values, i_values = [], [], []
                for code in stock_codes_ordered:
                    result = concentration_cache.get(code, {'status': 'error', 'message': '分析失敗'})
                    if result['status'] == 'success':
                        indicators = result.get('indicators', {})
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

                # ── 整合遠端視覺化服務：直接在本地產生統計卡片與圖表 ──
                display_concentration_visualization(filtered_stocks)

                render_chart_picker(
                    [(str(r.代碼), str(r.股票名稱)) for r in filtered_stocks.itertuples()], key="conc")
            else:
                st.warning("沒有找到或篩選出符合條件的股票。")


def _describe_103(p: dict) -> str:
    base = "昨收" if p['red_k_base'] == 'prev_close' else "開盤價"
    return f"""
**篩選條件（本機計算，等同 Goodinfo 我的選股103）：**
1.  紅K棒幅 {p['red_k_min']}% ~ {p['red_k_max']}%（分母：{base}）
2.  成交張數 {p['vol_min']:,.0f} ~ {p['vol_max']:,.0f} 張
3.  季線乖離 {p['ma60_dev_min']}% ~ {p['ma60_dev_max']}%
4.  週K值 {p['wk_min']} ~ {p['wk_max']}
5.  週K值向上
6.  月線 < 季線（空頭排列）
7.  日K > 日D
8.  今日成交張數 > {p['vol_ratio']} × 昨日成交張數
"""


def _plot_103_margins(df: pd.DataFrame, p: dict):
    """
    選股103「條件安全邊際」熱度表：每格 0 = 剛好壓在門檻上、1 = 離門檻很遠。
    格子內顯示實際數值，滑鼠移上去顯示門檻，一眼看出哪檔是勉強過關。
    """
    def rng(v, lo, hi):
        return ((np.minimum(v - lo, hi - v)) / ((hi - lo) / 2)).clip(0, 1)

    num = lambda c: pd.to_numeric(df.get(c), errors='coerce')
    cols = {
        '紅K棒幅': (rng(num('紅K棒幅'), p['red_k_min'], p['red_k_max']),
                   num('紅K棒幅').map('{:.2f}%'.format), f"{p['red_k_min']}%～{p['red_k_max']}%"),
        '成交張數': ((np.log10(num('成交張數') / p['vol_min'])).clip(0, 1),
                    num('成交張數').map('{:,.0f}'.format), f"≥ {p['vol_min']:,.0f} 張（10 倍以上為滿格）"),
        '量比': (((num('量比') - p['vol_ratio']) / p['vol_ratio']).clip(0, 1),
                num('量比').map('{:.2f}x'.format), f"> {p['vol_ratio']}x"),
        '季線乖離': (rng(num('季線乖離'), p['ma60_dev_min'], p['ma60_dev_max']),
                    num('季線乖離').map('{:+.2f}%'.format), f"{p['ma60_dev_min']}%～{p['ma60_dev_max']}%"),
        '週K': (rng(num('週K'), p['wk_min'], p['wk_max']),
               num('週K').map('{:.1f}'.format), f"{p['wk_min']}～{p['wk_max']}"),
        '週K向上': ((num('週K變化') / 10).clip(0, 1),
                  num('週K變化').map('{:+.1f}'.format), "較上週上升（+10 以上為滿格）"),
        '月<季': ((-num('月季線差(%)') / 5).clip(0, 1),
                 num('月季線差(%)').map('{:+.2f}%'.format), "月線低於季線（差 5% 以上為滿格）"),
        '日K>日D': (((num('日K') - num('日D')) / 10).clip(0, 1),
                   (num('日K') - num('日D')).map('{:+.1f}'.format), "K 高於 D（差 10 以上為滿格）"),
    }
    labels = [f"{n}({c})" for n, c in zip(df['名稱'], df['代碼'])]
    z = pd.DataFrame({k: v[0].to_numpy() for k, v in cols.items()}, index=labels)
    txt = pd.DataFrame({k: v[1].to_numpy() for k, v in cols.items()}, index=labels)
    hover = pd.DataFrame({k: [f"{t}（門檻：{v[2]}）" for t in v[1]] for k, v in cols.items()}, index=labels)
    order = z.min(axis=1).sort_values().index          # 最勉強的排最上面
    fig = heat_table(z.loc[order], txt.loc[order], hover=hover.loc[order], colorscale=MARGIN_SCALE,
                     zmin=0, zmax=1, title='條件安全邊際（顏色越淺越接近門檻；最勉強過關的排在最上面）',
                     colorbar_title='邊際')
    st.plotly_chart(fig, width="stretch")


def display_my_103_results():
    st.header("⭐ 我的選股103（本機計算）")
    params = st.session_state.get('params_103', asdict(Screen103Params()))

    try:
        with st.spinner("正在抓取全市場行情並計算條件（首次約需 30–60 秒）..."):
            res, stale = _with_lkg(lkg_key("screen103", params), _cached_screen_103, params)
    except ScreenerError as e:
        st.error(f"❌ 選股失敗：{e}")
        st.caption("失敗結果不會被快取，稍後重新按一次按鈕即可重試。")
        return
    if stale:
        _show_stale("我的選股103", stale)

    src_label = {'twse+tpex': '證交所 + 櫃買中心', 'finmind': 'FinMind'}.get(res['source'], res['source'])
    st.caption(
        f"資料日期：**{res['trade_date']:%Y/%m/%d}**（前一交易日 {res['prev_date']:%Y/%m/%d}）　"
        f"來源：{src_label}　全市場 {res['universe_size']} 檔 → 粗篩 {res['n_candidates']} 檔"
    )
    for note in res.get('notes', []):
        st.info(f"ℹ️ {note}")
    if res['errors']:
        with st.expander(f"⚠️ {len(res['errors'])} 檔候選股抓不到日線，未納入判斷"):
            st.write(res['errors'])
    st.info(_describe_103(params))

    scraped_df = res['matches'].copy()
    if scraped_df.empty:
        st.warning("今天沒有符合全部條件的股票。")
        return

    st.success(f"共 {len(scraped_df)} 檔符合條件，正在進行技術指標分析...")
    raw_codes = [str(c).strip() for c in scraped_df['代碼']]
    # 細篩已抓過符合者的日線：直接拿來算 I 值、畫圖，不再重抓 FinMind
    histories = res.get('histories') or {}
    progress_bar = st.progress(0, text="分析進度")
    analysis_cache = _batch_kd_analyze(
        raw_codes, progress_bar, label_prefix="正在分析",
        analyze_fn=lambda c: cached_analyze_stock(c, histories.get(c)))
    progress_bar.empty()

    # KD 直接用選股計算的日K/日D（與 Goodinfo 同算法）；I 值來自個股分析
    scraped_df['KD'] = [f"K:{k:.2f} D:{d:.2f}" for k, d in zip(scraped_df['日K'], scraped_df['日D'])]
    i_values = []
    for code in raw_codes:
        result = analysis_cache.get(code, {'status': 'error'})
        i_val = result.get('indicators', {}).get('i_value') if result['status'] == 'success' else None
        i_values.append(f"{i_val:.0f}" if i_val is not None else ("錯誤" if result['status'] != 'success' else "N/A"))
    scraped_df['I值'] = [str(v) for v in i_values]  # 混型別會讓 Arrow 轉換失敗

    display_columns = [
        '代碼', '名稱', 'KD', 'I值', '市場', '股價日期',
        '成交', '漲跌價', '漲跌幅', '成交張數', '紅K棒幅', '季線乖離', '週K'
    ]
    st.dataframe(scraped_df[[c for c in display_columns if c in scraped_df.columns]])

    if {'量比', '週K變化', '月季線差(%)'}.issubset(scraped_df.columns):
        _plot_103_margins(scraped_df, params)

    render_chart_picker([(str(r.代碼).strip(), str(r.名稱).strip()) for r in scraped_df.itertuples()],
                        key="103", histories=histories)


def display_monthly_revenue_visualization(df: pd.DataFrame):
    """
    整合遠端視覺化服務功能到本地：
    - 統計指標卡片
    - 年增率 / 月增率 Top 10 柱狀圖
    - K值 vs 年增率 四象限散佈圖 (依 I 值分類)
    """

    st.markdown("---")
    st.subheader("📊 月營收視覺化分析")

    viz_df = df.copy()

    # --- 解析 K / I 值（使用模組層級函式） ---
    viz_df['_K值'] = viz_df['KD'].apply(_parse_k)
    viz_df['_I值'] = viz_df['I值'].apply(_parse_i)

    # --- 終極精準版：年增與月增都強制要求包含 '%' 符號，並排除干擾欄位 ---
    yoy_col = None
    mom_col = None
    vol_col = None

    for col in viz_df.columns:
        # 去除空白字元以便精準比對
        c = col.replace(' ', '').replace('\xa0', '')
        
        # 找年增：必須有「年增」+ 必須有「%」+ 排除「累計」+ 排除「前X月」
        if yoy_col is None and '年增' in c and '%' in c and '累計' not in c and '前' not in c:
            yoy_col = col
            
        # 找月增：必須有「月增」+ 必須有「%」+ 排除「累計」+ 排除「前X月」
        elif mom_col is None and '月增' in c and '%' in c and '累計' not in c and '前' not in c:
            mom_col = col
            
        # 找成交量：優先找明確的成交張數
        elif vol_col is None and any(k in c for k in ['成交張數', '單日張數', '成交量']):
            vol_col = col

    # 若精準比對失敗，才退回寬鬆的備用機制 (以防網站哪天把 % 拿掉)
    for col in viz_df.columns:
        c = col.replace(' ', '').replace('\xa0', '')
        if yoy_col is None and any(k in c for k in ['YoY', 'yoy']):
            yoy_col = col
        if mom_col is None and any(k in c for k in ['MoM', 'mom']):
            mom_col = col
        if vol_col is None and any(k in c for k in ['成交張數', '張數', '量(張)', '成交量']):
            vol_col = col

    # 轉型為數值
    for col in [yoy_col, mom_col, vol_col]:
        if col:
            viz_df[col] = pd.to_numeric(viz_df[col], errors='coerce')

    # --- 依成交量篩選 (> 5000 張，與遠端服務相同邏輯) ---
    if vol_col and not viz_df[vol_col].isna().all():
        viz_filtered = viz_df[viz_df[vol_col] > 5000].copy()
    else:
        viz_filtered = viz_df.copy()

    n = len(viz_filtered)

    # --- 統計指標卡片 ---
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

    # --- 若未偵測到關鍵欄位，顯示可用欄位清單並返回 ---
    if not yoy_col and not mom_col:
        skip_cols = {'代碼', '名稱', 'KD', 'I值', '_K值', '_I值'}
        available = [c for c in viz_df.columns if c not in skip_cols]
        st.warning(
            f"⚠️ 未偵測到年增率／月增率欄位，無法繪製圖表。\n\n"
            f"可用欄位：`{'`、`'.join(available[:20])}`"
        )
        return

    # --- 決定要顯示哪些 Tab ---
    tab_defs = []
    yoy_hist_cols = [c for c in viz_df.columns if re.fullmatch(r'前\d+月年增\(%\)', str(c))]
    if yoy_col and yoy_hist_cols:
        tab_defs.append(("🔥 年增率熱度表", "heat"))
    if yoy_col and n > 0 and not viz_filtered[yoy_col].isna().all():
        tab_defs.append(("📊 年增率 Top10", "yoy"))
    if mom_col and n > 0 and not viz_filtered[mom_col].isna().all():
        tab_defs.append(("📊 月增率 Top10", "mom"))
    if viz_filtered['_K值'].notna().any() and yoy_col:
        tab_defs.append(("🎯 依 I 值分組", "quad"))

    if not tab_defs:
        return

    tabs = st.tabs([t[0] for t in tab_defs])

    for tab, (_, tab_type) in zip(tabs, tab_defs):
        with tab:

            # ── 年增率熱度表：所有入選股票（不受成交量過濾），左舊右新 ──
            if tab_type == "heat":
                hcols = sorted(yoy_hist_cols, key=lambda c: -int(re.search(r'\d+', c).group())) + [yoy_col]
                h = viz_df.copy()
                if '同期排名' in h.columns:
                    h = h.assign(_rank=h['同期排名'].astype(str).str.split('/').str[0].astype(float))
                    h = h.sort_values(['_rank', yoy_col], ascending=[True, False])
                else:
                    h = h.sort_values(yoy_col, ascending=False)
                labels = [f"{n_}({c_})" for n_, c_ in zip(h['名稱'], h['代碼'])]
                z = h[hcols].apply(pd.to_numeric, errors='coerce')
                z.index = labels
                z.columns = [c.replace('年增(%)', '').replace('(%)', '') or '當月' for c in hcols]
                z = z.rename(columns={'': '當月'})
                txt = z.map(lambda v: f"{v:.0f}%" if pd.notna(v) else "–")
                hover = txt.copy()
                if '同期排名' in h.columns:
                    z['同期排名'] = np.nan
                    txt['同期排名'] = list(h['同期排名'].astype(str))
                    hover['同期排名'] = txt['同期排名']
                fig_h = heat_table(z, txt, hover=hover, colorscale=REVENUE_SCALE, zmin=0, zmax=100,
                                   title='單月營收年增率（左舊右新；顏色超過 100% 以最深色表示）',
                                   colorbar_title='年增率 %')
                st.plotly_chart(fig_h, width="stretch")
                st.caption("依同期排名、當月年增率排序。顏色越深代表成長越多；看整列是否一路深色，可判斷成長是否穩定。")

            # ── 年增率 Top10：單一系列，用單色 ──
            elif tab_type == "yoy":
                top10 = viz_filtered.nlargest(10, yoy_col)[['名稱', '代碼', yoy_col]].copy()
                top10['股票'] = top10['名稱'] + '(' + top10['代碼'].astype(str) + ')'
                top10 = top10.sort_values(yoy_col)
                fig = go.Figure(go.Bar(
                    x=top10[yoy_col], y=top10['股票'], orientation='h', marker_color=UP_COLOR,
                    text=top10[yoy_col].round(1).astype(str) + '%', textposition='outside',
                    hovertemplate='<b>%{y}</b><br>年增率：%{x:.1f}%<extra></extra>'))
                fig.update_layout(title='月營收年增率 Top 10（成交量 > 5,000 張）', xaxis_title='年增率 (%)',
                                  height=max(360, len(top10) * 32 + 100), margin=dict(l=10))
                st.plotly_chart(fig, width="stretch")

            # ── 月增率 Top10：可能為負，紅正綠負 ──
            elif tab_type == "mom":
                top10m = viz_filtered.nlargest(10, mom_col)[['名稱', '代碼', mom_col]].copy()
                top10m['股票'] = top10m['名稱'] + '(' + top10m['代碼'].astype(str) + ')'
                top10m = top10m.sort_values(mom_col)
                fig_m = go.Figure(go.Bar(
                    x=top10m[mom_col], y=top10m['股票'], orientation='h',
                    marker_color=np.where(top10m[mom_col] >= 0, UP_COLOR, DOWN_COLOR),
                    text=top10m[mom_col].round(1).astype(str) + '%', textposition='outside',
                    hovertemplate='<b>%{y}</b><br>月增率：%{x:.1f}%<extra></extra>'))
                fig_m.update_layout(title='月營收月增率 Top 10（成交量 > 5,000 張）', xaxis_title='月增率 (%)',
                                    height=max(360, len(top10m) * 32 + 100), margin=dict(l=10))
                st.plotly_chart(fig_m, width="stretch")

            # ── 依 I 值拆開的小倍數圖：K值 vs 年增率，泡泡 = 成交量 ──
            elif tab_type == "quad":
                scatter_df = viz_filtered[viz_filtered['_K值'].notna() & viz_filtered[yoy_col].notna()].copy()
                scatter_df['_I'] = scatter_df['_I值']
                fig_q = i_small_multiples(
                    scatter_df, '_K值', yoy_col, '名稱', x_label='K值', y_label='年增率', y_fmt='.1f',
                    y_suffix='%', size_col=vol_col, size_label='成交張數',
                    title='依 I 值分組：K值 vs 月營收年增率（泡泡大小 = 成交量）')
                if fig_q is None:
                    st.info("沒有可繪製的資料。")
                else:
                    st.plotly_chart(fig_q, width="stretch")


def display_monthly_revenue_results():
    st.header("📈 月營收強勢股（本機計算）")
    params = st.session_state.get('params_rev', asdict(RevenueParams()))
    try:
        with st.spinner("正在讀取公開資訊觀測站月營收並計算條件（首次約需 1 分鐘）..."):
            res, stale = _with_lkg(lkg_key("screen_revenue", params), _cached_screen_revenue, params)
    except ScreenerError as e:
        st.error(f"❌ 月營收選股失敗：{e}")
        st.caption("失敗結果不會被快取，稍後重新按一次按鈕即可重試。")
        return
    if stale:
        _show_stale("月營收選股", stale)

    months_txt = "、".join(f"{y}/{m:02d}（{n}家）" for y, m, n in res['months_loaded'] if n)
    st.caption(f"營收資料：{months_txt}　全市場 {res['universe_size']} 家 → 年增率條件 {res['n_candidates']} 家")
    for note in res.get('notes', []):
        st.info(f"ℹ️ {note}")
    if res['errors']:
        with st.expander(f"⚠️ {len(res['errors'])} 檔抓不到歷年月營收，未納入同期排名判斷"):
            st.write(res['errors'])

    scraped_df = res['matches'].copy()
    if not scraped_df.empty:
        st.success(f"共 {len(scraped_df)} 檔符合條件，正在進行技術指標分析...")

        # 並發分析（以 ThreadPoolExecutor 取代逐筆順序呼叫）
        raw_codes = [str(r.代碼).strip() for r in scraped_df.itertuples()]
        progress_bar = st.progress(0, text="分析進度")
        revenue_cache = _batch_kd_analyze(raw_codes, progress_bar, label_prefix="正在分析")
        progress_bar.empty()
        scraped_df = _drop_no_data(scraped_df, revenue_cache)
        raw_codes = [str(r.代碼).strip() for r in scraped_df.itertuples()]

        k_values, d_values, i_values = [], [], []
        for code in raw_codes:
            if not code or code == 'nan':
                k_values.append("N/A"); d_values.append("N/A"); i_values.append("N/A")
                continue
            result = revenue_cache.get(code, {'status': 'error', 'message': '分析失敗'})
            if result['status'] == 'success':
                indicators = result.get('indicators', {})
                k_val = indicators.get('k')
                d_val = indicators.get('d')
                i_val = indicators.get('i_value')
                k_values.append(f"{k_val:.2f}" if k_val is not None else "N/A")
                d_values.append(f"{d_val:.2f}" if d_val is not None else "N/A")
                i_values.append(f"{i_val:.0f}" if i_val is not None else "N/A")
            else:
                k_values.append("錯誤"); d_values.append("錯誤"); i_values.append("錯誤")

        scraped_df['KD'] = [f"K:{k} D:{d}" for k, d in zip(k_values, d_values)]
        scraped_df['I值'] = i_values

        st.info(f"""
**篩選條件（本機計算，等同 Goodinfo 月營收選股03）：**
1.  單月營收年增率 – 當月 ≥ {params['yoy_cur_min']}%（當月 = {'各公司最新公告月份' if params.get('per_company_month') else '全市場最新公告月份'}）
2.  單月營收年增率 – 前1～{params['n_prev']}月皆 ≥ {params['yoy_prev_min']}%
3.  單月營收創歷年同期前 {params['top_n']} 高
""")

        all_cols = scraped_df.columns.tolist()
        try:
            name_idx = all_cols.index('名稱')
            new_cols = all_cols[:name_idx+1] + ['KD', 'I值'] + [c for c in all_cols[name_idx+1:] if c not in ['KD', 'I值']]
            scraped_df = scraped_df[new_cols]
        except ValueError:
            scraped_df = scraped_df[['代碼', '名稱', 'KD', 'I值'] + [c for c in all_cols if c not in ['代碼', '名稱', 'KD', 'I值']]]
        
        st.dataframe(scraped_df)

        # ── 整合遠端視覺化服務：直接在本地產生統計卡片與圖表 ──
        display_monthly_revenue_visualization(scraped_df)

        render_chart_picker([(str(r.代碼).strip(), str(r.名稱).strip()) for r in scraped_df.itertuples()
                             if str(r.代碼).strip() not in ('', 'nan')], key="rev")
    else:
        st.warning("目前沒有符合全部條件的股票。")


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

    def parse_i(v):
        try:
            return float(str(v).strip()) if str(v).strip() not in ('N/A', '錯誤', 'nan', '') else None
        except Exception:
            return None

    viz_df['_I'] = viz_df['I訊號'].apply(parse_i)

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
                
                k_val = f"{indicators.get('k'):.2f}" if indicators.get('k') is not None else "N/A"
                d_val = f"{indicators.get('d'):.2f}" if indicators.get('d') is not None else "N/A"
                
                i_val = indicators.get('i_value')
                # 這裡只儲存純文字值，不加入HTML標籤，以便 CSV 下載正確資料
                i_text = f"{i_val:.0f}" if i_val is not None else "N/A"

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

            # 定義樣式函式：僅用於顯示顏色
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
            render_stock_chart(stock_code, stock_name, key=f"tech_{stock_code}")
        with tab2:
            with st.spinner("正在生成月營收趨勢圖..."):
                revenue_json, revenue_error = cached_plot_revenue(stock_code)
                if not revenue_error:
                    st.plotly_chart(_fig_from_cache(revenue_json), width="stretch", key=f"rev_{stock_code}")
                else:
                    st.error(f"無法生成營收圖: {revenue_error}")
        with tab3:
            with st.spinner("正在生成大戶股權變化圖..."):
                shareholder_json, shareholder_error = cached_plot_shareholders(stock_code)
                if not shareholder_error:
                    st.plotly_chart(_fig_from_cache(shareholder_json), width="stretch", key=f"holders_{stock_code}")
                else:
                    st.error(f"無法生成大戶股權圖: {shareholder_error}")

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

    # ── 改善 7：選股條件參數化 ────────────────────────────────────────────
    st.sidebar.header("⚙️ 篩選參數")
    with st.sidebar.expander("排行榜篩選條件", expanded=False):
        filter_min_price    = st.slider("最低股價（元）",      10, 200,  35, key="fp_price")
        filter_min_change   = st.slider("最低漲幅（%）",       0.5, 10.0, 2.0, step=0.5, key="fp_change")
        filter_vol_ratio    = st.slider("預估量 / 5日均量 倍數", 1.0, 5.0, 2.0, step=0.5, key="fp_volr")

    with st.sidebar.expander("籌碼集中度篩選條件", expanded=False):
        filter_min_vol_conc = st.number_input("最低10日均量（張）", value=2000, step=500, key="fp_conc_vol")

    with st.sidebar.expander("我的選股103 條件", expanded=False):
        _d = Screen103Params()
        p103_red = st.slider("紅K棒幅（%）", 0.0, 10.0, (_d.red_k_min, _d.red_k_max), step=0.5, key="p103_red")
        p103_base = st.radio("棒幅分母", ["prev_close", "open"], horizontal=True, key="p103_base",
                             format_func=lambda x: "昨收" if x == "prev_close" else "開盤價")
        p103_vol = st.number_input("最低成交張數", value=int(_d.vol_min), step=500, key="p103_vol")
        p103_dev = st.slider("季線乖離（%）", -15.0, 15.0, (_d.ma60_dev_min, _d.ma60_dev_max), step=0.5, key="p103_dev")
        p103_wk = st.slider("週K值上限", 10, 100, int(_d.wk_max), step=5, key="p103_wk")
        p103_vr = st.slider("量增倍數（今 / 昨）", 1.0, 3.0, _d.vol_ratio, step=0.1, key="p103_vr")
    with st.sidebar.expander("月營收選股條件", expanded=False):
        _r = RevenueParams()
        prev_cur = st.slider("當月年增率下限（%）", 0, 50, int(_r.yoy_cur_min), step=5, key="prev_cur")
        prev_min = st.slider("前幾月年增率下限（%）", 0, 50, int(_r.yoy_prev_min), step=5, key="prev_min")
        prev_n = st.slider("連續檢查前幾個月", 1, 5, _r.n_prev, key="prev_n")
        prev_top = st.slider("創歷年同期前 N 高（0 = 不檢查）", 0, 5, _r.top_n, key="prev_top")
        prev_pc = st.checkbox("各公司以自己最新公告月份為當月", value=False, key="prev_pc",
                              help="不勾選＝與 Goodinfo 相同，全市場統一以最新有公告的月份為當月")
    st.session_state['params_rev'] = asdict(RevenueParams(
        yoy_cur_min=float(prev_cur), yoy_prev_min=float(prev_min), n_prev=prev_n, top_n=prev_top,
        months_to_load=max(6, prev_n + 2), per_company_month=prev_pc,
    ))
    st.session_state['params_103'] = asdict(Screen103Params(
        red_k_min=p103_red[0], red_k_max=p103_red[1], red_k_base=p103_base,
        vol_min=float(p103_vol), ma60_dev_min=p103_dev[0], ma60_dev_max=p103_dev[1],
        wk_max=float(p103_wk), vol_ratio=p103_vr,
    ))

    # 把參數存進 session_state，讓 display 函式讀取
    st.session_state['filter_params'] = {
        'min_price':    filter_min_price,
        'min_change':   filter_min_change,
        'vol_ratio':    filter_vol_ratio,
        'min_vol_conc': filter_min_vol_conc,
    }

    # ── 選股策略按鈕 ──────────────────────────────────────────────────────
    st.sidebar.header("選股策略")
    if st.sidebar.button("1日籌碼集中度選股"):
        st.session_state.action = "concentration_pick"
    if st.sidebar.button("我的選股103（本機計算）"):
        st.session_state.action = "my_stock_picks"
    if st.sidebar.button("月營收選股（本機計算）"):
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

    # ── 內容顯示路由 ──────────────────────────────────────────────────────
    if 'action' in st.session_state:
        action = st.session_state.action
        if action == "concentration_pick":
            display_concentration_results()
        elif action == "my_stock_picks":
            display_my_103_results()
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