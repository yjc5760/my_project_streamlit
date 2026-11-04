"""
台股日K線圖（仿券商 App 暗色主題）：K棒 + 20/60 EMA + 分價量(Volume Profile) + 成交量
＋選用副圖 KD(9,3,3)、MACD(12,26,9)。
紅漲綠跌；區間最高／最低標註；標題列顯示開高低收、漲跌幅、EMA、成交量。

輸入 DataFrame 沿用專案慣例：DatetimeIndex，欄位 open/high/low/close/volume（volume 單位：股），
即 finmind_client.price_history() / stock_analyzer.fetch_price_history() 的輸出。

用法：
    python kline_chart.py 2344 --name 華邦電            # 輸出 2344_kline.html
    python kline_chart.py 2344 --months 6 --vol-updown  # 成交量依漲跌上色
    python kline_chart.py 2344 --kd --macd               # 加 KD、MACD 副圖

Streamlit：見 views/single_stock.py 的「日K線」分頁。
"""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ---- 配色（台股：紅漲綠跌）----
BG = "#2b2b2b"
GRID = "#3a3a3a"
TXT = "#d0d0d0"
UP = "#e8343a"
DOWN = "#2fb34a"
EMA20_C = "#3b5bdb"
EMA60_C = "#c9a089"
VP_C = "rgba(140,140,140,0.35)"
VOL_C = "#2f8fe0"
K_C = "#f0b429"      # KD：K 琥珀
D_C = "#4dabf7"      #     D 淺藍
DIF_C = "#4dabf7"    # MACD：DIF 淺藍
DEA_C = "#f0b429"    #       訊號線 琥珀
REF_C = "#6a6a6a"    # 20/80、零軸參考線

WARMUP_DAYS = 200   # 多抓的日曆天數，讓 60EMA 在顯示區間開頭已收斂


def fetch_daily(stock_id: str, months: int = 3) -> pd.DataFrame:
    """透過專案共用的 finmind_client 抓日線（自帶 token、額度保護、錯誤分類）。volume 單位：股。"""
    import finmind_client

    today = date.today()
    return finmind_client.price_history(stock_id, today - timedelta(days=months * 31 + WARMUP_DAYS), today)


def volume_profile(df: pd.DataFrame, bins: int = 32) -> pd.DataFrame:
    """分價量：把每根K棒的量平均攤到它的 high~low 區間（一字線整根量放進所在價格格）。"""
    lo, hi = df["low"].min(), df["high"].max()
    if hi <= lo:
        hi = lo + 1e-6
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for h, l, v in df[["high", "low", "volume"]].itertuples(index=False):
        span = h - l
        if span > 0:
            overlap = np.clip(np.minimum(edges[1:], h) - np.maximum(edges[:-1], l), 0, None)
            vol += v * overlap / span
        else:
            vol[min(int(np.searchsorted(edges, h, side="right")) - 1, bins - 1)] += v
    return pd.DataFrame({"price": (edges[:-1] + edges[1:]) / 2, "volume": vol, "h": np.diff(edges)})


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """在完整日線上計算 EMA20/60、KD(9,3,3)、MACD(12,26,9)，讓顯示區間開頭的指標已暖機。"""
    from indicators import tw_kd

    out = df.copy()
    c = out["close"]
    out["ema20"] = c.ewm(span=20, adjust=False).mean()
    out["ema60"] = c.ewm(span=60, adjust=False).mean()
    out["k"], out["d"] = tw_kd(out["high"], out["low"], c)
    # 與 stock_analyzer._calculate_macd 相同：DIF = EMA12 - EMA26，訊號線 = DIF 的 EMA9，柱 = DIF - 訊號
    out["dif"] = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    out["dea"] = out["dif"].ewm(span=9, adjust=False).mean()
    out["osc"] = out["dif"] - out["dea"]
    return out


def _panel_label(fig: go.Figure, row: int, parts: list[tuple[str, str]]) -> None:
    """副圖左上角的數值標籤，例如「K 23.41  D 30.12」。"""
    yref = "y domain" if row == 1 else f"y{row} domain"
    text = "   ".join(f"<span style='color:{c}'>{t}</span>" for t, c in parts)
    fig.add_annotation(xref="paper", yref=yref, x=0.005, y=1.0, text=text, showarrow=False,
                       xanchor="left", yanchor="bottom", font=dict(size=11))


