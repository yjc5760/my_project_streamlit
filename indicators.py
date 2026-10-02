# indicators.py
"""共用技術指標。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def tw_kd(high, low, close, n: int = 9, init: float = 50.0) -> tuple[pd.Series, pd.Series]:
    """
    台灣慣用遞迴 KD（Goodinfo、多數看盤軟體採用）：
        RSV = (C - L_n) / (H_n - L_n) * 100     （H_n == L_n 時 RSV = 50）
        K   = K_prev * 2/3 + RSV * 1/3
        D   = D_prev * 2/3 + K   * 1/3          （初始 K = D = init）
    前 n-1 根為 NaN。輸入可為 Series 或 array；回傳 Series（沿用 close 的 index）。
    """
    close = pd.Series(close).reset_index(drop=True) if not isinstance(close, pd.Series) else close
    high = pd.Series(np.asarray(high, dtype=float), index=close.index)
    low = pd.Series(np.asarray(low, dtype=float), index=close.index)
    hh = high.rolling(n).max()
    ll = low.rolling(n).min()
    rng = (hh - ll).to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)
    lo = ll.to_numpy(dtype=float)
    k = np.full(len(c), np.nan)
    d = np.full(len(c), np.nan)
    pk = pd_ = init
    for i in range(len(c)):
        if np.isnan(rng[i]) or np.isnan(c[i]):
            continue
        rsv = 50.0 if rng[i] == 0 else (c[i] - lo[i]) / rng[i] * 100
        pk = pk * 2 / 3 + rsv / 3
        pd_ = pd_ * 2 / 3 + pk / 3
        k[i], d[i] = pk, pd_
    return pd.Series(k, index=close.index), pd.Series(d, index=close.index)
