"""
各功能頁共用：快取包裝（失敗不快取、盤中／盤後動態 TTL）、上次成功結果、錯誤提示、個股圖表元件。
"""
import time

import pandas as pd
import plotly.io as pio
import streamlit as st

import services
from concentration_1day import fetch_stock_concentration_data
from financial_snapshot import run_financial_snapshot
from market_calendar import is_trading_hours
from scrape_utils import ScrapeError
from stock_analyzer import AnalysisError, analyze_stock, build_chart, fetch_price_history
from stock_information_plot import plot_stock_major_shareholders, plot_stock_revenue_trend
from yahoo_scraper import scrape_yahoo_stock_rankings

# --------------------------------------------------------------------------------
# 改善 2：交易時間感知 TTL
# --------------------------------------------------------------------------------
def time_bucket(intraday_sec: int, offhours_sec: int = 3600) -> str:
    """
    快取的時間區段鍵：每次呼叫時依「現在是否盤中」決定區段長度，區段一換，快取鍵就不同 → 重新抓取。
    （不能直接寫 @st.cache_data(ttl=依盤中盤後計算)：decorator 的 ttl 只在程式載入時算一次，之後不會切換。）
    """
    trading = is_trading_hours()
    sec = intraday_sec if trading else offhours_sec
    return f"{'i' if trading else 'o'}{sec}-{int(time.time() // sec)}"

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
# K、D、I 值：表格裡一律存數字（算不出來 = 空白），只在顯示時格式化。
# 不再組成 'K:xx D:xx' 字串再拆回來；也避免數字與「錯誤」混在同一欄讓 Arrow 轉換失敗。
# -------------------------------------------------------------------------------
KDI_COLUMN_CONFIG = {
    "K": st.column_config.NumberColumn("K", format="%.2f"),
    "D": st.column_config.NumberColumn("D", format="%.2f"),
    "I值": st.column_config.NumberColumn("I值", format="%d", help="階梯訊號 -3～+3；空白＝算不出來"),
}


def add_kdi_columns(df: pd.DataFrame, cache: dict, code_col: str = '代碼',
                    name_col: str = '名稱', blank_label: str = 'K、D、I 值') -> pd.DataFrame:
    """
    依批次分析結果加上數字欄位 K、D、I值（失敗或資料不足為 NaN），
    並在畫面註明哪些股票的指標算不出來。
    """
    codes = df[code_col].astype(str).str.strip()
    ind = [services.indicator_values(cache.get(c, {})) for c in codes]
    df = df.copy()
    df['K'] = pd.Series([i.get('k') for i in ind], index=df.index, dtype='float64')
    df['D'] = pd.Series([i.get('d') for i in ind], index=df.index, dtype='float64')
    df['I值'] = pd.Series([i.get('i_value') for i in ind], index=df.index, dtype='float64')
    failed = [f"{n}({c})" for c, n, i in zip(codes, df[name_col], ind)
              if not i and c not in ('', 'nan')]
    if failed:
        st.caption(f"⚠️ {len(failed)} 檔技術指標分析失敗（{blank_label}留白）：{'、'.join(failed)}")
    return df

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
# 快取期限由 time_bucket 參數控制（盤中／盤後區段長度不同）；decorator 的 ttl 只負責清掉過期的舊區段。
@st.cache_data(ttl=3600, show_spinner=False)
def _screen_103_by_bucket(params: dict, bucket: str) -> dict:
    """我的選股103（本機計算）。ScreenerError 直接往外拋，不會被快取。"""
    return services.run_screen_103(params)

def _cached_screen_103(params: dict) -> dict:
    return _screen_103_by_bucket(params, time_bucket(1800, 3600))       # 盤中30分鐘；盤後1小時

@st.cache_data(ttl=21600, show_spinner=False)
def _screen_revenue_by_bucket(params: dict, bucket: str) -> dict:
    """月營收選股（本機計算）。ScreenerError 直接往外拋，不會被快取。"""
    return services.run_screen_revenue(params)

def _cached_screen_revenue(params: dict) -> dict:
    return _screen_revenue_by_bucket(params, time_bucket(1800, 21600))  # 盤中30分鐘；盤後6小時

@st.cache_data(ttl=3600)
def _concentration_by_bucket(bucket: str):
    return fetch_stock_concentration_data()          # ScrapeError 往外拋，不進快取

def _cached_fetch_concentration_data():
    return _concentration_by_bucket(time_bucket(300, 3600))             # 盤中5分鐘；盤後1小時

@st.cache_data(ttl=300)
def _yahoo_rankings_by_bucket(url, bucket: str):
    return scrape_yahoo_stock_rankings(url)          # ScrapeError 往外拋，不進快取

def _cached_scrape_yahoo_rankings(url):
    return _yahoo_rankings_by_bucket(url, time_bucket(60, 300))          # 盤中1分鐘；盤後5分鐘

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

class _PlotFailed(Exception):
    """快取函式內失敗時拋出，讓 st.cache_data 不快取失敗結果。"""


@st.cache_data(ttl=86400)
def _plot_revenue_json(stock_id: str) -> str:
    fig, err = plot_stock_revenue_trend(stock_id)
    if fig is None or err:
        raise _PlotFailed(err or "月營收圖產生失敗")
    return _fig_to_cache(fig)

@st.cache_data(ttl=43200)                            # 大戶週資料：12 小時
def _plot_shareholders_json(stock_id: str) -> str:
    fig, err = plot_stock_major_shareholders(stock_id)
    if fig is None or err:
        raise _PlotFailed(err or "大戶持股圖產生失敗")
    return _fig_to_cache(fig)

def cached_plot_revenue(stock_id: str):
    """回傳 (圖表 JSON, 錯誤訊息)；失敗不進快取，下次會重試。"""
    try:
        return _plot_revenue_json(stock_id), None
    except _PlotFailed as e:
        return None, str(e)

def cached_plot_shareholders(stock_id: str):
    try:
        return _plot_shareholders_json(stock_id), None
    except _PlotFailed as e:
        return None, str(e)


@st.cache_data(ttl=21600, show_spinner=False)
def _quality_by_bucket(rev_params: dict, q_params: dict, bucket: str) -> dict:
    """四條件選股：月營收母體（沿用月營收選股的快取）→ ②③①④ 逐關篩。失敗直接拋出，不進快取。"""
    rev = _cached_screen_revenue(rev_params)
    res = services.run_screen_quality(rev["matches"], q_params, _cached_price_history)
    res["revenue_notes"] = rev.get("notes", [])
    res["months_loaded"] = rev.get("months_loaded", [])
    return res

def cached_screen_quality(rev_params: dict, q_params: dict) -> dict:
    return _quality_by_bucket(rev_params, q_params, time_bucket(1800, 21600))   # 盤中30分鐘；盤後6小時

@st.cache_data(ttl=3600, show_spinner=False)
def cached_financial_snapshot(stock_id: str) -> dict:
    """季報指標（底層已有 1 天磁碟快取）；FinMind 失敗時拋出，不進快取。"""
    return run_financial_snapshot(stock_id)

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
