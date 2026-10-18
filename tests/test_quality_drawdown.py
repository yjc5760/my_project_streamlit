"""四條件選股（quality_screener）與回檔門檻回測（drawdown_backtest）的純計算邏輯。"""
import numpy as np
import pandas as pd
import pytest

import drawdown_backtest as db
import quality_screener as qs


# ──────────────────────────────────────────────────────────────────────────────
# 回檔門檻回測
# ──────────────────────────────────────────────────────────────────────────────
def _series(parts):
    """parts: [(天數, 終點價)]，線性連接，從 100 開始。"""
    vals, cur = [], 100.0
    for n, end in parts:
        vals.extend(np.linspace(cur, end, n + 1)[1:])
        cur = end
    return pd.Series(vals, index=pd.bdate_range("2015-01-05", periods=len(vals)))


def test_drawdown_counts_one_trigger_per_wave():
    # 平盤 → 跌 20% → 在 -12%～-18% 間來回震盪（同一波，不重複計算）→ 漲回新高 → 再跌 15%
    close = _series([(80, 100), (10, 80), (5, 88), (5, 82), (5, 88), (60, 110), (30, 110), (10, 93.5), (80, 120)])
    res = db.backtest_drawdown(close, thresholds=(10, 15, 20, 25), lookback=60, horizon=20)
    cnt = {r["回檔門檻(%)"]: r["觸發次數"] for r in res["summary"]}
    assert cnt == {10: 2, 15: 2, 20: 1, 25: 0}
    first = res["events"]["10"][0]
    assert first["距高點(%)"] <= -10 and first["已滿期"]
    assert res["baseline"]["樣本天數"] > 0


def test_drawdown_forward_return_and_mae():
    close = _series([(80, 100), (10, 85), (10, 80), (40, 100)])
    res = db.backtest_drawdown(close, thresholds=(10,), lookback=60, horizon=10)
    ev = res["events"]["10"][0]
    i = close.index.get_loc(pd.Timestamp(ev["觸發日"]))
    assert ev["持有10日報酬(%)"] == pytest.approx((close.iloc[i + 10] / close.iloc[i] - 1) * 100, abs=0.01)
    path_min = close.iloc[i:i + 11].min()                       # 只看持有期間內的最低點
    assert ev["期間最大浮虧(%)"] == pytest.approx((path_min / close.iloc[i] - 1) * 100, abs=0.01)
    assert ev["期間最大浮虧(%)"] <= ev["持有10日報酬(%)"]


def test_drawdown_unfinished_trigger_excluded_from_stats():
    close = _series([(80, 100), (5, 85)])           # 最後才觸發，沒有滿 60 日
    res = db.backtest_drawdown(close, thresholds=(10,), lookback=60, horizon=3)
    row = res["summary"][0]
    assert row["觸發次數"] == 1 and row["已滿期次數"] == 0 and row["中位數(%)"] is None
    assert res["current"]["已達門檻"] == [10]


def test_drawdown_too_short():
    with pytest.raises(ValueError):
        db.backtest_drawdown(_series([(50, 100)]), lookback=60, horizon=60)


# ──────────────────────────────────────────────────────────────────────────────
# ④ 營業現金流（2026-10-11 實際算過的數字）
# ──────────────────────────────────────────────────────────────────────────────
_Q_ENDS = ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30",
           "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]


def _ytd(vals_e8):
    return pd.Series([v * 1e8 for v in vals_e8], index=pd.to_datetime(_Q_ENDS))


UNIMICRON = _ytd([86.48415, 67.29223, 102.93587, 29.91482, 87.27374, 109.60347, 149.67094, 58.3267, 133.23412])
KINSUS = _ytd([27.01222, 48.87434, 77.05101, 19.1631, 33.38386, 44.34252, 80.92985, 14.49456, 39.43497])


def test_quarterly_from_ytd():
    q = qs.quarterly_from_ytd(UNIMICRON) / 1e8
    assert q[pd.Period("2024Q3")] == pytest.approx(67.29223 - 86.48415)     # 第 3 季為負
    assert q[pd.Period("2025Q1")] == pytest.approx(29.91482)                # Q1 照用
    assert np.isnan(q[pd.Period("2024Q2")])                                 # 缺 Q1 → 無法還原


def test_ttm_matches_hand_calc():
    t = (qs.ttm(qs.quarterly_from_ytd(UNIMICRON)) / 1e8).dropna().round(2)
    assert t.to_dict() == {pd.Period("2025Q2"): 103.73, pd.Period("2025Q3"): 145.25,
                           pd.Period("2025Q4"): 149.67, pd.Period("2026Q1"): 178.08,
                           pd.Period("2026Q2"): 195.63}


def test_ocf_rising():
    assert qs.ocf_rising(UNIMICRON, 3)["pass"] is True
    r = qs.ocf_rising(KINSUS, 3)                    # 26Q1 TTM 下滑一季
    assert r["pass"] is False and list(r["TTM營業現金流(億)"].values()) == [72.52, 80.93, 76.26, 86.98]


def test_ocf_gap_in_quarters_fails():
    s = UNIMICRON.drop(pd.Timestamp("2025-09-30"))
    assert qs.ocf_rising(s, 3)["pass"] is False


