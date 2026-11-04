# HANDOFF — 台股日K線圖（仿券商 App：K棒＋20/60EMA＋分價量＋成交量）

日期：2026-11-05

## 目標
使用者給了一張華邦電 2344 的券商 App 截圖（暗色主題、日K、20EMA 藍、60EMA 淺棕、背景灰色分價量橫條、下方藍色成交量、區間最高 193.0／最低 132.5 標註），要求「畫出這樣的圖」。

## 已完成
- 寫好 `kline_chart.py`（Plotly），內容見文末，可直接複製使用。
  - `fetch_finmind(stock_id, months=3, token=None)`：抓 FinMind `TaiwanStockPrice`，多抓約 200 天給 60EMA 暖機，成交量換算成「張」。
  - `volume_profile(df, bins=32)`：分價量，每根K棒的量平均攤到 high~low 區間。
  - `make_kline_figure(df, title, months=3)`：紅漲綠跌K棒、EMA20/60（`ewm(span, adjust=False)`）、分價量用獨立的 `xaxis3` 疊在主圖下（最大量約佔 80% 寬）、成交量副圖、最高/最低標註、標題列顯示開高低收／漲跌幅／EMA／量。x 軸用類別軸（`%m/%d`）跳過休市日。
  - CLI：`python kline_chart.py 2344 --name 華邦電 [--months 6] [--token XXX]` → 輸出 `2344_kline.html`
  - Streamlit：`st.plotly_chart(make_kline_figure(fetch_finmind("2344"), "華邦電 2344"), use_container_width=True)`
- 用**模擬資料**輸出 `kline_preview.html` 給使用者看版面（雲端環境的 proxy 擋 api.finmindtrade.com，所以沒跑過真實資料）。

## 未驗證／待辦
- 尚未用真實 FinMind 資料跑過；需在使用者電腦（或已連結的 FinMind MCP）驗證欄位與畫面。
- 可選加強（已向使用者提議，尚未回覆）：成交量柱依漲跌上紅綠色（程式已算好 `vol_colors`，目前沒套用）、加 KD／MACD 副圖。
- 預定整合進使用者的 Streamlit 台股分析平台。

