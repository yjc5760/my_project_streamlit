"""側邊欄的篩選參數：寫進 st.session_state，各功能頁從那裡讀。"""
from dataclasses import asdict

import streamlit as st

from local_screener import Screen103Params
from quality_screener import QualityParams
from revenue_screener import RevenueParams


def render_filter_params() -> None:
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
        prev_years = st.slider("每月營收須高於過去幾年同月份", 1, 10, _r.lookback_years, key="prev_years",
                               help="預設 4 年：今年每個月都要超過 2022～2025 年同月份的最高值")
        prev_yoy = st.slider("每月年增率須大於（%）", 0, 50, int(_r.yoy_min), step=5, key="prev_yoy")
        prev_pc = st.checkbox("各公司以自己最新公告月份為當月", value=False, key="prev_pc",
                              help="不勾選＝全市場統一以最新有公告的月份為當月，還沒公告的公司不入選")
    with st.sidebar.expander("四條件選股條件", expanded=False):
        _q = QualityParams()
        q_pc = st.checkbox("月營收母體：各公司以自己最新公告月份為當月", value=True, key="q_pc",
                           help="月初很多公司還沒公告上個月營收，建議勾選，避免被排除（年數、年增率沿用「月營收選股條件」）")
        q_accel = st.slider("② 營收年增率逐月不下降：檢查最近幾個月", 2, 6, _q.accel_months, key="q_accel")
        q_hw = st.slider("① 大戶：觀察最近幾週", 3, 8, _q.holder_weeks, key="q_hw")
        q_hup = st.slider("① 大戶：其中至少幾週增加", 1, 8, _q.holder_min_up, key="q_hup",
                          help="超過觀察週數時，會自動以觀察週數計算")
        q_strict = st.checkbox("① 嚴格版：另要求 8 週前 < 4 週前 < 最新", value=_q.holder_strict, key="q_strict")
        q_ocf = st.slider("④ 營業現金流 TTM 連續上升幾季", 1, 4, _q.ocf_quarters, key="q_ocf")
        q_vol = st.number_input("成交張數下限（0＝不限）", value=0, step=500, key="q_vol")
    st.session_state['params_quality_rev'] = asdict(RevenueParams(
        yoy_min=float(prev_yoy), lookback_years=prev_years, per_company_month=q_pc,
    ))
    st.session_state['params_quality'] = asdict(QualityParams(
        accel_months=q_accel, holder_weeks=q_hw, holder_min_up=min(q_hup, q_hw), holder_strict=q_strict,
        ocf_quarters=q_ocf, min_volume_lots=float(q_vol),
    ))
    st.session_state['params_rev'] = asdict(RevenueParams(
        yoy_min=float(prev_yoy), lookback_years=prev_years, per_company_month=prev_pc,
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
