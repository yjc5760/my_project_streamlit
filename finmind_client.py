# finmind_client.py
"""
FinMind API 共用連線：全專案只透過這裡呼叫 FinMind。

- 每個執行緒一個 requests.Session，重用連線（併發分析時省掉重複的 TLS 握手）
- 自動帶 FINMIND_API_TOKEN（每次呼叫時讀環境變數，Streamlit secrets 晚設定也吃得到）
- 額度用完（HTTP 402／429）時立即停止，並在冷卻時間內直接拒絕後續呼叫，
  避免批次分析把剩下的請求全部打出去、讓額度更久才恢復
- 錯誤一律 raise FinMindError，kind 用來分類：
    rate_limit  額度用完或限流
    network     連線失敗、逾時、HTTP 5xx
    api         FinMind 回傳錯誤訊息（參數錯、權限不足等）
    no_data     查詢成功但沒有資料
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date

import pandas as pd
import requests

API_URL = "https://api.finmindtrade.com/api/v4/data"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) tw-stock-analyzer"
QUOTA_COOLDOWN = 600          # 額度用完後冷卻秒數（FinMind 額度以小時計）

KIND_LABEL = {"rate_limit": "FinMind 額度用完", "network": "FinMind 連線問題",
              "api": "FinMind 回傳錯誤", "no_data": "FinMind 查無資料"}


class FinMindError(RuntimeError):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(f"[{KIND_LABEL.get(kind, kind)}] {message}")


_local = threading.local()
_quota_lock = threading.Lock()
_blocked_until = 0.0


def _session() -> requests.Session:
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        _local.session = s
    return s


def _check_quota() -> None:
    remain = _blocked_until - time.time()
    if remain > 0:
        raise FinMindError("rate_limit", f"額度已用完，約 {remain / 60:.0f} 分鐘後再試"
                                         "（設定 FINMIND_API_TOKEN 可提高額度）")


def _trip_quota(msg: str) -> FinMindError:
    global _blocked_until
    with _quota_lock:
        _blocked_until = max(_blocked_until, time.time() + QUOTA_COOLDOWN)
    return FinMindError("rate_limit", msg or "額度已用完")


def reset_quota_block() -> None:
    """測試或手動重置用。"""
    global _blocked_until
    with _quota_lock:
        _blocked_until = 0.0


def fetch(dataset: str, data_id: str | None = None, start_date: date | str | None = None,
          end_date: date | str | None = None, timeout: int = 30, retries: int = 3,
          allow_empty: bool = True) -> pd.DataFrame:
    """呼叫 FinMind v4 /data，回傳 DataFrame。allow_empty=False 時沒資料會 raise no_data。"""
    _check_quota()
    params = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = str(start_date)
    if end_date:
        params["end_date"] = str(end_date)
    headers = {}
    token = os.getenv("FINMIND_API_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last: FinMindError | None = None
    for i in range(retries):
        try:
            r = _session().get(API_URL, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last = FinMindError("network", f"{dataset} {data_id or ''} 連線失敗：{e}")
        else:
            js = None
            try:
                js = r.json()
            except ValueError:
                pass
            msg = (js or {}).get("msg") or (js or {}).get("error_message") or ""
            status = (js or {}).get("status", r.status_code)
            if r.status_code in (402, 429) or status in (402, 429) or "upper limit" in str(msg).lower():
                raise _trip_quota(str(msg))
            if r.status_code >= 500:
                last = FinMindError("network", f"HTTP {r.status_code}")
            elif r.status_code >= 400 or js is None or status != 200:
                raise FinMindError("api", f"{dataset} {data_id or ''}：{msg or f'HTTP {r.status_code}'}")
            else:
                df = pd.DataFrame(js.get("data") or [])
                if df.empty and not allow_empty:
                    raise FinMindError("no_data", f"{dataset} {data_id or ''} 沒有資料")
                return df
        if i < retries - 1:
            time.sleep(1 + i)
    raise last  # type: ignore[misc]


def price_history(code: str, start: date, end: date) -> pd.DataFrame:
    """
    日線：DatetimeIndex，欄位 open/high/low/close/volume（volume 單位：股）。
    剔除 FinMind 在暫停交易日給的 0 價資料。
    """
    df = fetch("TaiwanStockPrice", code, start, end, allow_empty=False)
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce")
    df = df[(df["close"] > 0) & (df["high"] > 0)].dropna()
    if df.empty:
        raise FinMindError("no_data", f"{code} 日線清理後沒有資料")
    return df.sort_index()


def month_revenue(code: str, start: date, end: date) -> pd.DataFrame:
    return fetch("TaiwanStockMonthRevenue", code, start, end, allow_empty=False)
