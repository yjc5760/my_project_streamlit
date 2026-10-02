import os
import time
import pandas as pd
import numpy as np
import requests
import twstock
from datetime import date, timedelta

from indicators import tw_kd
from viz import (I_STYLES, NEUTRAL_COLOR, REF_LINE, SERIES, DOWN_COLOR, UP_COLOR)

# --- 新增 Plotly 相關導入 ---
import plotly.graph_objects as go
from plotly.subplots import make_subplots



def _get_with_retry(
    url: str,
    params: dict,
    headers: dict,
    timeout: int = 20,
    max_retries: int = 3
) -> requests.Response:
    """帶指數退避的 GET 請求；HTTP 429 Rate Limit 時自動重試。"""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429 and attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"⚠️  FinMind Rate Limit (429)，{wait}s 後重試 ({attempt+1}/{max_retries})...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(1)
    raise requests.exceptions.RequestException(
        f"連線失敗，已重試 {max_retries} 次: {last_exc}"
    )
class TaiwanStockAnalyzer:
    def __init__(self, stock_id: str, days: int = 300) -> None:
        """
        初始化股票分析器
        :param stock_id: 股票代碼
        :param days: 分析期間天數
        """
        self.stock_id = stock_id
        self.days = days
        self.start_date = date.today() - timedelta(days=days)
        self.stock_name = self._get_stock_name()
        self.price_data: pd.DataFrame = pd.DataFrame()
        self.indicators = {}
        self.finmind_api_token = os.getenv('FINMIND_API_TOKEN')

    def _get_stock_name(self) -> str:
        """利用 twstock 取得股票名稱"""
        try:
            info = twstock.codes[self.stock_id]
            return info.name
        except KeyError:
            print(f"警告: 股票代碼 {self.stock_id} 在 twstock.codes 中未找到。將使用代碼作為名稱。")
            return self.stock_id

    def fetch_data(self) -> None:
        """從 FinMind API 抓取股票資料 (此函式邏輯不變)"""
        print(f"正在從 FinMind API 抓取股票 {self.stock_id} 的資料...")
        
        finmind_url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "TaiwanStockPrice",
            "data_id": self.stock_id,
            "start_date": self.start_date.strftime('%Y-%m-%d'),
            "end_date": date.today().strftime('%Y-%m-%d'),
        }
        headers = {}
        if self.finmind_api_token:
            headers["Authorization"] = f"Bearer {self.finmind_api_token}"
            print("使用 FinMind API Token 進行驗證。")
        else:
            print("警告: 未設定 FINMIND_API_TOKEN 環境變數，將嘗試匿名存取 FinMind API。")

        try:
            response = _get_with_retry(finmind_url, params=params, headers=headers, timeout=20)
            raw_data = response.json()
            
            if raw_data.get("status") != 200:
                error_message_from_api = raw_data.get('error_message', 'FinMind API 回傳錯誤')
                raise ValueError(f"FinMind API 錯誤: {error_message_from_api}")

            data_list = raw_data.get('data')
            if not data_list:
                raise ValueError(f"FinMind API 未回傳股票 {self.stock_id} 的資料。")

            data = pd.DataFrame(data_list)
            data.rename(columns={
                'date': 'Date', 'open': 'Open', 'max': 'High',
                'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume'
            }, inplace=True)
            
            data['Date'] = pd.to_datetime(data['Date'])
            data.set_index('Date', inplace=True)
            data = data[['Open', 'High', 'Low', 'Close', 'Volume']]
            
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                data[col] = pd.to_numeric(data[col], errors='coerce')
            
            self.price_data = data.dropna(subset=['Close'])
            
            if self.price_data.empty:
                raise ValueError("資料處理後為空。")
            
            print(f"成功從 FinMind API 抓取並處理 {self.stock_id} 的資料。共 {len(self.price_data)} 筆。")

        except requests.exceptions.RequestException as e:
            raise ValueError(f"連線 FinMind API 時發生錯誤: {e}")
        except ValueError as e:
            raise ValueError(f"處理 FinMind API 資料時發生錯誤: {e}")
        except Exception as e:
            raise ValueError(f"抓取 FinMind API 資料時發生未預期錯誤: {type(e).__name__} - {e}")
    
    # --- 指標計算函式 (邏輯不變) ---
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
        signals = np.where(is_flat, 0, signals)
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
            raise ValueError(f"股票 {self.stock_id} 有效資料不足（dropna 後僅剩 {len(df)} 筆），無法繪圖。")

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


def analyze_stock(stock_id: str, days: int = 300) -> dict:
    """
    主函式：分析指定股票並返回包含圖表物件的字典。
    """
    try:
        analyzer = TaiwanStockAnalyzer(stock_id, days)
        print(f"正在抓取 {stock_id} ({analyzer.stock_name}) 的資料...")
        analyzer.fetch_data()
        
        print("計算技術指標中...")
        analyzer.calculate_indicators()
        
        print("計算交易訊號中...")
        analyzer.calculate_signals()

        print(f"產生圖表物件: {stock_id}")
        chart_figure = analyzer.create_chart()

        # 使用 _last_valid 取最後一個非 NaN 值，避免暖機期 NaN 被誤判為有效數值
        last_k = _last_valid(analyzer.indicators.get('k', []))
        last_d = _last_valid(analyzer.indicators.get('d', []))
        last_i = _last_valid(analyzer.indicators.get('I_value', []))
        avg_vol_5 = analyzer.price_data['Volume'].iloc[-6:-1].mean()

        return {
            'status': 'success',
            'chart_figure': chart_figure, # 返回圖表物件，而不是圖片路徑
            'indicators': {
                'k': last_k,
                'd': last_d,
                'i_value': last_i,
                'avg_vol_5': avg_vol_5
            }
        }

    except Exception as e:
        error_message = f"分析過程發生錯誤 ({stock_id}): {str(e)}"
        print(error_message)
        # 改善 5：分類錯誤類型，讓 UI 層可以顯示更具體的提示
        err_str = str(e).lower()
        if '429' in err_str or 'rate limit' in err_str or 'too many' in err_str:
            error_type = 'rate_limit'
        elif 'timeout' in err_str or 'connection' in err_str or 'network' in err_str:
            error_type = 'network'
        elif '未回傳' in str(e) or 'no data' in err_str or '資料處理後為空' in str(e):
            error_type = 'no_data'
        elif '有效資料不足' in str(e):
            error_type = 'insufficient_data'
        else:
            error_type = 'unknown'
        return {
            'status': 'error',
            'error_type': error_type,
            'message': error_message
        }