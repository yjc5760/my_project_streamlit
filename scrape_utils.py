# scrape_utils.py
"""
第三方網頁爬蟲共用工具：統一的錯誤分類，以及「上次成功結果」(last-known-good) 存取。

錯誤分類（ScrapeError.kind）：
    network  連線失敗、逾時、HTTP 4xx/5xx
    blocked  被擋（403/429/503、Cloudflare 驗證頁、驗證碼）——多半是雲端 IP 或限流
    layout   網頁抓到了，但找不到預期的表格／欄位——網站改版
    empty    結構正確但沒有資料——可能尚未更新
"""
from __future__ import annotations

import functools
import inspect
import hashlib
import json
import os
import pickle
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KIND_LABEL = {
    "network": "連線問題",
    "blocked": "被網站阻擋",
    "layout": "網頁改版",
    "empty": "沒有資料",
}

DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

# 只認 Cloudflare 驗證頁的特徵字；一般網頁可能內嵌 reCAPTCHA 等字樣，不能拿來判斷
_BLOCK_MARKERS = re.compile(r"<title>\s*Just a moment|cf-chl-|Attention Required! \| Cloudflare", re.I)


class ScrapeError(RuntimeError):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(f"[{KIND_LABEL.get(kind, kind)}] {message}")


def fetch_html(url: str, label: str, headers: dict | None = None, timeout: int = 20,
               retries: int = 3, encoding: str | None = None) -> str:
    """GET 一個網頁並回傳文字；失敗時 raise ScrapeError（已分類）。"""
    last: ScrapeError | None = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=headers or DEFAULT_HEADERS, timeout=timeout)
        except requests.Timeout:
            last = ScrapeError("network", f"{label}連線逾時（{timeout} 秒）")
        except requests.RequestException as e:
            last = ScrapeError("network", f"{label}連線失敗：{e}")
        else:
            head = r.text[:5000] if r.text else ""
            if r.status_code in (403, 429, 503) or _BLOCK_MARKERS.search(head):
                last = ScrapeError("blocked", f"{label}拒絕存取（HTTP {r.status_code}），"
                                              f"可能是擋雲端 IP 或請求太頻繁")
            elif r.status_code >= 400:
                last = ScrapeError("network", f"{label}回應 HTTP {r.status_code}")
            else:
                if encoding:
                    r.encoding = encoding
                return r.text
        if i < retries - 1:
            time.sleep(2 ** i)
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 上次成功結果（last-known-good）
# Streamlit Cloud 的檔案系統在 app 重新部署／休眠喚醒後會清空，
# 所以這只能撐過「資料源暫時故障」，不是長期儲存。
# ---------------------------------------------------------------------------
LKG_DIR = Path(os.getenv("LKG_DIR", Path(__file__).resolve().parent / ".cache" / "last_good"))


def lkg_key(name: str, params: dict | None = None) -> str:
    if not params:
        return name
    h = hashlib.md5(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:10]
    return f"{name}_{h}"


def lkg_save(key: str, data) -> None:
    obj = {"saved_at": datetime.now(ZoneInfo("Asia/Taipei")), "data": data}
    try:
        LKG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = LKG_DIR / f"{key}.pkl.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(obj, f)
        tmp.replace(LKG_DIR / f"{key}.pkl")
    except Exception as e:                          # noqa: BLE001  存檔失敗不影響主流程
        print(f"[lkg] 無法儲存 {key}: {e}")
    _remote_save(key, obj)


def lkg_load(key: str):
    """回傳 (data, saved_at)；沒有存檔時回傳 None。本機沒有時（例如雲端重新部署後）改讀遠端備份。"""
    path = LKG_DIR / f"{key}.pkl"
    if not path.exists():
        obj = _remote_load(key)
        return (obj["data"], obj["saved_at"]) if obj else None
    try:
        with open(path, "rb") as f:
            obj = pickle.load(f)
        return obj["data"], obj["saved_at"]
    except Exception as e:                          # noqa: BLE001
        print(f"[lkg] 無法讀取 {key}: {e}")
        return None


# ---------------------------------------------------------------------------
# 上次成功結果的遠端備份（選用）：存到使用者自己的私人 GitHub Gist，
# 讓 Streamlit Cloud 重新部署、休眠喚醒後仍有上次結果可顯示。
#   LKG_GIST_ID       Gist 的 ID（網址最後那串）
#   LKG_GITHUB_TOKEN  只勾 gist 權限的 GitHub token
# 兩個都設定才啟用；沒設定時行為和以前完全一樣。
# - 上傳在背景執行緒，不拖慢畫面；資料沒變就不重傳。
# - 只有本機沒有存檔時才讀遠端，讀回來後存一份到本機。
# - 每份資料以 token 做 HMAC-SHA256 簽章，讀取時驗證，簽章不符一律丟棄（不 unpickle 來路不明的資料）。
# ---------------------------------------------------------------------------
_GIST_API = "https://api.github.com/gists/"
_REMOTE_PREFIX = "lkg1"
_remote_sent: dict[str, str] = {}                  # key → 最後一次上傳的資料雜湊
_remote_lock = threading.Lock()


def _remote_cfg() -> tuple[str, str] | None:
    gist, token = os.getenv("LKG_GIST_ID", "").strip(), os.getenv("LKG_GITHUB_TOKEN", "").strip()
    return (gist, token) if gist and token else None


def _gist_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _encode_remote(obj, token: str) -> str:
    import base64, hmac, zlib
    blob = zlib.compress(pickle.dumps(obj), 6)
    sig = hmac.new(token.encode(), blob, hashlib.sha256).hexdigest()
    return f"{_REMOTE_PREFIX}:{sig}:{base64.b64encode(blob).decode()}"


