# earnings_materials.py
"""
法說會資料：從公開資訊觀測站「法人說明會一覽表」（t100sb02_1）查某家公司近幾次法說會，
下載簡報 PDF 到 mcp_output/，可選擇抽出文字（需 pip install pypdf）。

- 台灣多數公司只上傳簡報，沒有逐字稿；簡報常以圖表為主，抽出的文字可能不完整。
- 公開資訊觀測站限制查詢頻率：每次只查一家公司、請求之間間隔 1 秒以上。
- 公司每季通常只在自辦的財報說明會發表一份新簡報，之後參加券商論壇都沿用同一份，
  所以結果依「簡報檔」分組，下載最近 n 份不同的簡報（才能比較前後兩季）。
- 查詢：POST /mops/web/ajax_t100sb02_1（TYPEK＝sii／otc、co_id、民國年）。
  簡報連結是表單送出，實際檔案在 /nas/STR/<檔名>。
- debug=True 會把原始回應存成 mcp_output/_mops_t100sb02_*.html（格式變動時對照用）。

欄位（2026-10 實測）：公司代號、公司名稱、召開法人說明會日期、時間、地點、法人說明會擇要訊息、
中文檔案、英文檔案、公司網站、影音連結資訊、其他應敘明事項、歷年法人說明會。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from market_calendar import now_tw
from scrape_utils import DEFAULT_HEADERS, ScrapeError

HOSTS = ["https://mopsov.twse.com.tw", "https://mops.twse.com.tw"]
GAP = 1.2                                             # 請求間隔（秒）
_ROC_DATE = re.compile(r"(\d{3})/(\d{1,2})/(\d{1,2})")
_PDF = re.compile(r"[\w\-]+\.pdf", re.I)


def _decode(content: bytes) -> str:
    for enc in ("utf-8", "cp950", "big5"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _requests_for(code: str, roc_year: int):
    """查詢方式：上市（sii）找不到再試上櫃（otc）。回傳 (標籤, method, path, params)。"""
    base = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
            "co_id": code, "year": str(roc_year), "month": ""}
    for typek in ("sii", "otc"):
        yield (f"ajax_{typek}_{roc_year}", "post", "/mops/web/ajax_t100sb02_1", {**base, "TYPEK": typek})


_VIDEO = re.compile(r"https?://[^\s'\"<>]+")
_SELF_HOSTED = re.compile(r"當天發布|自辦|財務暨營運報告|業績發表")


def parse_conferences(html: str, code: str) -> list[dict]:
    """抓出該公司的資料列。欄位依 2026-10 實測順序；檔名另用 regex 補抓，避免版面小改就失效。"""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        texts = [td.get_text(" ", strip=True) for td in tds]
        if texts[0] != code:
            continue
        m = _ROC_DATE.search(texts[2])
        date = (f"{int(m.group(1)) + 1911}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None)
        zh = _PDF.findall(texts[6]) if len(texts) > 6 else []
        en = _PDF.findall(texts[7]) if len(texts) > 7 else []
        if not zh and not en:                          # 版面變動時退回整列搜尋
            allp = list(dict.fromkeys(_PDF.findall(str(tr))))
            zh = [p for p in allp if not re.search(r"E\d{3}\.pdf$", p, re.I)]
            en = [p for p in allp if p not in zh]
        video = [a.get("href") for a in tds[9].find_all("a") if a.get("href")] if len(tds) > 9 else []
        summary = re.sub(r"\s+", " ", texts[5]) if len(texts) > 5 else ""
        out.append({"日期": date, "時間": texts[3] if len(texts) > 3 else None,
                    "地點": texts[4] if len(texts) > 4 else None, "擇要": summary,
                    "自辦": bool(_SELF_HOSTED.search(summary)) and "受邀" not in summary,
                    "中文簡報": zh, "英文簡報": en, "影音": video})
    out.sort(key=lambda r: r["日期"] or "", reverse=True)
    return out


def group_decks(rows: list[dict]) -> list[dict]:
    """依中文簡報檔分組（沒有中文檔就用英文檔）：首次發表日、自辦說明會、沿用場次、影音連結。新到舊。"""
    decks: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["日期"] or ""):     # 舊到新：影音連結依場次先後排列
        files = r["中文簡報"] or r["英文簡報"]
        if not files:
            continue
        d = decks.setdefault(files[0], {"簡報檔": files[0], "英文簡報檔": (r["英文簡報"] or [None])[0],
                                        "場次": [], "影音": []})
        d["場次"].append(r)
        d["影音"] += [v for v in r["影音"] if v not in d["影音"]]
    out = []
    for d in decks.values():
        sess = sorted(d["場次"], key=lambda r: r["日期"] or "")
        main = next((r for r in sess if r["自辦"]), sess[0])
        out.append({"簡報檔": d["簡報檔"], "英文簡報檔": d["英文簡報檔"],
                    "首次發表日": main["日期"], "發表場合": main["擇要"], "自辦說明會": main["自辦"],
                    "沿用場次": len(sess), "最近一次使用": sess[-1]["日期"], "影音": d["影音"]})
    return sorted(out, key=lambda d: d["首次發表日"] or "", reverse=True)


def list_conferences(code: str, years: int = 2, debug_dir: Path | None = None) -> tuple[list[dict], list[str]]:
    """查近 years 年的法說會。回傳 (資料列, 嘗試紀錄)。"""
    roc = now_tw().year - 1911
    rows, log = [], []
    for y in range(roc, roc - years, -1):
        for label, method, path, params in _requests_for(code, y):
            for host in HOSTS:
                try:
                    if method == "post":
                        r = requests.post(host + path, data=params, headers=DEFAULT_HEADERS, timeout=30)
                    else:
                        r = requests.get(host + path, params=params, headers=DEFAULT_HEADERS, timeout=30)
                except requests.RequestException as e:
                    log.append(f"{label} {host}: 連線失敗 {e}")
                    time.sleep(GAP)
                    continue
                html = _decode(r.content)
                if debug_dir is not None:
                    debug_dir.mkdir(exist_ok=True)
                    (debug_dir / f"_mops_t100sb02_{code}_{label}_{host.split('//')[1].split('.')[0]}.html"
                     ).write_text(html, encoding="utf-8")
                found = parse_conferences(html, code) if r.status_code == 200 else []
                log.append(f"{label} {host}: HTTP {r.status_code}, {len(html)} 字元, 解析到 {len(found)} 筆")
                time.sleep(GAP)
                if found:
                    rows.extend(found)
                    break                                   # 這個查詢方式成功，不用試下一個 host
            if any(r_["日期"] and r_["日期"].startswith(str(y + 1911)) for r_ in rows):
                break                                       # 這一年已經有資料，不用試其他查詢方式
    uniq = {}
    for r_ in rows:
        uniq.setdefault((r_["日期"], r_["時間"], tuple(r_["中文簡報"])), r_)
    return sorted(uniq.values(), key=lambda r_: r_["日期"] or "", reverse=True), log


def _pdf_urls(name: str, links: list[str]) -> list[str]:
    urls = []
    for l in links:
        m = re.search(r"(https?://[^'\"\s)]+\.pdf|/[^'\"\s)]+\.pdf)", l, re.I)
        if m and name in m.group(1):
            u = m.group(1)
            urls += [u] if u.startswith("http") else [h + u for h in HOSTS]
    urls += [f"{h}/nas/STR/{name}" for h in HOSTS]
    return list(dict.fromkeys(urls))


def download_pdf(name: str, links: list[str], dest: Path) -> tuple[Path | None, list[str]]:
    log = []
    for url in _pdf_urls(name, links):
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=60)
        except requests.RequestException as e:
            log.append(f"{url}: 連線失敗 {e}")
            continue
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            dest.parent.mkdir(exist_ok=True)
            dest.write_bytes(r.content)
            return dest, log
        log.append(f"{url}: HTTP {r.status_code}, 開頭 {r.content[:15]!r}")
        time.sleep(GAP)
    return None, log


def pdf_text(path: Path, max_chars: int = 20000) -> str | None:
    """需要 pypdf；沒安裝時回傳 None。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(str(path))
    parts = [f"--- 第 {i} 頁 ---\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages, 1)]
    text = "\n".join(parts)
    return text if len(text) <= max_chars else text[:max_chars] + f"\n…（已截斷，全文 {len(text)} 字）"


def get_materials(code: str, out_dir: Path, n_download: int = 2, years: int = 2,
                  extract_text: bool = True, debug: bool = False, include_sessions: bool = False) -> dict:
    rows, log = list_conferences(code, years, out_dir if debug else None)
    if not rows:
        raise ScrapeError("layout" if any("HTTP 200" in l for l in log) else "network",
                          "公開資訊觀測站查不到這家公司的法說會資料（或頁面格式不符）；嘗試紀錄：" + "；".join(log))
    decks = group_decks(rows)
    notes = []
    for d in decks[:n_download]:
        dest = out_dir / f"{code}_法說會_{d['首次發表日']}_{d['簡報檔']}"
        if dest.exists() and dest.read_bytes()[:4] == b"%PDF":
            path, dl_log = dest, []                              # 已下載過，不重抓
        else:
            path, dl_log = download_pdf(d["簡報檔"], [], dest)
            time.sleep(GAP)
        d["檔案"] = str(path) if path else None
        if path is None:
            d["下載失敗"] = dl_log
        elif extract_text:
            txt = pdf_text(path)
            if txt is None:
                notes.append("未安裝 pypdf，沒有抽出文字（在 .venv-mcp 執行 pip install pypdf 即可）")
            else:
                d["文字"] = txt
    res = {"n_sessions": len(rows), "decks": decks, "notes": list(dict.fromkeys(notes))}
    if include_sessions:
        res["sessions"] = [{k: r[k] for k in ("日期", "時間", "地點", "擇要", "中文簡報", "影音")} for r in rows]
    if debug:
        res["debug_log"] = log
    return res
