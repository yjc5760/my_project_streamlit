"""月營收選股（本機計算）。"""
import re
from dataclasses import asdict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from local_screener import ScreenerError
from revenue_screener import RevenueParams
from scrape_utils import lkg_key
from viz import DOWN_COLOR, REVENUE_SCALE, UP_COLOR, heat_table, i_small_multiples
from views.common import (KDI_COLUMN_CONFIG, _batch_kd_analyze, _cached_screen_revenue,
                          _drop_no_data, _show_stale, _with_lkg, add_kdi_columns, render_chart_picker)


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
    viz_df['_K值'] = viz_df['K']
    viz_df['_I值'] = viz_df['I值']

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
        skip_cols = {'代碼', '名稱', 'K', 'D', 'I值', '_K值', '_I值'}
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
                elif '最弱月超越前高(%)' in h.columns:
                    h = h.sort_values('最弱月超越前高(%)', ascending=False)
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
                st.caption("依「最弱月超越前高」由大到小排序（最右欄為當月，往左依序是前幾個月）。顏色越深代表成長越多；看整列是否一路深色，可判斷成長是否穩定。")

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
    st.caption(f"營收資料：{months_txt}　全市場 {res['universe_size']} 家 → "
               f"每月年增率皆 > {params.get('yoy_min', 0)}% 共 {res['n_candidates']} 家")
    for note in res.get('notes', []):
        st.info(f"ℹ️ {note}")
    if res['errors']:
        with st.expander(f"⚠️ {len(res['errors'])} 檔抓不到歷年月營收，未納入歷年同期比較"):
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
        scraped_df = add_kdi_columns(scraped_df, revenue_cache)

        st.info(f"""
**篩選條件（本機計算）：今年每個月都創同期新高**（當月 = {'各公司最新公告月份' if params.get('per_company_month') else '全市場最新公告月份'}）
1.  今年 1 月～當月，**每個月**的單月營收都高於過去 {params['lookback_years']} 年同月份的最高值
2.  今年 1 月～當月，**每個月**的單月營收年增率都 > {params['yoy_min']}%

「最弱月超越前高(%)」＝今年各月中，超過歷年同月最高值幅度最小的那個月，數字越大代表領先越穩。
""")

        # K、D、I值 排在「名稱」後面
        kdi = ['K', 'D', 'I值']
        rest = [c for c in scraped_df.columns if c not in kdi]
        name_idx = rest.index('名稱')
        scraped_df = scraped_df[rest[:name_idx + 1] + kdi + rest[name_idx + 1:]]

        st.dataframe(scraped_df, column_config=KDI_COLUMN_CONFIG)

        # ── 整合遠端視覺化服務：直接在本地產生統計卡片與圖表 ──
        display_monthly_revenue_visualization(scraped_df)

        render_chart_picker([(str(r.代碼).strip(), str(r.名稱).strip()) for r in scraped_df.itertuples()
                             if str(r.代碼).strip() not in ('', 'nan')], key="rev")
    else:
        st.warning("目前沒有符合全部條件的股票。")