# ──────────────────────────────────────────────────────────────────────────────
# ① 大戶、② 營收、③ 均線
# ──────────────────────────────────────────────────────────────────────────────
def test_holders_rising():
    tpe = [68.51, 68.25, 68.24, 68.07, 67.89, 68.37]         # 台光電，新到舊：連 4 週增加
    r = qs.holders_rising(tpe)
    assert r["pass"] and r["4週增減"] == 0.62 and r["週增減(舊→新)"] == [0.18, 0.17, 0.01, 0.26]
    assert qs.holders_rising([50.81, 50.88, 51.67, 50.14, 48.63, 48.06])["pass"] is False   # 高力：最新一週減少
    assert qs.holders_rising([1, 2, 3])["pass"] is False                                    # 資料不足


def test_holders_strict_requires_8_week_rise():
    # 近 4 週連增，但 8 週前更高 → 一般版通過、嚴格版不通過
    new_to_old = [70.5, 70.3, 70.2, 70.1, 70.0, 71.0, 71.5, 71.2, 71.0]
    assert qs.holders_rising(new_to_old)["pass"] is True
    assert qs.holders_rising(new_to_old, strict=True)["pass"] is False


def _rev_row(cur, p1, p2):
    return {"代碼": "2383", "名稱": "台光電", "年增(%)": cur, "前1月年增(%)": p1, "前2月年增(%)": p2}


@pytest.mark.parametrize("cur,p1,p2,ok", [
    (168.87, 129.84, 129.40, True),
    (45.0, 45.0, 30.0, True),          # 持平算不減速
    (40.0, 45.0, 30.0, False),         # 當月減速
    (10.0, 5.0, -1.0, False),          # 有負成長
    (10.0, None, 5.0, False),          # 缺值
])
def test_revenue_accelerating(cur, p1, p2, ok):
    assert qs.revenue_accelerating(_rev_row(cur, p1, p2), 3) is ok


def test_ma_aligned():
    up = pd.Series(np.arange(1, 31, dtype=float), index=pd.bdate_range("2026-09-01", periods=30))
    assert qs.ma_aligned(up)["pass"] is True
    assert qs.ma_aligned(up[::-1].reset_index(drop=True).set_axis(up.index))["pass"] is False
    assert qs.ma_aligned(up.iloc[:10])["pass"] is False


# ──────────────────────────────────────────────────────────────────────────────
# 逐關篩選流程
# ──────────────────────────────────────────────────────────────────────────────
def test_screen_quality_short_circuits_and_records_reasons():
    rev = pd.DataFrame([
        {"代碼": "1111", "名稱": "全過", "年增(%)": 30, "前1月年增(%)": 20, "前2月年增(%)": 10},
        {"代碼": "2222", "名稱": "營收減速", "年增(%)": 10, "前1月年增(%)": 20, "前2月年增(%)": 30},
        {"代碼": "3333", "名稱": "均線空頭", "年增(%)": 30, "前1月年增(%)": 20, "前2月年增(%)": 10},
        {"代碼": "4444", "名稱": "大戶抓不到", "年增(%)": 30, "前1月年增(%)": 20, "前2月年增(%)": 10},
    ])
    idx = pd.bdate_range("2026-09-01", periods=30)
    up = pd.DataFrame({"close": np.arange(1, 31, dtype=float)}, index=idx)
    down = pd.DataFrame({"close": np.arange(30, 0, -1, dtype=float)}, index=idx)
    called = []

    def price_fn(c):
        return down if c == "3333" else up

    def holders_fn(c):
        called.append(c)
        if c == "4444":
            raise ConnectionError("timeout")
        return pd.DataFrame({"資料日期": pd.date_range("2026-10-09", periods=6, freq="-7D"),
                             "pct": [68.51, 68.25, 68.24, 68.07, 67.89, 68.37]})

    res = qs.screen_quality(rev, price_fn, holders_fn, lambda c: UNIMICRON)
    assert [m["代碼"] for m in res.matches] == ["1111"]
    assert sorted(called) == ["1111", "4444"]                  # 2222、3333 沒過前關，不抓大戶
    reasons = {d["代碼"]: d["未通過"] for d in res.detail}
    assert reasons["2222"].startswith("②") and reasons["3333"].startswith("③")
    assert reasons["4444"] == "①大戶（資料抓取失敗）"
    assert res.errors[0]["代碼"] == "4444"
    assert list(res.funnel.values()) == [4, 3, 2, 1, 1]


def test_adjust_close_dividend_and_face_value_change():
    idx = pd.bdate_range("2025-08-25", periods=6)
    raw = pd.Series([210.5, 210.5, 209.5, 210.0, 52.5, 53.0], index=idx)   # 第 3 天除息 1 元、第 5 天 1 拆 4
    ev = pd.DataFrame({"date": [idx[2], idx[4]], "kind": ["除權息", "面額變更／分割"],
                       "factor": [209.5 / 210.5, 52.5 / 210.0]})
    adj = db.adjust_close(raw, ev)
    assert adj.iloc[-1] == 53.0 and adj.iloc[-2] == 52.5                    # 最新價格不變
    assert adj.pct_change().abs().max() < 0.02                              # 跳空已消除
    assert adj.iloc[0] == pytest.approx(210.5 * (209.5 / 210.5) * (52.5 / 210.0))
