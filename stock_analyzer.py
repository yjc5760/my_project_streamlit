# stock_analyzer.py
"""
個股技術分析：日線 → 指標（KD、乖離、I/J/K 訊號、MACD、WMA）→ 7 層 Plotly 圖。

- 指標與畫圖分開：批次選股只需要 K、D、I 值時用 analyze_stock(..., with_chart=False)，
  不建圖、不佔記憶體；要看圖時再用同一份日線呼叫 build_chart()。
- 可傳入已抓好的日線（price_data），例如 103 細篩抓過的日線，避免重複呼叫 FinMind。
- 失敗時 analyze_stock 回傳 {'status': 'error', 'error_type': ..., 'message': ...}，
  error_type 由例外類別決定（不再比對錯誤訊息文字）：
  rate_limit / network / no_data / insufficient_data / api / unknown
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import twstock

import finmind_client
from indicators import tw_kd
from market_calendar import now_tw
from viz import (I_STYLES, NEUTRAL_COLOR, REF_LINE, SERIES, DOWN_COLOR, UP_COLOR)

import plotly.graph_objects as go
from plotly.subplots import make_subplots

MIN_BARS = 60          # 季線（60 日）需要的最少日線數


class AnalysisError(RuntimeError):
    """個股分析失敗；kind 對應 analyze_stock 回傳的 error_type。"""
    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


def fetch_price_history(stock_id: str, days: int = 300) -> pd.DataFrame:
    """抓近 days 天日線（欄位 open/high/low/close/volume，volume 單位：股）。"""
    today = now_tw().date()
    try:
        return finmind_client.price_history(stock_id, today - timedelta(days=days), today)
    except finmind_client.FinMindError as e:
        raise AnalysisError(e.kind, str(e)) from e


def _to_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """接受 open/high/... 或 Open/High/... 欄位，統一成 Open/High/Low/Close/Volume。"""
    cols = {c: c.capitalize() for c in df.columns if c.lower() in ("open", "high", "low", "close", "volume")}
    out = df.rename(columns=cols)[["Open", "High", "Low", "Close", "Volume"]].copy()
    out.index = pd.to_datetime(out.index)
    return out.sort_index().dropna(subset=["Close"])


def avg_volume_before_today(volume: pd.Series, n: int = 5, today: date | None = None) -> float | None:
    """
    「前 n 日均量」（單位同輸入）：只取今天以前的 n 根。
    盤中 FinMind 還沒有今天這根時，最後一根就是昨天，不能再去掉。
    """
    today = today or now_tw().date()
    v = volume.dropna()
    if len(v) and pd.Timestamp(v.index[-1]).date() >= today:
        v = v.iloc[:-1]
    v = v.iloc[-n:]
    return float(v.mean()) if len(v) else None


class TaiwanStockAnalyzer:
    def __init__(self, stock_id: str, days: int = 300, price_data: pd.DataFrame | None = None) -> None:
        """
        :param stock_id: 股票代碼
        :param days: 分析期間天數（日曆天）
        :param price_data: 已抓好的日線；給了就不再呼叫 FinMind
        """
        self.stock_id = stock_id
        self.days = days
        self.stock_name = self._get_stock_name()
        self.price_data: pd.DataFrame = _to_ohlcv(price_data) if price_data is not None else pd.DataFrame()
        self.indicators = {}

    def _get_stock_name(self) -> str:
        info = twstock.codes.get(self.stock_id)
        return info.name if info else self.stock_id

    def fetch_data(self) -> None:
        if self.price_data.empty:
            self.price_data = _to_ohlcv(fetch_price_history(self.stock_id, self.days))
        if len(self.price_data) < MIN_BARS:
            raise AnalysisError("insufficient_data",
                                f"{self.stock_id} 日線只有 {len(self.price_data)} 根，不足 {MIN_BARS} 根")

    # --- 指標計算函式 ---
    def calculate_weighted_moving_average(self, prices, period):
        weights = np.arange(1, period + 1, dtype=float)
        weight_sum = weights.sum()
        kernel = weights[::-1]  # 最新的權重最大
        result = np.full(len(prices), np.nan)
        # 使用 mode='valid'：只保留卷積完整覆蓋 period 筆的結果，避免邊界偏差
        conv = np.convolve(prices, kernel, mode='valid')
        result[period - 1:] = conv / weight_sum
        return result

    def _calculate_sma(self, data, period):
        return pd.Series(data).rolling(window=period).mean().values

    def _calculate_stochastic(self, high, low, close, k_period=9):
        """台灣遞迴式 KD（與 Goodinfo 一致），取代原本 SMA 平滑的慢速隨機指標。"""
        k, d = tw_kd(high, low, close, n=k_period)
        return k.to_numpy(), d.to_numpy()

    def _calculate_macd(self, prices, fast_period=12, slow_period=26, signal_period=9):
        prices_s = pd.Series(prices)
        ema_fast = prices_s.ewm(span=fast_period, adjust=False).mean()
        ema_slow = prices_s.ewm(span=slow_period, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=signal_period, adjust=False).mean()
        histogram = macd - signal
        return macd.values, signal.values, histogram.values

    def calculate_indicators(self) -> None:
        close = self.price_data['Close'].values
        high = self.price_data['High'].values
        low = self.price_data['Low'].values
        self.indicators['sma5'] = self._calculate_sma(close, 5)
        self.indicators['sma20'] = self._calculate_sma(close, 20)
        self.indicators['sma60'] = self._calculate_sma(close, 60)
        self.indicators['k'], self.indicators['d'] = self._calculate_stochastic(high, low, close)
        self.indicators['dev_5_20'] = (self.indicators['sma5'] - self.indicators['sma20']) / self.indicators['sma20'] * 100
        self.indicators['dev_20_60'] = (self.indicators['sma20'] - self.indicators['sma60']) / self.indicators['sma60'] * 100
        self.indicators['dev_5_60'] = (self.indicators['sma5'] - self.indicators['sma60']) / self.indicators['sma60'] * 100
        self.indicators['dev_1_20'] = (close - self.indicators['sma20']) / self.indicators['sma20'] * 100
        self.indicators['macd'], self.indicators['macd_signal'], self.indicators['macd_hist'] = self._calculate_macd(close)
        self.indicators['wma5'] = self.calculate_weighted_moving_average(close, 5)
        self.indicators['wma10'] = self.calculate_weighted_moving_average(close, 10)

    def calculate_signals(self) -> None:
        self.indicators['I_value'] = self._calculate_stair_signal()
        self.indicators['J_value'] = self._calculate_deviation_signal()
        dev_5_60 = self.indicators['dev_5_60']
        k = self.indicators['k']
        self.indicators['K_value'] = np.where(dev_5_60 >= 0, 3, -3)
        self.indicators['L_value'] = np.where(k >= 80, 100, np.where(k <= 20, 0, np.nan))

    def _calculate_stair_signal(self) -> np.ndarray:
        a = self.indicators['dev_5_20']
        b = self.indicators['dev_20_60']
        c = self.indicators['dev_5_60']
        # 向量化替代 Python 迴圈
        signals = np.where(
            (a >= c) & (c >= b), 1,
            np.where(
                (c >= a) & (a >= b), 2,
                np.where(
                    (c >= b) & (b >= a), 3,
                    np.where(
                        (b >= c) & (c >= a), -1,
                        np.where(
                            (b >= a) & (a >= c), -2,
                            -3
                        )
                    )
                )
            )
        )
        # 均線糾結時（三乖離差距皆小於 0.1%）輸出 0（中性），避免盤整期訊號跳動
        FLAT_THRESHOLD = 0.1
        is_flat = (np.abs(a - b) < FLAT_THRESHOLD) & (np.abs(b - c) < FLAT_THRESHOLD)
        signals = np.where(is_flat, 0, signals).astype(float)
        # 季線暖機期（任一乖離為 NaN）沒有意義，設為 NaN，不要落到預設的 -3
        signals[np.isnan(a) | np.isnan(b) | np.isnan(c)] = np.nan
        return signals

    def _calculate_deviation_signal(self) -> np.ndarray:
        dev = self.indicators['dev_1_20']
        return np.where(dev >= 5, 4, np.where(dev <= -5, -4, np.nan))

    def create_chart(self, visible_days: int = 126) -> go.Figure:
        """
        使用 Plotly 建立互動式技術分析圖。
        - 紅漲綠跌（K 線、成交量、MACD 柱）；線條系列用藍／琥珀／紫，不和漲跌混色
        - 略過週末與國定假日（資料中沒有的交易日）
        - 預設顯示最近 visible_days 根（約 6 個月），雙擊圖表可看全部
        """
        df = self.price_data.copy()
        for key, value in self.indicators.items():
            df[key] = value

        # 動態裁切：去除均線暖機期的 NaN，同時確保至少保留 20 筆資料
        df = df.dropna(subset=['sma60']).copy()
        if df.empty or len(df) < 20:
            raise AnalysisError("insufficient_data",
                                f"股票 {self.stock_id} 有效資料不足（dropna 後僅剩 {len(df)} 筆），無法繪圖。")

        blue, amber, violet = SERIES
        up = df['Close'] >= df['Close'].shift(1).fillna(df['Open'])

        titles = [
            '股價與均線（藍 週5／琥珀 月20／紫 季60）',
            '成交量（紅漲綠跌）',
            'KD（藍 K／琥珀 D；● 超買 ≥80、超賣 ≤20）',
            '乖離率 %（藍 週-月／琥珀 月-季／紫 週-季）',
            '訊號（柱：階梯 I 值，紅多綠空；● 乖離 J；線：多空 K）',
            'MACD（柱：紅正綠負；藍 MACD／琥珀 Signal）',
            '加權均線（藍 5WMA／琥珀 10WMA）',
        ]
        fig = make_subplots(
            rows=7, cols=1, shared_xaxes=True, vertical_spacing=0.035,
            row_heights=[0.34, 0.11, 0.11, 0.11, 0.11, 0.11, 0.11],
            subplot_titles=titles,
        )

        # 1. K線和均線（只有這一列進圖例）
        fig.add_trace(go.Candlestick(
            x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
            name='K線', increasing=dict(line=dict(color=UP_COLOR), fillcolor=UP_COLOR),
            decreasing=dict(line=dict(color=DOWN_COLOR), fillcolor=DOWN_COLOR),
        ), row=1, col=1)
        for col_, name, color in [('sma5', '週線(5)', blue), ('sma20', '月線(20)', amber),
                                  ('sma60', '季線(60)', violet)]:
            fig.add_trace(go.Scatter(x=df.index, y=df[col_], mode='lines', name=name,
                                     line=dict(color=color, width=1.5)), row=1, col=1)

        # 2. 成交量（依當日漲跌上色，單位：張）
        fig.add_trace(go.Bar(x=df.index, y=df['Volume'] / 1000, name='成交量(張)', showlegend=False,
                             marker_color=np.where(up, UP_COLOR, DOWN_COLOR),
                             hovertemplate='%{y:,.0f} 張<extra>成交量</extra>'), row=2, col=1)

        # 3. KD
        fig.add_trace(go.Scatter(x=df.index, y=df['k'], mode='lines', name='K', showlegend=False,
                                 line=dict(color=blue, width=1.5)), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['d'], mode='lines', name='D', showlegend=False,
                                 line=dict(color=amber, width=1.5)), row=3, col=1)
        kd_sig = df['L_value']
        fig.add_trace(go.Scatter(
            x=df.index, y=kd_sig, mode='markers', name='KD訊號', showlegend=False,
            marker=dict(size=7, color=np.where(kd_sig >= 80, UP_COLOR, DOWN_COLOR)),
            hovertemplate='%{y:.0f}<extra>KD 超買/超賣</extra>'), row=3, col=1)
        for lvl in (20, 80):
            fig.add_hline(y=lvl, row=3, col=1, **REF_LINE)

        # 4. 乖離率
        for col_, name, color in [('dev_5_20', '週-月', blue), ('dev_20_60', '月-季', amber),
                                  ('dev_5_60', '週-季', violet)]:
            fig.add_trace(go.Scatter(x=df.index, y=df[col_], mode='lines', name=name, showlegend=False,
                                     line=dict(color=color, width=1.5)), row=4, col=1)
        fig.add_hline(y=0, row=4, col=1, **REF_LINE)

        # 5. 訊號：I 值柱依 -3～+3 上色
        i_vals = pd.Series(df['I_value']).round()
        i_colors = [I_STYLES.get(int(v), (None, NEUTRAL_COLOR))[1] if pd.notna(v) else NEUTRAL_COLOR
                    for v in i_vals]
        fig.add_trace(go.Bar(x=df.index, y=df['I_value'], name='階梯訊號 I', showlegend=False,
                             marker_color=i_colors,
                             hovertemplate='I = %{y:.0f}<extra></extra>'), row=5, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['J_value'], mode='markers', name='乖離訊號 J',
                                 showlegend=False, marker=dict(color=violet, size=7)), row=5, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['K_value'], mode='lines', name='多空訊號 K',
                                 showlegend=False, line=dict(color=blue, width=1.5)), row=5, col=1)

        # 6. MACD
        fig.add_trace(go.Bar(x=df.index, y=df['macd_hist'], name='Histogram', showlegend=False,
                             marker_color=np.where(df['macd_hist'] >= 0, UP_COLOR, DOWN_COLOR)),
                      row=6, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['macd'], mode='lines', name='MACD', showlegend=False,
                                 line=dict(color=blue, width=1.5)), row=6, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['macd_signal'], mode='lines', name='Signal',
                                 showlegend=False, line=dict(color=amber, width=1.5)), row=6, col=1)

        # 7. WMA
        fig.add_trace(go.Scatter(x=df.index, y=df['wma5'], mode='lines', name='5WMA', showlegend=False,
                                 line=dict(color=blue, width=1.5)), row=7, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['wma10'], mode='lines', name='10WMA', showlegend=False,
                                 line=dict(color=amber, width=1.5)), row=7, col=1)

        # 略過週末與國定假日：工作日中沒有交易資料的日期一律隱藏
        all_bdays = pd.bdate_range(df.index.min(), df.index.max())
        holidays = all_bdays.difference(df.index)
        fig.update_xaxes(
            rangebreaks=[dict(bounds=["sat", "mon"]),
                         dict(values=holidays.strftime('%Y-%m-%d').tolist())],
            tickformat='%Y-%m-%d',
        )

        # 預設只看最近 visible_days 根；價格與成交量的 y 軸依可見範圍設定
        win = df.iloc[-min(visible_days, len(df)):]
        x0 = win.index[0] - pd.Timedelta(days=1)
        x1 = df.index[-1] + pd.Timedelta(days=1)
        fig.update_xaxes(range=[x0, x1])
        lo = min(win['Low'].min(), win[['sma5', 'sma20', 'sma60']].min().min())
        hi = max(win['High'].max(), win[['sma5', 'sma20', 'sma60']].max().max())
        pad = (hi - lo) * 0.05
        fig.update_yaxes(range=[lo - pad, hi + pad], row=1, col=1)
        fig.update_yaxes(range=[0, (win['Volume'].max() / 1000) * 1.1], row=2, col=1)
        fig.update_yaxes(range=[0, 100], row=3, col=1)

        fig.update_layout(
            title=f'{self.stock_name} ({self.stock_id}) 技術分析圖',
            height=1300,
            xaxis_rangeslider_visible=False,
            hovermode='x unified',
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.015, xanchor="left", x=0),
            margin=dict(t=110),
        )
        fig.update_annotations(font_size=12)    # 子圖標題
        return fig


def _last_valid(arr) -> float | None:
    """取序列最後一個非 NaN 的浮點值；全為 NaN 或空陣列時回傳 None。"""
    arr = np.asarray(arr, dtype=float)
    valid = arr[~np.isnan(arr)]
    return float(valid[-1]) if len(valid) > 0 else None


def analyze_stock(stock_id: str, days: int = 300, with_chart: bool = True,
                  price_data: pd.DataFrame | None = None) -> dict:
    """
    分析個股。成功：{'status': 'success', 'indicators': {k, d, i_value, avg_vol_5}, 'price_data': 日線,
    'chart_figure': Figure（with_chart=True 時才有）}。
    失敗：{'status': 'error', 'error_type': ..., 'message': ...}。
    avg_vol_5 為「今天以前」的 5 日均量（股）。
    """
    try:
        analyzer = TaiwanStockAnalyzer(stock_id, days, price_data)
        analyzer.fetch_data()
        analyzer.calculate_indicators()
        analyzer.calculate_signals()
        result = {
            'status': 'success',
            'price_data': analyzer.price_data,
            'indicators': {
                # 取最後一個非 NaN 值，避免暖機期 NaN 被誤判為有效數值
                'k': _last_valid(analyzer.indicators.get('k', [])),
                'd': _last_valid(analyzer.indicators.get('d', [])),
                'i_value': _last_valid(analyzer.indicators.get('I_value', [])),
                'avg_vol_5': avg_volume_before_today(analyzer.price_data['Volume']),
            },
        }
        if with_chart:
            result['chart_figure'] = analyzer.create_chart()
        return result
    except AnalysisError as e:
        error_type, message = e.kind, str(e)
    except finmind_client.FinMindError as e:
        error_type, message = e.kind, str(e)
    except Exception as e:                      # noqa: BLE001
        error_type, message = 'unknown', f"{type(e).__name__}: {e}"
    print(f"[analyze_stock] {stock_id} 失敗（{error_type}）：{message}")
    return {'status': 'error', 'error_type': error_type,
            'message': f"分析過程發生錯誤 ({stock_id}): {message}"}


def build_chart(stock_id: str, price_data: pd.DataFrame | None = None, days: int = 300) -> go.Figure:
    """只要圖：用給定日線（或重新抓）建 7 層技術分析圖。失敗時 raise AnalysisError。"""
    analyzer = TaiwanStockAnalyzer(stock_id, days, price_data)
    analyzer.fetch_data()
    analyzer.calculate_indicators()
    analyzer.calculate_signals()
    return analyzer.create_chart()
