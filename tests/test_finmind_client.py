"""第 5 項：FinMind 共用連線——額度用完立即停止、錯誤分類。"""
import pytest
import requests

import finmind_client as fc


class FakeResp:
    def __init__(self, status=200, js=None):
        self.status_code = status
        self._js = js

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.headers = {}

    def get(self, *a, **kw):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def session(monkeypatch):
    fc.reset_quota_block()
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)

    def install(*responses):
        s = FakeSession(responses)
        monkeypatch.setattr(fc, "_session", lambda: s)
        return s
    yield install
    fc.reset_quota_block()


def test_success(session):
    session(FakeResp(200, {"status": 200, "data": [{"date": "2026-10-01", "close": 1}]}))
    assert len(fc.fetch("TaiwanStockPrice", "2330")) == 1


def test_quota_exhausted_stops_immediately(session):
    s = session(FakeResp(402, {"status": 402, "msg": "Requests reach the upper limit"}))
    with pytest.raises(fc.FinMindError) as e:
        fc.fetch("TaiwanStockPrice", "2330")
    assert e.value.kind == "rate_limit"
    assert s.calls == 1                       # 不重試
    with pytest.raises(fc.FinMindError) as e2:  # 冷卻期間後續呼叫直接拒絕，不打 API
        fc.fetch("TaiwanStockPrice", "2317")
    assert e2.value.kind == "rate_limit" and s.calls == 1


def test_network_retries_then_fails(session):
    s = session(requests.ConnectionError("x"), requests.ConnectionError("x"), requests.ConnectionError("x"))
    with pytest.raises(fc.FinMindError) as e:
        fc.fetch("TaiwanStockPrice", "2330")
    assert e.value.kind == "network" and s.calls == 3


def test_network_recovers(session):
    session(requests.ConnectionError("x"), FakeResp(200, {"status": 200, "data": [{"a": 1}]}))
    assert len(fc.fetch("TaiwanStockPrice", "2330")) == 1


def test_no_data(session):
    session(FakeResp(200, {"status": 200, "data": []}))
    with pytest.raises(fc.FinMindError) as e:
        fc.fetch("TaiwanStockPrice", "9999", allow_empty=False)
    assert e.value.kind == "no_data"


def test_api_error(session):
    session(FakeResp(400, {"status": 400, "msg": "parameter error"}))
    with pytest.raises(fc.FinMindError) as e:
        fc.fetch("TaiwanStockPrice", "2330")
    assert e.value.kind == "api"


def test_price_history_normalises(session):
    session(FakeResp(200, {"status": 200, "data": [
        {"date": "2026-10-02", "open": 10, "max": 11, "min": 9, "close": 10.5, "Trading_Volume": 1000},
        {"date": "2026-10-01", "open": 0, "max": 0, "min": 0, "close": 0, "Trading_Volume": 0},   # 暫停交易
    ]}))
    df = fc.price_history("2330", "2026-09-01", "2026-10-02")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 1 and df["high"].iloc[0] == 11
