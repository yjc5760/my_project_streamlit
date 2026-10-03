import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_ohlcv(n: int = 150, start: str = "2026-01-05", seed: int = 0, trend: float = 0.3,
               end_vol_mult: float = 1.0) -> pd.DataFrame:
    """合成日線（交易日、小寫欄位、volume 單位：股）。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    close = 100 + np.cumsum(rng.normal(trend, 1.5, n))
    open_ = close + rng.normal(0, 0.8, n)
    high = np.maximum(open_, close) + rng.uniform(0.1, 1.5, n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 1.5, n)
    vol = rng.integers(2_000_000, 6_000_000, n).astype(float)
    vol[-1] *= end_vol_mult
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


@pytest.fixture
def ohlcv():
    return make_ohlcv()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """測試一律不連網：休市日表當作空的（只判斷週末）。"""
    import market_calendar
    monkeypatch.setattr(market_calendar, "_closed_days_for", lambda year: set())
    yield
