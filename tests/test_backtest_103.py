"""我的選股103 回測：逐日向量化的條件必須和實際選股用的 evaluate_103 完全一致。"""
import numpy as np
import pandas as pd
import pytest

import backtest_103 as bt
from local_screener import Screen103Params, evaluate_103
from tests.conftest import make_ohlcv


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_conditions_match_evaluate_103(seed):
    df = make_ohlcv(n=260, start="2025-01-02", seed=seed, trend=0.05)
    p = Screen103Params()
    cond = bt.conditions_103(df, p)
    for t in (70, 101, 150, 199, 259):                       # 隨機挑幾天，含週中與週五
        ev = evaluate_103(df.iloc[: t + 1], p, check_vol_up=True)
        row = cond.iloc[t]
        for k in ("ma60_dev", "day_k", "day_d", "week_k", "week_k_prev"):
            assert row[k] == pytest.approx(ev[k], abs=1e-9), (seed, t, k)
        for k in ("c3_ma60_dev", "c4_week_k", "c5_week_k_up", "c6_ma20_lt_ma60", "c7_dk_gt_dd", "c8_vol_up"):
            assert bool(row[k]) == ev[k], (seed, t, k)


def test_red_k_and_volume_conditions():
    df = make_ohlcv(n=80, seed=4)
    df.iloc[-1, df.columns.get_loc("open")] = df["close"].iloc[-2]
    df.iloc[-1, df.columns.get_loc("close")] = df["close"].iloc[-2] * 1.05      # 紅K 5%
    df.iloc[-1, df.columns.get_loc("volume")] = df["volume"].iloc[-2] * 2        # 量增 2 倍
    c = bt.conditions_103(df, Screen103Params(vol_min=0)).iloc[-1]
    assert c["red_k"] == pytest.approx(5.0) and c["c1_red_k"] and c["c8_vol_up"] and c["c2_volume"]


def test_simulate_rule_stop_and_take_profit():
    idx = pd.bdate_range("2026-01-05", periods=6)
    adj = pd.DataFrame({"open": [100, 100, 99, 95, 96, 97], "high": [101, 103, 100, 96, 97, 120],
                        "low": [99, 98, 91, 94, 95, 96], "close": [100, 101, 93, 95, 96, 118]}, index=idx)
    r, i, why = bt.simulate_rule(adj, 1, stop=8, tp=None, hold=5)              # 第 2 根低點 91 → 停損 92
    assert why == "停損" and i == 2 and r == pytest.approx(92 / 100 - 1 - bt.COST)
    r, i, why = bt.simulate_rule(adj, 1, stop=None, tp=2, hold=5)              # 進場當天高點 103 → 停利 102
    assert why == "停利" and i == 1 and r == pytest.approx(0.02 - bt.COST)
    assert bt.simulate_rule(adj, 1, None, None, 10) is None                    # 資料不足


def test_adjusted_prices():
    idx = pd.bdate_range("2026-01-05", periods=4)
    raw = pd.DataFrame({"open": [100, 100, 97, 98], "high": [101, 101, 98, 99], "low": [99, 99, 96, 97],
                        "close": [100, 100, 97, 98], "volume": 1}, index=idx)
    adj = bt.adjusted(raw, pd.DataFrame({"date": [idx[2]], "factor": [0.97]}))
    assert adj["close"].tolist() == pytest.approx([97, 97, 97, 98])


def _yahoo_raw():
    """模擬 yf.download(group_by='ticker') 的 MultiIndex 結果（2 檔，其中 9999.TW 全是空值）。"""
    idx = pd.DatetimeIndex(["2026-07-08", "2026-07-09", "2026-07-10"], name="Date")
    cols = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    a = pd.DataFrame([[810, 820, 800, 810, 806.08, 1e6], [806, 830, 800, 820, 820, 2e6],
                      [820, 825, 815, 822, 822, 1.5e6]], index=idx, columns=cols)
    b = pd.DataFrame(np.nan, index=idx, columns=cols)
    return pd.concat({"3167.TWO": a, "9999.TW": b}, axis=1)


def test_yahoo_frame_and_adjust():
    raw = _yahoo_raw()
    df = bt._yahoo_frame(raw, "3167.TWO")
    assert list(df.columns) == ["open", "high", "low", "close", "adj_close", "volume"] and len(df) == 3
    assert bt._yahoo_frame(raw, "9999.TW") is None            # 全空值 → 視為沒有資料
    assert bt._yahoo_frame(raw, "1234.TW") is None            # 不在結果裡
    adj = bt.adjusted_yahoo(df)
    assert adj["close"].iloc[0] == pytest.approx(806.08)      # 除息前一天被調整
    assert adj["open"].iloc[0] == pytest.approx(810 * 806.08 / 810)
    assert adj["close"].iloc[-1] == 822                        # 最新價格不變


def test_compare_sources(tmp_path, monkeypatch):
    fm, yh = tmp_path / "fm", tmp_path / "yh"
    fm.mkdir(); yh.mkdir()
    monkeypatch.setattr(bt, "PRICES", fm)
    monkeypatch.setattr(bt, "PRICES_YAHOO", yh)
    idx = pd.bdate_range("2026-01-05", periods=30)
    base = pd.DataFrame({"date": idx, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                         "volume": 6_000_000.0})
    base.to_csv(fm / "2330.csv.gz", index=False)
    y = base.copy(); y["adj_close"] = 100.0
    y.loc[3, "close"] = 103.0                                   # 一天收盤差 3%
    y.loc[5, "volume"] = 4_000_000.0                            # 一天成交量跨過 5000 張門檻
    y.drop(index=[7]).to_csv(yh / "2330.csv.gz", index=False)   # Yahoo 少一天
    r = bt.compare_sources().iloc[0]
    assert r["重疊天數"] == 29 and r["Yahoo缺的天數"] == 1
    assert r["收盤差>0.5%的天數比例(%)"] == pytest.approx(100 / 29, abs=0.01)
    assert r["張數≥5000判斷不一致天數"] == 1

def test_yahoo_taint_marks_window_around_bad_jump():
    import numpy as np
    import pandas as pd
    from backtest_103 import yahoo_taint
    px = pd.Series(np.full(300, 100.0))
    px.iloc[2] = 150.0                      # 上市初期（前 5 日）不算
    px.iloc[3:] = 100.0
    px.iloc[150:] = 75.0                    # 單日 -25%：超過漲跌幅限制
    t = yahoo_taint(px, days=60)
    assert not t[:89].any()
    assert t[90:211].all()
    assert not t[211:].any()