## kline_chart.py（完整程式）
```python
"""
台股日K線圖：K棒 + 20/60 EMA + 分價量(Volume Profile) + 成交量
風格仿券商 App 暗色主題（紅漲綠跌）。

用法：
    python kline_chart.py 2344            # 從 FinMind 抓資料，輸出 2344_kline.html
    python kline_chart.py 2344 --months 6

Streamlit 整合：
    from kline_chart import fetch_finmind, make_kline_figure
    df = fetch_finmind("2344")
    st.plotly_chart(make_kline_figure(df, "華邦電 2344"), use_container_width=True)
"""
from __future__ import annotations

import argparse
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


def fetch_finmind(stock_id: str, months: int = 3, token: str | None = None) -> pd.DataFrame:
    """抓 FinMind 日線。多抓 120 個交易日讓 60EMA 暖機。回傳 index=日期，欄位 open/high/low/close/volume(張)。"""
    import requests

    start = (date.today() - timedelta(days=months * 31 + 200)).isoformat()
    params = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start}
    if token:
        params["token"] = token
    r = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=20)
    r.raise_for_status()
    d = pd.DataFrame(r.json()["data"])
    if d.empty:
        raise ValueError(f"FinMind 無 {stock_id} 資料")
    d = d.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    d["date"] = pd.to_datetime(d["date"])
    d["volume"] = d["volume"] / 1000  # 股 -> 張
    return d.set_index("date")[["open", "high", "low", "close", "volume"]]


def volume_profile(df: pd.DataFrame, bins: int = 32) -> pd.DataFrame:
    """分價量：把每根K棒的量平均攤到它的 high~low 區間。"""
    lo, hi = df["low"].min(), df["high"].max()
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for h, l, v in df[["high", "low", "volume"]].itertuples(index=False):
        a = np.clip(np.minimum(edges[1:], h) - np.maximum(edges[:-1], l), 0, None)
        span = h - l
        vol += v * (a / span) if span > 0 else v * ((edges[:-1] <= h) & (edges[1:] >= h))
    return pd.DataFrame({"price": (edges[:-1] + edges[1:]) / 2, "volume": vol, "h": np.diff(edges)})


def make_kline_figure(df: pd.DataFrame, title: str = "", months: int = 3) -> go.Figure:
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema60"] = df["close"].ewm(span=60, adjust=False).mean()
    cut = df.index[-1] - pd.DateOffset(months=months)
    v = df[df.index > cut]
    x = v.index.strftime("%m/%d")  # 類別軸，自動跳過休市日
    vp = volume_profile(v)

    last, prev = v.iloc[-1], df["close"].iloc[-2]
    chg = last.close - prev
    arrow, cc = ("▲", UP) if chg >= 0 else ("▼", DOWN)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.04)

    # 分價量：獨立 x 軸疊在主圖底下，最大量佔約 80% 寬度
    fig.add_trace(go.Bar(y=vp.price, x=vp.volume, width=vp.h * 0.8, orientation="h",
                         marker_color=VP_C, xaxis="x3", yaxis="y", hoverinfo="skip",
                         showlegend=False))

    fig.add_trace(go.Candlestick(x=x, open=v.open, high=v.high, low=v.low, close=v.close,
                                 increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
                                 decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
                                 name="K", showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=v.ema20, line=dict(color=EMA20_C, width=1.4), name="20EMA"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=v.ema60, line=dict(color=EMA60_C, width=1.4), name="60EMA"), row=1, col=1)

    vol_colors = [UP if c >= o else DOWN for o, c in zip(v.open, v.close)]
    fig.add_trace(go.Bar(x=x, y=v.volume, marker_color=VOL_C, name="成交量", showlegend=False,
                         customdata=vol_colors), row=2, col=1)

    # 區間最高/最低標註
    ih, il = v["high"].idxmax(), v["low"].idxmin()
    fig.add_annotation(x=ih.strftime("%m/%d"), y=v.high.max(), text=f"←{v.high.max():g}",
                       showarrow=False, xanchor="left", yanchor="bottom", font=dict(color=UP, size=12))
    fig.add_annotation(x=il.strftime("%m/%d"), y=v.low.min(), text=f"{v.low.min():g}→",
                       showarrow=False, xanchor="right", yanchor="top", font=dict(color=DOWN, size=12))

    head = (f"<b>{title}</b>  開{last.open:g} 高{last.high:g} 低{last.low:g} 收{last.close:g} "
            f"<span style='color:{cc}'>{arrow}{chg:+.2f} ({chg / prev * 100:+.2f}%)</span><br>"
            f"<span style='color:{EMA20_C}'>20EMA {last.ema20:.1f}</span>   "
            f"<span style='color:{EMA60_C}'>60EMA {last.ema60:.1f}</span>   "
            f"<span style='color:{TXT}'>量 {last.volume:,.0f} 張</span>")

    fig.update_layout(
        title=dict(text=head, x=0.01, font=dict(size=14, color=TXT)),
        paper_bgcolor=BG, plot_bgcolor=BG, font=dict(color=TXT, family="Noto Sans TC, Microsoft JhengHei, sans-serif"),
        margin=dict(l=10, r=55, t=70, b=30), height=640, hovermode="x unified",
        showlegend=False, bargap=0.15,
        xaxis3=dict(overlaying="x", side="top", range=[0, vp.volume.max() * 1.25], visible=False),
    )
    common = dict(gridcolor=GRID, zeroline=False)
    fig.update_xaxes(type="category", rangeslider_visible=False, nticks=6, **common)
    fig.update_yaxes(side="right", **common)
    fig.update_yaxes(showgrid=False, row=2, col=1)
    return fig


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stock_id")
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--name", default="")
    ap.add_argument("--token", default=None)
    a = ap.parse_args()
    data = fetch_finmind(a.stock_id, a.months, a.token)
    f = make_kline_figure(data, f"{a.name} {a.stock_id}".strip(), a.months)
    out = f"{a.stock_id}_kline.html"
    f.write_html(out, include_plotlyjs="cdn")
    print("saved", out)
```

## 2026-11-05 進度（已完成上方待辦）
- `kline_chart.py` 已放進專案並改寫：
  - 抓資料改走共用的 `finmind_client.price_history`（token、額度保護、錯誤分類），輸入沿用專案慣例 volume＝股，圖內轉張。
  - 修正：分價量原本用覆疊軸 Bar，會**蓋在K棒上面**；改成 `layer="below"` 的矩形畫在K棒後方。
  - 新增 `vol_updown` 參數（成交量依漲跌上紅綠色），CLI 用 `--vol-updown`。
- **已用真實 FinMind 資料驗證**（2344，2026-08-05～11-04）：區間最高 193.0（8/12）、最低 132.5（11/4）、收 141.5 ▼4.5，與使用者截圖一致。
- Streamlit：`views/single_stock.py` 新增「日K線」分頁（3／6 個月切換、成交量上色開關），共用 `_cached_price_history` 快取，不額外打 FinMind。
- 測試：`tests/test_kline_chart.py`（4 項：分價量守恆、一字線、圖表結構／單位／標註、上色）。
- 已加 KD(9,3,3)／MACD(12,26,9) 副圖：`make_kline_figure(..., panels=["kd","macd"])`，CLI `--kd --macd`；
  KD 用共用的 `indicators.tw_kd`，MACD 算法同 `stock_analyzer._calculate_macd`；指標在完整日線上暖機後才裁切。
  Streamlit 分頁加「副圖」多選（預設 KD＋MACD）。2344 驗證：11/4 K 29.42、D 39.61、DIF −5.16、MACD −3.54、OSC −1.61。
- 配色改為**白底預設**（`theme="light"`），暗色保留為 `theme="dark"`／CLI `--dark`；配色集中在 `THEMES`。
- 測試增為 7 項。尚未在本機 `streamlit run` 目視確認分頁。