def make_kline_figure(df: pd.DataFrame, title: str = "", months: int = 3,
                      vol_updown: bool = False, panels: tuple[str, ...] | list[str] = (),
                      height: int | None = None) -> go.Figure:
    """
    df：open/high/low/close/volume(股)。指標用全部資料計算，只顯示最近 months 個月。
    panels：成交量下方要加的副圖，可含 "kd"、"macd"（依給定順序）。
    height：預設主圖＋量 640px，每多一個副圖加 150px。
    """
    panels = [p.lower() for p in panels]
    bad = set(panels) - {"kd", "macd"}
    if bad:
        raise ValueError(f"不支援的副圖：{bad}")

    df = df.sort_index().copy()
    df["volume"] = df["volume"] / 1000          # 股 -> 張
    df = add_indicators(df)
    cut = df.index[-1] - pd.DateOffset(months=months)
    v = df[df.index > cut]
    if len(v) < 2:
        raise ValueError("顯示區間資料不足")
    x = v.index.strftime("%m/%d")               # 類別軸，自動跳過休市日
    vp = volume_profile(v)

    last = v.iloc[-1]
    prev = df["close"].iloc[-2]
    chg = last.close - prev
    arrow, cc = ("▲", UP) if chg >= 0 else ("▼", DOWN)

    n_sub = len(panels)
    if n_sub == 0:
        heights = [0.75, 0.25]
    else:                                       # 主圖像素高度大致不變，副圖平分剩下的空間
        main = 0.58 - 0.06 * (n_sub - 1)
        heights = [main, 0.16] + [(1 - main - 0.16) / n_sub] * n_sub
    if height is None:
        height = 640 + 150 * n_sub
    fig = make_subplots(rows=2 + n_sub, cols=1, shared_xaxes=True, row_heights=heights,
                        vertical_spacing=0.035 if n_sub else 0.04)

    # 分價量：用 layer="below" 的矩形畫在K棒後面（覆疊軸的 trace 一定蓋在K棒上面，所以不用 Bar）。
    # x 用 paper 座標（0＝主圖左緣），最大量約佔 80% 寬度。
    vmax = vp.volume.max() or 1
    for p, vol, h in vp[["price", "volume", "h"]].itertuples(index=False):
        if vol <= 0:
            continue
        fig.add_shape(type="rect", xref="paper", yref="y", layer="below", line_width=0, fillcolor=VP_C,
                      x0=0, x1=0.8 * vol / vmax, y0=p - h * 0.4, y1=p + h * 0.4)

    fig.add_trace(go.Candlestick(x=x, open=v.open, high=v.high, low=v.low, close=v.close,
                                 increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
                                 decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
                                 name="日K", showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=v.ema20, line=dict(color=EMA20_C, width=1.4), name="20EMA",
                             hovertemplate="%{y:.2f}"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=v.ema60, line=dict(color=EMA60_C, width=1.4), name="60EMA",
                             hovertemplate="%{y:.2f}"), row=1, col=1)

    if vol_updown:
        vol_color = [UP if c >= o else DOWN for o, c in zip(v.open, v.close)]
    else:
        vol_color = VOL_C
    fig.add_trace(go.Bar(x=x, y=v.volume, marker_color=vol_color, name="量(張)", showlegend=False,
                         hovertemplate="%{y:,.0f} 張"), row=2, col=1)

    for i, panel in enumerate(panels):
        row = 3 + i
        if panel == "kd":
            fig.add_trace(go.Scatter(x=x, y=v.k, line=dict(color=K_C, width=1.3), name="K",
                                     hovertemplate="%{y:.2f}"), row=row, col=1)
            fig.add_trace(go.Scatter(x=x, y=v.d, line=dict(color=D_C, width=1.3), name="D",
                                     hovertemplate="%{y:.2f}"), row=row, col=1)
            for lv in (20, 80):
                fig.add_hline(y=lv, line=dict(color=REF_C, width=1, dash="dot"), row=row, col=1)
            fig.update_yaxes(range=[0, 100], tickvals=[20, 50, 80], row=row, col=1)
            _panel_label(fig, row, [("KD(9)", TXT), (f"K {last.k:.2f}", K_C), (f"D {last.d:.2f}", D_C)])
        else:
            fig.add_trace(go.Bar(x=x, y=v.osc, name="OSC", hovertemplate="%{y:.2f}",
                                 marker_color=[UP if o >= 0 else DOWN for o in v.osc]), row=row, col=1)
            fig.add_trace(go.Scatter(x=x, y=v.dif, line=dict(color=DIF_C, width=1.3), name="DIF",
                                     hovertemplate="%{y:.2f}"), row=row, col=1)
            fig.add_trace(go.Scatter(x=x, y=v.dea, line=dict(color=DEA_C, width=1.3), name="MACD",
                                     hovertemplate="%{y:.2f}"), row=row, col=1)
            _panel_label(fig, row, [("MACD(12,26,9)", TXT), (f"DIF {last.dif:.2f}", DIF_C),
                                    (f"MACD {last.dea:.2f}", DEA_C),
                                    (f"OSC {last.osc:.2f}", UP if last.osc >= 0 else DOWN)])

    # 區間最高／最低標註
    ih, il = v["high"].idxmax(), v["low"].idxmin()
    fig.add_annotation(x=ih.strftime("%m/%d"), y=v.high.max(), text=f"←{v.high.max():g}", xref="x", yref="y",
                       showarrow=False, xanchor="left", yanchor="bottom", font=dict(color=UP, size=12))
    fig.add_annotation(x=il.strftime("%m/%d"), y=v.low.min(), text=f"{v.low.min():g}→", xref="x", yref="y",
                       showarrow=False, xanchor="right", yanchor="top", font=dict(color=DOWN, size=12))

    head = (f"<b>{title}</b>  開{last.open:g} 高{last.high:g} 低{last.low:g} 收{last.close:g} "
            f"<span style='color:{cc}'>{arrow}{chg:+.2f} ({chg / prev * 100:+.2f}%)</span><br>"
            f"<span style='color:{EMA20_C}'>20EMA {last.ema20:.2f}</span>   "
            f"<span style='color:{EMA60_C}'>60EMA {last.ema60:.2f}</span>   "
            f"<span style='color:{TXT}'>量 {last.volume:,.0f} 張</span>")

    common = dict(gridcolor=GRID, zeroline=False)
    fig.update_xaxes(type="category", rangeslider_visible=False, nticks=6, **common)
    fig.update_yaxes(side="right", **common)
    fig.update_yaxes(showgrid=False, row=2, col=1)
    for i, panel in enumerate(panels):          # update_yaxes(**common) 會把 zeroline 關掉，MACD 再打開
        if panel == "macd":
            fig.update_yaxes(zeroline=True, zerolinecolor=REF_C, row=3 + i, col=1)
    fig.update_layout(
        title=dict(text=head, x=0.01, font=dict(size=14, color=TXT)),
        paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TXT, family="Noto Sans TC, Microsoft JhengHei, sans-serif"),
        margin=dict(l=10, r=55, t=70, b=30), height=height, hovermode="x unified",
        showlegend=False, bargap=0.15,
    )
    return fig


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="台股日K線圖（K棒＋20/60EMA＋分價量＋成交量）")
    ap.add_argument("stock_id")
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--name", default="")
    ap.add_argument("--token", default=None, help="FinMind token（預設讀 FINMIND_API_TOKEN）")
    ap.add_argument("--vol-updown", action="store_true", help="成交量依漲跌上紅綠色")
    ap.add_argument("--kd", action="store_true", help="加 KD 副圖")
    ap.add_argument("--macd", action="store_true", help="加 MACD 副圖")
    a = ap.parse_args()
    if a.token:
        os.environ["FINMIND_API_TOKEN"] = a.token
    data = fetch_daily(a.stock_id, a.months)
    panels = [p for p, on in (("kd", a.kd), ("macd", a.macd)) if on]
    f = make_kline_figure(data, f"{a.name} {a.stock_id}".strip(), a.months, a.vol_updown, panels)
    out = f"{a.stock_id}_kline.html"
    f.write_html(out, include_plotlyjs="cdn")
    print("saved", out)