def _decode_remote(text: str, token: str):
    """驗證簽章後還原；格式或簽章不符時 raise ValueError。"""
    import base64, hmac, zlib
    try:
        prefix, sig, b64 = text.strip().split(":", 2)
        blob = base64.b64decode(b64, validate=True)
    except Exception as e:                          # noqa: BLE001
        raise ValueError(f"格式不符：{e}") from e
    if prefix != _REMOTE_PREFIX:
        raise ValueError("格式版本不符")
    expect = hmac.new(token.encode(), blob, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        raise ValueError("簽章不符（token 已更換或內容遭修改）")
    return pickle.loads(zlib.decompress(blob))


def _remote_upload(key: str, content: str, digest: str, cfg: tuple[str, str]) -> None:
    gist, token = cfg
    try:
        r = requests.patch(_GIST_API + gist, headers=_gist_headers(token), timeout=20,
                           json={"files": {f"{key}.lkg": {"content": content}}})
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        with _remote_lock:
            _remote_sent[key] = digest
    except Exception as e:                          # noqa: BLE001  備份失敗不影響主流程
        print(f"[lkg] 遠端備份 {key} 失敗：{e}")


def _remote_save(key: str, obj, background: bool = True) -> None:
    cfg = _remote_cfg()
    if not cfg:
        return
    try:
        digest = hashlib.sha256(pickle.dumps(obj["data"])).hexdigest()
        with _remote_lock:
            if _remote_sent.get(key) == digest:     # 資料沒變（例如同一份結果畫面重整），不重傳
                return
        content = _encode_remote(obj, cfg[1])
    except Exception as e:                          # noqa: BLE001
        print(f"[lkg] 遠端備份 {key} 失敗：{e}")
        return
    if background:
        threading.Thread(target=_remote_upload, args=(key, content, digest, cfg), daemon=True).start()
    else:
        _remote_upload(key, content, digest, cfg)


def _remote_load(key: str):
    cfg = _remote_cfg()
    if not cfg:
        return None
    gist, token = cfg
    try:
        r = requests.get(_GIST_API + gist, headers=_gist_headers(token), timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        f = (r.json().get("files") or {}).get(f"{key}.lkg")
        if not f:
            return None
        text = f.get("content") or ""
        if f.get("truncated"):                      # 超過 1MB 時 API 只回部分內容，改抓原始檔
            raw = requests.get(f["raw_url"], headers=_gist_headers(token), timeout=30)
            raw.raise_for_status()
            text = raw.text
        obj = _decode_remote(text, token)
    except Exception as e:                          # noqa: BLE001
        print(f"[lkg] 讀取遠端備份 {key} 失敗：{e}")
        return None
    try:                                            # 讀回來存一份到本機，下次不用再連網
        LKG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = LKG_DIR / f"{key}.pkl.tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(obj, fh)
        tmp.replace(LKG_DIR / f"{key}.pkl")
    except Exception:                               # noqa: BLE001
        pass
    with _remote_lock:
        _remote_sent[key] = hashlib.sha256(pickle.dumps(obj["data"])).hexdigest()
    return obj


# ---------------------------------------------------------------------------
# 有期限的快取（失敗不快取：函式 raise 時什麼都不存）
# ---------------------------------------------------------------------------
def memoize_ttl(ttl, maxsize: int = 256):
    """
    記憶體快取。ttl 可以是秒數，或 ttl(args, result) -> 秒數（依參數／結果決定期限，例如空結果短一點）。
    超過 maxsize 時淘汰最久沒用的。執行緒安全。提供 .cache_clear()。
    """
    def deco(fn):
        store: OrderedDict = OrderedDict()
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args):
            now = time.time()
            with lock:
                hit = store.get(args)
                if hit and hit[0] > now:
                    store.move_to_end(args)
                    return hit[1]
            result = fn(*args)
            sec = ttl(args, result) if callable(ttl) else ttl
            if sec > 0:
                with lock:
                    store[args] = (now + sec, result)
                    store.move_to_end(args)
                    while len(store) > maxsize:
                        store.popitem(last=False)
            return result

        def cache_clear():
            with lock:
                store.clear()
        wrapper.cache_clear = cache_clear
        return wrapper
    return deco


MEMO_DIR = Path(os.getenv("MEMO_DIR", LKG_DIR.parent / "memo"))


def disk_memo(namespace: str, ttl_sec: int):
    """
    磁碟快取（.cache/memo/<namespace>/）：給季報、大戶週資料這類很少變動、又會耗 API 額度的資料。
    程式重啟後仍有效；讀寫失敗時直接重抓，不影響主流程。設環境變數 DISABLE_DISK_MEMO=1 可關閉。
    """
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if os.getenv("DISABLE_DISK_MEMO"):
                return fn(*args, **kwargs)
            # 把位置／關鍵字參數一律綁定成完整參數（含預設值），
            # 讓 f(x, 3)、f(x, years=3)、f(x) 共用同一個快取鍵
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            key = hashlib.md5(repr(tuple(bound.arguments.items())).encode()).hexdigest()[:16]
            path = MEMO_DIR / namespace / f"{key}.pkl"
            try:
                if path.exists() and time.time() - path.stat().st_mtime < ttl_sec:
                    with open(path, "rb") as f:
                        return pickle.load(f)
            except Exception as e:                  # noqa: BLE001
                print(f"[memo] 讀取 {namespace}/{key} 失敗，改為重抓：{e}")
            result = fn(*args, **kwargs)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                with open(tmp, "wb") as f:
                    pickle.dump(result, f)
                tmp.replace(path)
            except Exception as e:                  # noqa: BLE001
                print(f"[memo] 無法儲存 {namespace}/{key}：{e}")
            return result
        return wrapper
    return deco
