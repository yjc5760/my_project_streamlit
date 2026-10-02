# viz.py
"""
共用圖表設定：顏色語意、I 值分類、泡泡大小、散佈圖與小倍數圖。

顏色規則（全 app 一致）：
  * 紅／綠只代表「漲／跌」「多／空」（台股慣例：紅漲綠跌）。
  * 線條系列（均線、KD、MACD…）一律用 SERIES 三色（藍、琥珀、紫），不用紅綠，避免和漲跌混淆。
  * I 值（階梯訊號 -3～+3）是有方向的序數：正值紅色系、負值綠色系、0 灰色，越深越強。
  * 泡泡大小一律代表「量能」（量比／成交量／均量）；y 軸已經是量的圖不用泡泡。
色票已用 dataviz 驗證工具檢查淺色與深色背景。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

UP_COLOR = "#e04f4e"        # 漲／多
DOWN_COLOR = "#1f9a5e"      # 跌／空
NEUTRAL_COLOR = "#a3a29c"   # 持平／未分類
SERIES = ["#2a78d6", "#c98500", "#7a6ad8"]   # 線條系列：藍、琥珀、紫
REF_LINE = dict(line_dash="dot", line_color="#898781", opacity=0.7)
MARKER_RING = dict(width=1.5, color="rgba(252,252,251,0.85)")

# I 值：(標籤, 顏色)；正值紅系、負值綠系，由淺到深代表由弱到強
I_STYLES = {
    3: ("+3 多頭強", "#b3261f"),
    2: ("+2 多頭", "#e04f4e"),
    1: ("+1 多頭弱", "#ee8a89"),
    0: ("0 均線糾結", NEUTRAL_COLOR),
    -1: ("-1 空頭弱", "#6cc59a"),
    -2: ("-2 空頭", "#1f9a5e"),
    -3: ("-3 空頭強", "#0c6b3a"),
}
I_ORDER = [3, 2, 1, 0, -1, -2, -3]
UNKNOWN_I = ("I 值未知", "#cfcec8")
LABEL_LIMIT = 12            # 點數超過此值就不直接標名稱，只在滑鼠移上去時顯示

# 月營收熱度表：單一紅色系（成長越多越深）
REVENUE_SCALE = [[0.0, "#fde3e2"], [0.35, "#f4a6a5"], [0.7, "#e04f4e"], [1.0, "#8f1d17"]]
# 選股103 安全邊際：單一藍色系（越深越安全）
MARGIN_SCALE = [[0.0, "#e8f0fb"], [0.4, "#9ec5f4"], [0.75, "#3987e5"], [1.0, "#184f95"]]


def i_value(v):
    """把 I 值（可能是 '1'、1.0、'N/A'）轉成 int；無效時回傳 None。"""
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else int(round(f))


def i_style(v):
    return I_STYLES.get(i_value(v), UNKNOWN_I)


def bubble_sizes(values, lo: float = 9, hi: float = 34) -> pd.Series:
    """泡泡面積與數值成正比（直徑取平方根），缺值給最小尺寸。"""
    s = pd.to_numeric(pd.Series(values), errors="coerce").clip(lower=0)
    if s.isna().all() or s.max() == 0:
        return pd.Series(14.0, index=s.index)
    r = np.sqrt(s.fillna(0)) / math.sqrt(s.max())
    return lo + (hi - lo) * r


def add_i_scatter(fig, df: pd.DataFrame, x: str, y: str, name_col: str, *,
                  x_label: str, y_label: str, size_col: str | None = None, size_label: str = "",
                  x_fmt: str = ".1f", y_fmt: str = ".2f", y_suffix: str = "",
                  row=None, col=None, showlegend: bool = True, label_limit: int = LABEL_LIMIT):
    """依 I 值分組畫散佈點（一組一個 trace，圖例順序 +3 → -3）。df 需有 '_I' 欄。"""
    ivals = df["_I"].map(i_value)
    sizes = bubble_sizes(df[size_col]) if size_col else pd.Series(12.0, index=df.index)
    use_text = len(df) <= label_limit
    for iv in I_ORDER + [None]:
        mask = ivals.isna() if iv is None else (ivals == iv)
        sub = df[mask]
        if sub.empty:
            continue
        label, color = I_STYLES.get(iv, UNKNOWN_I)
        hover = (f"<b>%{{text}}</b><br>{x_label}：%{{x:{x_fmt}}}<br>"
                 f"{y_label}：%{{y:{y_fmt}}}{y_suffix}")
        custom = None
        if size_col:
            custom = pd.to_numeric(sub[size_col], errors="coerce").to_numpy()
            hover += f"<br>{size_label}：%{{customdata:,.2f}}"
        hover += f"<br>I 值：{label}<extra></extra>"
        trace = go.Scatter(
            x=sub[x], y=sub[y], text=sub[name_col], customdata=custom,
            mode="markers+text" if use_text else "markers",
            textposition="top center", textfont=dict(size=10),
            name=label, legendgroup=label, showlegend=showlegend,
            marker=dict(color=color, size=sizes[mask].tolist(), opacity=0.85, line=MARKER_RING),
            hovertemplate=hover,
        )
        if row is None:
            fig.add_trace(trace)
        else:
            fig.add_trace(trace, row=row, col=col)


def i_small_multiples(df: pd.DataFrame, x: str, y: str, name_col: str, *, x_label: str,
                      y_label: str, title: str, size_col: str | None = None, size_label: str = "",
                      y_fmt: str = ".2f", y_suffix: str = "", x_range=(0, 100)):
    """
    依 I 值拆成小倍數圖：只畫有股票的分類，依 +3 → -3 排列；所有小圖共用相同座標範圍。
    回傳 None 表示沒有資料。
    """
    ivals = df["_I"].map(i_value)
    present = [iv for iv in I_ORDER if (ivals == iv).any()]
    if ivals.isna().any():
        present.append(None)
    if not present:
        return None
    n = len(present)
    cols = min(4, n)
    rows = math.ceil(n / cols)
    titles = [f"{I_STYLES.get(iv, UNKNOWN_I)[0]}（{int((ivals.isna() if iv is None else ivals == iv).sum())} 檔）"
              for iv in present]
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles,
                        horizontal_spacing=0.05, vertical_spacing=0.16 if rows > 1 else 0.1)
    yv = pd.to_numeric(df[y], errors="coerce")
    pad = max((yv.max() - yv.min()) * 0.18, 1)
    y_range = [min(yv.min(), 0) - pad, yv.max() + pad]
    for k, iv in enumerate(present):
        r, c = k // cols + 1, k % cols + 1
        sub = df[ivals.isna()] if iv is None else df[ivals == iv]
        add_i_scatter(fig, sub, x, y, name_col, x_label=x_label, y_label=y_label,
                      size_col=size_col, size_label=size_label, y_fmt=y_fmt, y_suffix=y_suffix,
                      row=r, col=c, showlegend=False, label_limit=8)
        fig.add_vline(x=50, row=r, col=c, **REF_LINE)
        fig.add_hline(y=0, row=r, col=c, **REF_LINE)
    fig.update_xaxes(range=list(x_range))
    fig.update_yaxes(range=y_range)
    for c in range(1, cols + 1):
        fig.update_xaxes(title_text=x_label, row=rows, col=c)
    for r in range(1, rows + 1):
        fig.update_yaxes(title_text=y_label, row=r, col=1)
    fig.update_layout(title=title, height=320 * rows + 90, showlegend=False,
                      margin=dict(t=90))
    return fig


def legend_top(fig):
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="left", x=0, title_text=""))
    return fig


def heat_table(z: pd.DataFrame, text: pd.DataFrame, *, colorscale, zmin: float, zmax: float,
               title: str, colorbar_title: str, hover: pd.DataFrame | None = None,
               row_height: int = 30):
    """把 DataFrame 畫成熱度表（列 = 股票、欄 = 指標），格子內直接顯示數值。"""
    fig = go.Figure(go.Heatmap(
        z=z.to_numpy(dtype=float), x=list(z.columns), y=list(z.index),
        text=text.to_numpy(), texttemplate="%{text}", textfont=dict(size=12),
        customdata=(hover if hover is not None else text).to_numpy(),
        hovertemplate="<b>%{y}</b><br>%{x}：%{customdata}<extra></extra>",
        colorscale=colorscale, zmin=zmin, zmax=zmax, xgap=2, ygap=2,
        colorbar=dict(title=dict(text=colorbar_title, side="top"), thickness=12, len=0.8),
    ))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=12))
    fig.update_xaxes(side="top", tickfont=dict(size=12))
    fig.update_layout(title=title, height=max(260, row_height * len(z) + 140),
                      margin=dict(t=110, l=10, r=10, b=10))
    return fig
