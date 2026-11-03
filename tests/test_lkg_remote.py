"""上次成功結果的 Gist 遠端備份（以假的 GitHub API 測試，不連網）。"""
import pickle

import pandas as pd
import pytest

import scrape_utils as su


class FakeGist:
    """模擬 GET / PATCH https://api.github.com/gists/<id>。"""

    def __init__(self):
        self.files: dict[str, str] = {}
        self.patches = 0
        self.gets = 0

    def patch(self, url, headers=None, json=None, timeout=None):
        assert url.endswith("/G1") and headers["Authorization"] == "Bearer TOKEN"
        self.patches += 1
        for name, f in json["files"].items():
            self.files[name] = f["content"]
        return _Resp(200, {})

    def get(self, url, headers=None, timeout=None):
        self.gets += 1
        return _Resp(200, {"files": {n: {"content": c, "truncated": False} for n, c in self.files.items()}})


class _Resp:
    def __init__(self, code, data):
        self.status_code, self._data, self.text = code, data, ""

    def json(self):
        return self._data


@pytest.fixture
def gist(tmp_path, monkeypatch):
    g = FakeGist()
    monkeypatch.setattr(su, "LKG_DIR", tmp_path / "lkg")
    monkeypatch.setattr(su.requests, "patch", g.patch)
    monkeypatch.setattr(su.requests, "get", g.get)
    monkeypatch.setenv("LKG_GIST_ID", "G1")
    monkeypatch.setenv("LKG_GITHUB_TOKEN", "TOKEN")
    monkeypatch.setattr(su, "_remote_sent", {})
    # 測試裡同步上傳，方便檢查
    orig = su._remote_save
    monkeypatch.setattr(su, "_remote_save", lambda k, o: orig(k, o, background=False))
    return g


def test_disabled_without_env(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "LKG_DIR", tmp_path)
    monkeypatch.delenv("LKG_GIST_ID", raising=False)
    monkeypatch.delenv("LKG_GITHUB_TOKEN", raising=False)
    called = []
    monkeypatch.setattr(su.requests, "patch", lambda *a, **k: called.append(1))
    monkeypatch.setattr(su.requests, "get", lambda *a, **k: called.append(1))
    su.lkg_save("k", {"v": 1})
    assert su.lkg_load("k")[0] == {"v": 1}
    assert su.lkg_load("missing") is None
    assert called == []


def test_roundtrip_after_local_wiped(gist):
    df = pd.DataFrame({"代碼": ["2330"], "K": [45.1]})
    su.lkg_save("screen103_x", {"matches": df})
    assert gist.patches == 1 and "screen103_x.lkg" in gist.files
    # 模擬 Streamlit Cloud 重新部署：本機檔案消失
    (su.LKG_DIR / "screen103_x.pkl").unlink()
    data, saved_at = su.lkg_load("screen103_x")
    pd.testing.assert_frame_equal(data["matches"], df)
    assert (su.LKG_DIR / "screen103_x.pkl").exists()          # 讀回後存一份到本機
    su.lkg_load("screen103_x")
    assert gist.gets == 1                                     # 第二次直接讀本機


def test_unchanged_data_not_reuploaded(gist):
    su.lkg_save("k", {"v": 1})
    su.lkg_save("k", {"v": 1})
    assert gist.patches == 1
    su.lkg_save("k", {"v": 2})
    assert gist.patches == 2


def test_tampered_or_wrong_token_rejected(gist, monkeypatch):
    su.lkg_save("k", {"v": 1})
    (su.LKG_DIR / "k.pkl").unlink()
    # 換 token：舊簽章驗不過 → 當作沒有備份
    monkeypatch.setenv("LKG_GITHUB_TOKEN", "OTHER")
    monkeypatch.setattr(gist, "get", gist.get)
    assert su._remote_load("k") is None
    # 竄改內容（換成別的 pickle、沿用舊簽章）
    monkeypatch.setenv("LKG_GITHUB_TOKEN", "TOKEN")
    prefix, sig, _ = gist.files["k.lkg"].split(":", 2)
    import base64, zlib
    evil = base64.b64encode(zlib.compress(pickle.dumps({"saved_at": 0, "data": "evil"}))).decode()
    gist.files["k.lkg"] = f"{prefix}:{sig}:{evil}"
    assert su._remote_load("k") is None


def test_remote_failure_does_not_raise(gist, monkeypatch):
    def boom(*a, **k):
        raise su.requests.ConnectionError("down")
    monkeypatch.setattr(su.requests, "patch", boom)
    monkeypatch.setattr(su.requests, "get", boom)
    su.lkg_save("k", {"v": 1})                 # 本機照存
    assert su.lkg_load("k")[0] == {"v": 1}
    assert su.lkg_load("nothing") is None
