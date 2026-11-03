"""四條件選股（月營收 → 營收加速 → 均線 → 大戶 → 營業現金流）。"""
from dataclasses import asdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from local_screener import ScreenerError
from quality_screener import QualityParams
from revenue_screener import RevenueParams
from scrape_utils import ScrapeError
from viz import SERIES
from views.common import cached_screen_quality, render_chart_picker


def _fmt_seq(seq, unit="") -> str:
    return " → ".join(f"{v:g}{unit}" for v in seq) if seq else ""


def _quality_row(d: dict) -> dict:
    """quality_screener 的逐檔結果（巢狀 dict）→ 表格一列。"""
    ma, hd, oc = d.get("③均線") or {}, d.get("①大戶") or {}, d.get("④營現流") or {}
    ttm = list((oc.get("TTM營業現金流(億)") or {}).values())
    return {
        "代碼": d["代碼"], "名稱": d["名稱"], "營收月份": d.get("營收月份"),
        "營收YoY(舊→新,%)": _fmt_seq(d.get("②營收YoY(舊→新)")),
        "收盤": ma.get("收盤"), "MA5": ma.get("MA5"), "MA10": ma.get("MA10"), "MA20": ma.get("MA20"),
        "大戶持股(%)": hd.get("最新(%)"), "大戶4週增減": hd.get("4週增減"), "大戶8週增減": hd.get("8週增減"),
        "大戶週增減(舊→新)": _fmt_seq(hd.get("週增減(舊→新)")),
        "TTM營現流(億,舊→新)": _fmt_seq(ttm),
        "未通過": d.get("未通過") or "",
    }


def display_quality_results():
    st.header("🧪 四條件選股（月營收 → 營收加速 → 均線 → 大戶 → 營業現金流）")
    rev_params = st.session_state.get('params_quality_rev', asdict(RevenueParams(per_company_month=True)))
    q_params = st.session_state.get('params_quality', asdict(QualityParams()))
    try:
        with st.spinner("正在跑月營收母體，再逐關抓日線、大戶週資料與現金流量表（約 1～3 分鐘）..."):
            res = cached_screen_quality(rev_params, q_params)
    except (ScreenerError, ScrapeError) as e:
        st.error(f"❌ 四條件選股失敗：{e}")
        st.caption("失敗結果不會被快取，稍後重新按一次按鈕即可重試。")
        return

    st.info(f"""
**條件**（依成本由低到高逐關篩選，沒過前一關就不抓後面的資料）
- 母體：月營收選股（今年每月營收高於前 {rev_params['lookback_years']} 年同月、年增率 > {rev_params['yoy_min']}%；{'各公司最新公告月份' if rev_params.get('per_company_month') else '全市場最新公告月份'}）
- ② 營收 YoY 不減速：最近 {q_params['accel_months']} 個月年增率皆 > 0 且逐月不下降
- ③ 均線：MA5 > MA20 且 MA10 > MA20
- ① 大戶（>400 張）持續增加：近 {q_params['holder_weeks']} 週至少 {q_params['holder_min_up']} 週增加、累計為正、最新一週不減少{'；且 8 週前 < 4 週前 < 最新' if q_params['holder_strict'] else ''}
- ④ 營業現金流：近四季合計（TTM）連續 {q_params['ocf_quarters']} 季上升且 > 0
""")
    for note in res.get("revenue_notes", []):
        st.caption(f"ℹ️ {note}")

    funnel = res["funnel"]
    fig = go.Figure(go.Funnel(y=list(funnel.keys()), x=list(funnel.values()), textinfo="value",
                              marker={"color": SERIES[0]}, connector={"line": {"color": "#d0cec8"}}))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch", key="quality_funnel")

    if res["errors"]:
        with st.expander(f"⚠️ {len(res['errors'])} 檔抓資料失敗（列為未通過）"):
            st.dataframe(pd.DataFrame(res["errors"]), hide_index=True)

    if res["matches"]:
        st.success(f"共 {len(res['matches'])} 檔通過全部條件")
        df = pd.DataFrame([_quality_row(d) for d in res["matches"]]).drop(columns=["未通過"])
        st.dataframe(df, hide_index=True)
        render_chart_picker([(r["代碼"], r["名稱"]) for r in res["matches"]], key="quality")
    else:
        st.warning("目前沒有通過全部條件的股票。")

    rejected = [d for d in res["detail"] if d.get("未通過") and not d["未通過"].startswith("②")]
    if rejected:
        with st.expander(f"過了「營收加速」、但在後面關卡被刷掉的 {len(rejected)} 檔（近乎過關，可留意）"):
            rdf = pd.DataFrame([_quality_row(d) for d in rejected])
            st.dataframe(rdf[["代碼", "名稱", "未通過"] + [c for c in rdf.columns if c not in ("代碼", "名稱", "未通過")]],
                         hide_index=True)
