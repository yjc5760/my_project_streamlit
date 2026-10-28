"""快取修正：有期限的記憶體快取、磁碟快取、月營收快取期限、大戶抓取改用 fetch_html。"""
from datetime import date

import pandas as pd
import pytest

import scrape_utils as su


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(su.time, "time", c)
    return c


def test_memoize_ttl_expires(clock):
    calls = []

    @su.memoize_ttl(60)
    def f(x):
        calls.append(x)
        return x * 2
    assert f(1) == 2 and f(1) == 2 and calls == [1]          # 期限內不重算
    clock.t += 61
    assert f(1) == 2 and calls == [1, 1]                      # 過期重算


def test_memoize_ttl_does_not_cache_failures(clock):
    n = {"calls": 0}

    @su.memoize_ttl(60)
    def f():
        n["calls"] += 1
        if n["calls"] == 1:
            raise RuntimeError("暫時失敗")
        return "ok"
    with pytest.raises(RuntimeError):
        f()
    assert f() == "ok" and n["calls"] == 2                     # 失敗後立刻重試，不會卡在失敗結果


def test_memoize_ttl_per_result_ttl_and_maxsize(clock):
    calls = []

    @su.memoize_ttl(lambda args, res: 0 if res is None else 60, maxsize=2)
    def f(x):
        calls.append(x)
        return None if x == 0 else x
    f(0); f(0)
    assert calls == [0, 0]                                     # ttl=0 → 不快取
    f(1); f(2); f(3)                                           # 上限 2：最舊的 1 被淘汰
    f(3); f(2)
    assert calls == [0, 0, 1, 2, 3]
    f(1)
    assert calls[-1] == 1
    f.cache_clear(); f(3)
    assert calls[-1] == 3


def test_disk_memo(tmp_path, monkeypatch, clock):
    monkeypatch.delenv("DISABLE_DISK_MEMO", raising=False)
    monkeypatch.setattr(su, "MEMO_DIR", tmp_path)
    calls = []

    @su.disk_memo("t", 100)
    def g(code):
        calls.append(code)
        if code == "bad":
            raise ValueError("x")
        return pd.DataFrame({"a": [1]})

    import os
    assert g("2330").equals(pd.DataFrame({"a": [1]})) and g("2330") is not None and calls == ["2330"]
    f = next((tmp_path / "t").glob("*.pkl"))
    os.utime(f, (clock.t - 200, clock.t - 200))                # 檔案超過期限 → 重抓
    g("2330")
    assert calls == ["2330", "2330"]
    with pytest.raises(ValueError):
        g("bad")
    assert len(list((tmp_path / "t").glob("*.pkl"))) == 1      # 失敗不寫檔


def test_disk_memo_kwargs_and_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("DISABLE_DISK_MEMO", raising=False)
    monkeypatch.setattr(su, "MEMO_DIR", tmp_path)
    calls = []

    @su.disk_memo("kwargs_t", 100)
    def f(code, years=3, mode="full"):
        calls.append((code, years, mode))
        return 1

    f("2330")
    f("2330", 3)
    f("2330", years=3)
    f("2330", mode="full", years=3)
    assert calls == [("2330", 3, "full")]


def test_disk_memo_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "MEMO_DIR", tmp_path)              # conftest 已設 DISABLE_DISK_MEMO
    calls = []

    @su.disk_memo("t", 100)
    def g():
        calls.append(1)
        return 1
    g(); g()
    assert calls == [1, 1] and not (tmp_path / "t").exists()


def test_mops_ttl(monkeypatch):
    import revenue_screener as rs

    class D(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 18)
    monkeypatch.setattr(rs, "date", D)
    full = pd.DataFrame({"code": ["2330"]})
    assert rs._mops_ttl((2026, 9), pd.DataFrame()) == 1800       # 還沒公告
    assert rs._mops_ttl((2026, 9), full) == 3600                 # 上個月：還可能增加
    assert rs._mops_ttl((2026, 8), full) == 3600
    assert rs._mops_ttl((2026, 7), full) == 7 * 86400            # 已定案


def test_fetch_major_holders_uses_fetch_html(monkeypatch):
    import stock_information_plot as sip
    html = """<table id="Details">
      <tr><td>序</td><td>資料日期</td><td>集保總張數</td><td>>400張大股東持有百分比</td></tr>
      <tr><td>1</td><td>20261016</td><td>1000</td><td>78.49</td></tr>
      <tr><td>2</td><td>20261008</td><td>1000</td><td>79.18</td></tr>
    </table>"""
    seen = {}

    def fake_fetch_html(url, label, **kw):
        seen["url"] = url
        return html
    monkeypatch.setattr(sip, "fetch_html", fake_fetch_html)
    df = sip.fetch_major_holders("3037")
    assert "stock=3037" in seen["url"]
    assert df[sip.HOLDERS_PCT_COL].tolist() == [78.49, 79.18]
    assert df["資料日期"].iloc[0] == pd.Timestamp("2026-10-16")


def test_plot_shareholders_reports_scrape_error(monkeypatch):
    import stock_information_plot as sip

    def boom(code):
        raise su.ScrapeError("blocked", "被擋")
    monkeypatch.setattr(sip, "fetch_major_holders", boom)
    fig, err = sip.plot_stock_major_shareholders("2330")
    assert fig is None and "被擋" in err
