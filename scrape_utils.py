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
    try:
        LKG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = LKG_DIR / f"{key}.pkl.tmp"
        with open(tmp, "wb") as f:
            pickle.dump({"saved_at": datetime.now(ZoneInfo("Asia/Taipei")), "data": data}, f)
        tmp.replace(LKG_DIR / f"{key}.pkl")
    except Exception as e:                          # noqa: BLE001  存檔失敗不影響主流程
        print(f"[lkg] 無法儲存 {key}: {e}")


def lkg_load(key: str):
    """回傳 (data, saved_at)；沒有存檔時回傳 None。"""
    path = LKG_DIR / f"{key}.pkl"
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            obj = pickle.load(f)
        return obj["data"], obj["saved_at"]
    except Exception as e:                          # noqa: BLE001
        print(f"[lkg] 無法讀取 {key}: {e}")
        return None


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
        @functools.wraps(fn)
        def wrapper(*args):
            if os.getenv("DISABLE_DISK_MEMO"):
                return fn(*args)
            key = hashlib.md5(repr(args).encode()).hexdigest()[:16]
            path = MEMO_DIR / namespace / f"{key}.pkl"
            try:
                if path.exists() and time.time() - path.stat().st_mtime < ttl_sec:
                    with open(path, "rb") as f:
                        return pickle.load(f)
            except Exception as e:                  # noqa: BLE001
                print(f"[memo] 讀取 {namespace}/{key} 失敗，改為重抓：{e}")
            result = fn(*args)
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
