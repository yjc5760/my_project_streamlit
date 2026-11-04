"""個股查詢：技術分析、日K線、月營收、大戶持股、季報指標。"""
import pandas as pd
import streamlit as st
import twstock
from plotly.subplots import make_subplots

from kline_chart import make_kline_figure
from stock_information_plot import get_stock_code
from viz import SERIES, legend_top
from views.common import (_cached_price_history, _fig_from_cache, cached_financial_snapshot, cached_plot_revenue,
                          cached_plot_shareholders, render_stock_chart)


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
        
        tab1, tab_k, tab2, tab3, tab4 = st.tabs(["技術分析", "日K線", "月營收趨勢", "大戶股權變化", "季報指標"])
        with tab1:
            render_stock_chart(stock_code, stock_name, key=f"tech_{stock_code}")
        with tab_k:
            render_kline(stock_code, stock_name)
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
        with tab4:
            render_financial_snapshot(stock_code)


def render_kline(stock_code: str, stock_name: str):
    """券商風日K線：K棒＋20/60EMA＋分價量＋成交量（共用技術分析的日線快取，不另打 FinMind）。"""
    c1, c2, c3 = st.columns([1, 1.4, 1])
    months = c1.radio("區間", [3, 6], format_func=lambda m: f"{m} 個月", horizontal=True,
                      key=f"kline_m_{stock_code}")
    panels = c2.multiselect("副圖", ["KD", "MACD"], default=["KD", "MACD"], key=f"kline_p_{stock_code}")
    updown = c3.toggle("成交量依漲跌上色", key=f"kline_vc_{stock_code}")
    try:
        with st.spinner("正在讀取日線（FinMind）..."):
            df = _cached_price_history(stock_code)
        fig = make_kline_figure(df, f"{stock_name} {stock_code}", months, vol_updown=updown,
                                panels=[p.lower() for p in panels])
    except Exception as e:                      # noqa: BLE001  AnalysisError／資料不足
        st.error(f"無法生成日K線圖：{e}")
        return
    st.plotly_chart(fig, width="stretch", key=f"kline_{stock_code}")
    st.caption("紅漲綠跌；藍線 20EMA、棕線 60EMA；灰色橫條為區間分價量（每根K棒的量平均分配到最高～最低價）；"
               "成交量單位：張；KD(9,3,3) 虛線為 20／80；MACD(12,26,9) 柱為 DIF−MACD，紅正綠負")


def _metric_delta(v, unit):
    return None if v is None else f"{v:+.2f}{unit}"


def render_financial_snapshot(stock_code: str):
    try:
        with st.spinner("正在讀取季報（FinMind）..."):
            snap = cached_financial_snapshot(stock_code)
    except Exception as e:                      # noqa: BLE001  FinMindError／資料缺漏
        st.error(f"無法取得季報資料：{e}")
        return
    m = snap["本季指標"]
    st.caption(f"最新季度 {snap['最新季度']}；QoQ 比 {snap['比較基準']['QoQ']}、YoY 比 {snap['比較基準']['YoY']}"
               "；資料來源：FinMind 綜合損益表（單季）、現金流量表")
    c = st.columns(5)
    c[0].metric("營收（億）", m["營收"]["本季(億)"], _metric_delta(m["營收"]["YoY(%)"], "% YoY"))
    c[1].metric("毛利率", f"{m['毛利率']['本季(%)']}%", _metric_delta(m["毛利率"]["YoY(百分點)"], " 百分點 YoY"))
    c[2].metric("營益率", f"{m['營益率']['本季(%)']}%", _metric_delta(m["營益率"]["YoY(百分點)"], " 百分點 YoY"))
    c[3].metric("EPS（元）", m["EPS"]["本季(元)"], _metric_delta(m["EPS"]["YoY(%)"], "% YoY"))
    c[4].metric("業外占稅前淨利", f"{m['業外占稅前淨利']['本季(%)']}%")

    for f in snap["flags"]:
        st.warning(f"⚠️ {f}")
    for n in snap.get("基期說明", []):
        st.caption(f"ℹ️ {n}")
    for n in snap.get("notes", []):
        st.caption(f"ℹ️ {n}")

    tr = pd.DataFrame(snap["近期季度"])
    if not tr.empty:
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_bar(x=tr["季度"], y=tr["營收(億)"], name="營收（億）", marker_color="#c9c6bd", secondary_y=False)
        for col, color in (("毛利率(%)", SERIES[0]), ("營益率(%)", SERIES[1]), ("淨利率(%)", SERIES[2])):
            fig.add_scatter(x=tr["季度"], y=tr[col], name=col, mode="lines+markers",
                            line=dict(color=color, width=2), secondary_y=True)
        fig.update_yaxes(title_text="營收（億）", secondary_y=False)
        fig.update_yaxes(title_text="比率（%）", secondary_y=True)
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified")
        legend_top(fig)
        st.plotly_chart(fig, width="stretch", key=f"fin_{stock_code}")
        st.dataframe(tr, hide_index=True)

    cash = snap.get("現金轉換")
    if cash and "現金轉換率(倍)" in cash:
        st.caption(f"現金轉換（{cash['區間']}）：營業現金流 {cash['營業現金流(億)']} 億 ÷ 稅後淨利 "
                   f"{cash['稅後淨利(億)']} 億 ＝ **{cash['現金轉換率(倍)']} 倍**")
    elif cash:
        st.caption(cash.get("說明", ""))
