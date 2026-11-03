"""我的選股103（本機計算）。"""
from dataclasses import asdict

import numpy as np
import pandas as pd
import streamlit as st

from local_screener import Screen103Params, ScreenerError
from scrape_utils import lkg_key
from viz import MARGIN_SCALE, heat_table
from views.common import (KDI_COLUMN_CONFIG, _batch_kd_analyze, _cached_screen_103, _show_stale,
                          _with_lkg, add_kdi_columns, cached_analyze_stock, render_chart_picker)


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

    # I 值來自個股分析；K、D 改用選股計算的日K/日D（與 Goodinfo 同算法）
    scraped_df = add_kdi_columns(scraped_df, analysis_cache, blank_label='I 值')
    scraped_df['K'] = pd.to_numeric(scraped_df['日K'], errors='coerce')
    scraped_df['D'] = pd.to_numeric(scraped_df['日D'], errors='coerce')

    display_columns = [
        '代碼', '名稱', 'K', 'D', 'I值', '市場', '股價日期',
        '成交', '漲跌價', '漲跌幅', '成交張數', '紅K棒幅', '季線乖離', '週K'
    ]
    st.dataframe(scraped_df[[c for c in display_columns if c in scraped_df.columns]],
                 column_config=KDI_COLUMN_CONFIG)

    if {'量比', '週K變化', '月季線差(%)'}.issubset(scraped_df.columns):
        _plot_103_margins(scraped_df, params)

    render_chart_picker([(str(r.代碼).strip(), str(r.名稱).strip()) for r in scraped_df.itertuples()],
                        key="103", histories=histories)
