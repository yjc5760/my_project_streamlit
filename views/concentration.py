"""1日籌碼集中度選股。"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import services
from viz import DOWN_COLOR, REF_LINE, UP_COLOR, add_i_scatter, i_small_multiples, legend_top
from views.common import (KDI_COLUMN_CONFIG, _batch_kd_analyze, _drop_no_data, add_kdi_columns,
                          cached_fetch_concentration_data, render_chart_picker)


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
    viz_df['_K'] = viz_df['K']
    viz_df['_D'] = viz_df['D']
    viz_df['_I'] = viz_df['I值']

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
                filtered_stocks = add_kdi_columns(filtered_stocks, concentration_cache, name_col='股票名稱')

                st.info(
                    f"**篩選條件：**\n"
                    f"1. 5日集中度 > 10日集中度\n"
                    f"2. 10日集中度 > 20日集中度\n"
                    f"3. 5日與10日集中度皆 > 0\n"
                    f"4. 10日均量 > {_min_vol_conc:,} 張（可在側邊欄調整）"
                )

                display_columns = [
                    '編號', '代碼', '股票名稱', 'K', 'D', 'I值', '1日集中度', '5日集中度',
                    '10日集中度', '20日集中度', '60日集中度', '120日集中度', '10日均量'
                ]
                final_display_columns = [col for col in display_columns if col in filtered_stocks.columns]
                st.dataframe(filtered_stocks[final_display_columns], column_config=KDI_COLUMN_CONFIG)

                # ── 整合遠端視覺化服務：直接在本地產生統計卡片與圖表 ──
                display_concentration_visualization(filtered_stocks)

                render_chart_picker(
                    [(str(r.代碼), str(r.股票名稱)) for r in filtered_stocks.itertuples()], key="conc")
            else:
                st.warning("沒有找到或篩選出符合條件的股票。")
