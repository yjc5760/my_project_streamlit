import os
import pandas as pd
import numpy as np
import requests
import twstock
from datetime import date, timedelta

# --- 新增 Plotly 相關導入 ---
import plotly.graph_objects as go
from plotly.subplots import make_subplots

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
            response = requests.get(finmind_url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
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
        # 使用 np.convolve 取代 Python 迴圈，效能提升顯著
        conv = np.convolve(prices, kernel, mode='full')[:len(prices)]
        result[period - 1:] = conv[period - 1:] / weight_sum
        return result

    def _calculate_sma(self, data, period):
        return pd.Series(data).rolling(window=period).mean().values

    def _calculate_stochastic(self, high, low, close, k_period=9, k_slowing=3, d_period=3):
        high_s = pd.Series(high)
        low_s = pd.Series(low)
        close_s = pd.Series(close)
        min_low = low_s.rolling(window=k_period).min()
        max_high = high_s.rolling(window=k_period).max()
        raw_k = 100 * ((close_s - min_low) / (max_high - min_low))
        k = raw_k.rolling(window=k_slowing).mean().values
        d = pd.Series(k).rolling(window=d_period).mean().values
        return k, d

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
        return signals

    def _calculate_deviation_signal(self) -> np.ndarray:
        dev = self.indicators['dev_1_20']
        return np.where(dev >= 5, 4, np.where(dev <= -5, -4, np.nan))

    def create_chart(self) -> go.Figure:
        """
        【重大修改】使用 Plotly 創建互動式圖表，並返回圖表物件。
        """
        df = self.price_data.copy()
        for key, value in self.indicators.items():
            df[key] = value

        df = df.iloc[101:].copy() # 裁切資料以顯示

        fig = make_subplots(
            rows=7, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.4, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
        )

        # 1. K線圖和均線
        fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K線'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['sma5'], mode='lines', name='週線(5)', line=dict(color='blue', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['sma20'], mode='lines', name='月線(20)', line=dict(color='orange', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['sma60'], mode='lines', name='季線(60)', line=dict(color='red', width=1)), row=1, col=1)
        
        # 2. 成交量
        fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='成交量', marker_color='grey'), row=2, col=1)

        # 3. KD指標
        fig.add_trace(go.Scatter(x=df.index, y=df['k'], mode='lines', name='K值', line=dict(color='red', width=1)), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['d'], mode='lines', name='D值', line=dict(color='green', width=1)), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['L_value'], mode='markers', name='KD訊號', marker=dict(color='blue', size=8)), row=3, col=1)

        # 4. 乖離率
        fig.add_trace(go.Scatter(x=df.index, y=df['dev_5_20'], mode='lines', name='週-月', line=dict(color='red', width=1)), row=4, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['dev_20_60'], mode='lines', name='月-季', line=dict(color='green', width=1)), row=4, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['dev_5_60'], mode='lines', name='週-季', line=dict(color='orange', width=1)), row=4, col=1)

        # 5. 訊號
        fig.add_trace(go.Bar(x=df.index, y=df['I_value'], name='階梯訊號', marker_color='red'), row=5, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['J_value'], mode='markers', name='乖離訊號', marker=dict(color='blue', size=8)), row=5, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['K_value'], mode='lines', name='多空訊號', line=dict(color='orange', width=2)), row=5, col=1)

        # 6. MACD
        colors = ['green' if val < 0 else 'red' for val in df['macd_hist']]
        fig.add_trace(go.Bar(x=df.index, y=df['macd_hist'], name='Histogram', marker_color=colors), row=6, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['macd'], mode='lines', name='MACD', line=dict(color='blue', width=1)), row=6, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['macd_signal'], mode='lines', name='Signal', line=dict(color='red', width=1)), row=6, col=1)
        
        # 7. WMA
        fig.add_trace(go.Scatter(x=df.index, y=df['wma5'], mode='lines', name='5WMA', line=dict(color='red', width=1.5)), row=7, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['wma10'], mode='lines', name='10WMA', line=dict(color='green', width=1.5)), row=7, col=1)
        
        # 更新整體佈局
        fig.update_layout(
            title=f'{self.stock_name} ({self.stock_id}) 技術分析圖',
            height=1200,
            xaxis_rangeslider_visible=False,
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        fig.update_xaxes(
            rangebreaks=[dict(bounds=["sat", "mon"])], # 隱藏週末
            tickformat='%Y-%m-%d'
        )
        # 更新y軸標題
        fig.update_yaxes(title_text="股價", row=1, col=1)
        fig.update_yaxes(title_text="成交量", row=2, col=1)
        fig.update_yaxes(title_text="KD", row=3, col=1)
        fig.update_yaxes(title_text="乖離(%)", row=4, col=1)
        fig.update_yaxes(title_text="訊號", row=5, col=1)
        fig.update_yaxes(title_text="MACD", row=6, col=1)
        fig.update_yaxes(title_text="WMA", row=7, col=1)
        
        return fig


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

        last_k = analyzer.indicators['k'][-1] if len(analyzer.indicators.get('k', [])) > 0 else None
        last_d = analyzer.indicators['d'][-1] if len(analyzer.indicators.get('d', [])) > 0 else None
        last_i = analyzer.indicators['I_value'][-1] if len(analyzer.indicators.get('I_value', [])) > 0 else None
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
        return {
            'status': 'error',
            'message': error_message
        }
