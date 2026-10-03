"""KD 演算法、I 值、個股分析（指標與畫圖分離、錯誤類別）、103 日線重用。"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

import finmind_client
import local_screener
import stock_analyzer
from conftest import make_ohlcv
from indicators import tw_kd


def _naive_kd(h, l, c, n=9):
    k = d = 50.0
    ks, ds = [], []
    for i in range(len(c)):
        if i < n - 1:
            ks.append(np.nan); ds.append(np.nan); continue
        hh, ll = max(h[i - n + 1:i + 1]), min(l[i - n + 1:i + 1])
        rsv = 50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
        ks.append(k); ds.append(d)
    return np.array(ks), np.array(ds)


def test_tw_kd_matches_definition(ohlcv):
    k, d = tw_kd(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    nk, nd = _naive_kd(ohlcv["high"].tolist(), ohlcv["low"].tolist(), ohlcv["close"].tolist())
    np.testing.assert_allclose(k.to_numpy(), nk, equal_nan=True)
    np.testing.assert_allclose(d.to_numpy(), nd, equal_nan=True)
    assert k.iloc[:8].isna().all() and k.iloc[8:].notna().all()


def test_tw_kd_flat_range_is_50():
    flat = pd.Series([10.0] * 12)
    k, d = tw_kd(flat, flat, flat)
    assert k.iloc[-1] == pytest.approx(50.0) and d.iloc[-1] == pytest.approx(50.0)


@pytest.mark.parametrize("a, b, c, expected", [
    (3.0, 1.0, 2.0, 1),     # 週-月 ≥ 週-季 ≥ 月-季
    (2.0, 1.0, 3.0, 2),
    (1.0, 2.0, 3.0, 3),
    (1.0, 3.0, 2.0, -1),
    (2.0, 3.0, 1.0, -2),
    (3.0, 2.0, 1.0, -3),
    (1.00, 1.05, 1.02, 0),  # 均線糾結
])
def test_stair_signal(a, b, c, expected):
    an = stock_analyzer.TaiwanStockAnalyzer("2330", price_data=make_ohlcv(70))
    an.indicators = {"dev_5_20": np.array([a]), "dev_20_60": np.array([b]), "dev_5_60": np.array([c])}
    assert an._calculate_stair_signal()[0] == expected


def test_stair_signal_nan_during_warmup():
    an = stock_analyzer.TaiwanStockAnalyzer("2330", price_data=make_ohlcv(70))
    an.indicators = {"dev_5_20": np.array([1.0]), "dev_20_60": np.array([np.nan]), "dev_5_60": np.array([np.nan])}
    assert np.isnan(an._calculate_stair_signal()[0])     # 以前會落到 -3


def test_analyze_without_chart(ohlcv):
    res = stock_analyzer.analyze_stock("2330", with_chart=False, price_data=ohlcv)
    assert res["status"] == "success" and "chart_figure" not in res
    ind = res["indicators"]
    assert ind["i_value"] in (-3, -2, -1, 0, 1, 2, 3)
    assert 0 <= ind["k"] <= 100 and ind["avg_vol_5"] > 0


def test_analyze_with_chart_and_build_chart(ohlcv):
    res = stock_analyzer.analyze_stock("2330", with_chart=True, price_data=ohlcv)
    assert isinstance(res["chart_figure"], go.Figure)
    assert isinstance(stock_analyzer.build_chart("2330", ohlcv), go.Figure)


def test_price_data_reuse_skips_finmind(ohlcv, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("不應呼叫 FinMind")
    monkeypatch.setattr(finmind_client, "price_history", boom)
    assert stock_analyzer.analyze_stock("2330", with_chart=False, price_data=ohlcv)["status"] == "success"


def test_insufficient_data():
    res = stock_analyzer.analyze_stock("2330", with_chart=False, price_data=make_ohlcv(40))
    assert res["status"] == "error" and res["error_type"] == "insufficient_data"


@pytest.mark.parametrize("kind", ["rate_limit", "network", "no_data", "api"])
def test_error_kind_from_exception_class(kind, monkeypatch):
    def fail(*a, **k):
        raise finmind_client.FinMindError(kind, "測試")
    monkeypatch.setattr(finmind_client, "price_history", fail)
    res = stock_analyzer.analyze_stock("2330", with_chart=False)
    assert res["status"] == "error" and res["error_type"] == kind


def test_fine_screen_keeps_histories_of_matches(monkeypatch):
    from datetime import date
    hist = make_ohlcv(120)
    td = hist.index[-1].date()
    monkeypatch.setattr(local_screener, "fetch_daily_history", lambda code, end, days: hist)
    monkeypatch.setattr(local_screener, "evaluate_103",
                        lambda d, p, check_vol_up=False: {"match": True})
    cands = pd.DataFrame([{"code": "2330", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
    histories: dict = {}
    detail, errors = local_screener.fine_screen(cands, td, local_screener.Screen103Params(),
                                                histories=histories)
    assert not errors and list(histories) == ["2330"] and len(histories["2330"]) == 120
    assert isinstance(td, date)
