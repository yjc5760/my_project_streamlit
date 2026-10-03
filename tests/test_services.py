"""第 6 項：共用篩選規則（Streamlit 與 MCP 共用）。"""
import pandas as pd
import pytest

import scrape_utils
import services
from scrape_utils import ScrapeError


@pytest.mark.parametrize("code, ok", [("2330", True), ("0050", False), ("00878", False),
                                       ("2330A", False), (" 1101 ", True)])
def test_is_common_stock(code, ok):
    assert services.is_common_stock(code) is ok


def _rank_df():
    return pd.DataFrame({
        "Rank": [3, 1, 2, 4, 5],
        "Stock Symbol": ["2317", "2330", "0050", "2454", "9999"],
        "Stock Name": ["鴻海", "台積電", "元大50", "聯發科", "假"],
        "Price": ["200", "1000", "150", "20", "80"],          # 聯發科 20 元：低於股價門檻
        "Change Percent": ["5.1", "3.2", "2.5", "9", "4"],
        "Estimated Volume": [10000, 4000, 99999, 50000, 7000],
    })


def fake_analyze(code):
    if code == "9999":
        return {"status": "error", "error_type": "no_data", "message": "查無"}
    if code == "1234":
        return {"status": "error", "error_type": "rate_limit", "message": "額度"}
    return {"status": "success", "indicators": {"k": 80.0, "d": 70.0, "i_value": 1.0, "avg_vol_5": 3_000_000}}


def test_prelim_ranking():
    pre, excluded = services.prelim_ranking(_rank_df(), 35, 2.0)
    assert set(pre["Stock Symbol"]) == {"2317", "2330", "9999"}
    assert excluded == ["元大50(0050)"]


def test_screen_ranking():
    res = services.screen_ranking(_rank_df(), fake_analyze, 35, 2.0, 2.0)
    # 5日均量 3000 張 × 2 = 6000：鴻海 10000 通過、台積電 4000 不通過
    assert [r["stock_info"]["Stock Symbol"] for r in res.passed] == ["2317"]
    assert res.no_data == ["假(9999)"] and res.n_prelim == 3 and not res.errors


def test_screen_ranking_errors_sorted():
    df = _rank_df()
    df.loc[1, "Stock Symbol"] = "1234"
    res = services.screen_ranking(df, fake_analyze, 35, 2.0, 2.0)
    assert res.errors[0]["error_type"] == "rate_limit"


def test_filter_concentration():
    raw = pd.DataFrame({
        "編號": [1, 2, 3], "代碼": ["2330", "00878", "2317"], "股票名稱": ["台積電", "國泰永續", "鴻海"],
        "1日集中度": [1, 1, 1], "5日集中度": [3, 3, 1], "10日集中度": [2, 2, 2], "20日集中度": [1, 1, 1],
        "60日集中度": [1, 1, 1], "120日集中度": [1, 1, 1], "10日均量": [5000, 5000, 5000]})
    assert services.filter_concentration(raw, 2000)["代碼"].tolist() == ["2330"]


def test_batch_analyze_dedup_and_errors():
    def fn(c):
        if c == "2":
            raise RuntimeError("boom")
        return {"status": "success"}
    seen = []
    out = services.batch_analyze(["1", "1", "2", "", None, "nan"], fn, lambda i, n, c: seen.append(n))
    assert set(out) == {"1", "2"} and out["2"]["status"] == "error" and seen == [2, 2]


def test_with_lkg_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape_utils, "LKG_DIR", tmp_path)
    data, stale = services.with_lkg("k", lambda: {"v": 1})
    assert data == {"v": 1} and stale is None

    def fail():
        raise ScrapeError("blocked", "403")
    data, stale = services.with_lkg("k", fail)
    assert data == {"v": 1} and stale is not None

    with pytest.raises(ScrapeError):
        services.with_lkg("other", fail)
