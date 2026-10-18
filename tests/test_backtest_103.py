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
