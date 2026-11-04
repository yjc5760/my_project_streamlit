"""kline_chart：券商風日K線（K棒＋EMA＋分價量＋成交量）。"""
import numpy as np
import pandas as pd

from kline_chart import make_kline_figure, volume_profile
from conftest import make_ohlcv


def test_volume_profile_conserves_volume():
    df = make_ohlcv(120)
    vp = volume_profile(df, bins=20)
    assert len(vp) == 20
    assert np.isclose(vp["volume"].sum(), df["volume"].sum())


def test_volume_profile_doji_bar():
    df = pd.DataFrame({"high": [10.0, 12.0], "low": [10.0, 8.0], "volume": [100.0, 400.0]})
    vp = volume_profile(df, bins=4)
    assert np.isclose(vp["volume"].sum(), 500.0)


def test_make_kline_figure_basic():
    df = make_ohlcv(200)
    fig = make_kline_figure(df, "測試 0000", months=3)
    kinds = [t.type for t in fig.data]
    assert kinds == ["candlestick", "scatter", "scatter", "bar"]
    shown = fig.data[0].x
    assert len(shown) < len(df)                       # 只顯示最近 3 個月
    vol = fig.data[3].y
    assert np.isclose(vol[-1], df["volume"].iloc[-1] / 1000)   # 股 -> 張
    assert all(s.layer == "below" for s in fig.layout.shapes)   # 分價量在K棒後面
    texts = [a.text for a in fig.layout.annotations]
    v = df[df.index > df.index[-1] - pd.DateOffset(months=3)]
    assert f"←{v.high.max():g}" in texts and f"{v.low.min():g}→" in texts


def test_vol_updown_colors():
    df = make_ohlcv(200)
    fig = make_kline_figure(df, months=3, vol_updown=True)
    colors = set(fig.data[3].marker.color)
    assert colors <= {"#e8343a", "#2fb34a"}


def test_kd_macd_panels():
    from indicators import tw_kd

    df = make_ohlcv(200)
    fig = make_kline_figure(df, months=3, panels=["kd", "macd"])
    names = [t.name for t in fig.data]
    assert names[-5:] == ["K", "D", "OSC", "DIF", "MACD"]
    # KD 用專案共用的 tw_kd，且在完整資料上暖機後才裁切
    k, _ = tw_kd(df["high"], df["low"], df["close"])
    assert np.isclose(fig.data[names.index("K")].y[-1], k.iloc[-1])
    assert fig.layout.height == 640 + 150 * 2
    assert "yaxis4" in fig.layout and "yaxis5" not in fig.layout   # 主圖、量、KD、MACD 共 4 列


def test_bad_panel_rejected():
    import pytest

    with pytest.raises(ValueError):
        make_kline_figure(make_ohlcv(200), panels=["rsi"])
