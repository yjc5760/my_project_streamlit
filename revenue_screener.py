# revenue_screener.py
"""
本機計算「月營收選股」：今年每個月都創同期新高。

條件（今年 1 月～當月逐月檢查；當月＝全市場最新公告月份，例：10 月初為 9 月）：
  A. 每個月的單月營收，都高於過去 N 年（預設 4 年，例：2022～2025）同月份的最高值
     → 今年那條線每個月都在最上方，每個月都在創同期歷史新高。
  B. 每個月的單月營收年增率（和去年同月比）都 > 門檻（預設 0%）→ 每個月都正成長。
  （A 已經包含「高於去年同月」；B 是讓年增率門檻可以另外調高。）

資料來源：公開資訊觀測站「每月營業收入彙總表」（上市／上櫃 × 國內／KY），不需 FinMind。
  1. 粗篩（條件 B）：抓今年 1 月～當月的彙總表，年增率取「去年同月增減(%)」。
  2. 細篩（條件 A）：同樣用彙總表往回抓前幾年同月份（每個檔案含當年與去年，隔年抓一次），
     只比對粗篩通過的公司。

用法：
    python revenue_screener.py                     # 今天
    python revenue_screener.py --diag 2344,2330    # 逐月診斷
    python revenue_screener.py --detail rev.csv
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date, timedelta
from typing import Callable

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from local_screener import (UA, ScreenerError, _COMMON_STOCK, _to_date,
                            fetch_tpex_day, fetch_twse_day)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MOPS_HOSTS = ["https://mopsov.twse.com.tw", "https://mops.twse.com.tw"]
MARKETS = {"sii": "上市", "otc": "上櫃"}


@dataclass
class RevenueParams:
    yoy_min: float = 0.0             # B. 今年每個月的單月年增率皆 > yoy_min（%）
    lookback_years: int = 4          # A. 今年每個月的單月營收皆 > 過去 N 年同月份最高值
    per_company_month: bool = False  # False：全市場統一以「最新有公告的月份」為當月（還沒公告的不入選）
                                     # True：各公司以自己今年最新公告的月份為當月


@dataclass
class RevenueResult:
    matches: pd.DataFrame
    detail: pd.DataFrame             # 所有粗篩候選股（含逐月營收、前高、是否符合）
    as_of: date
    months_loaded: list              # [(年, 月, 公司數), ...]
    universe_size: int
    errors: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# 公開資訊觀測站 每月營業收入彙總表
# ---------------------------------------------------------------------------
def _num(s) -> float:
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_mops_t21sc03(html: str) -> pd.DataFrame:
    """
    解析 t21sc03 彙總表 HTML。各產業一張表、欄位順序固定：
    公司代號, 公司名稱, 當月營收, 上月營收, 去年當月營收, 上月比較增減(%), 去年同月增減(%),
    當月累計營收, 去年累計營收, 前期比較增減(%), 備註
    以位置解析（不依賴表頭），只取第一欄是股票代號的列。
    """
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 7 or not re.fullmatch(r"\d{4}[A-Z]?", tds[0]):
            continue
        rows.append({
            "code": tds[0], "name": tds[1],
            "revenue": _num(tds[2]) * 1000,        # 仟元 → 元
            "rev_prev_month": _num(tds[3]) * 1000,
            "rev_last_year": _num(tds[4]) * 1000,
            "mom": _num(tds[5]),
            "yoy": _num(tds[6]),
        })
    return pd.DataFrame(rows)


def _decode(content: bytes) -> str:
    for enc in ("utf-8", "cp950", "big5"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _fetch_mops_file(path: str, problems: list) -> pd.DataFrame | None:
    """None = 連線失敗；空表 = 檔案不存在或無資料。"""
    for host in MOPS_HOSTS:
        try:
            r = requests.get(host + path, headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 404:
                return pd.DataFrame()
            r.raise_for_status()
            return parse_mops_t21sc03(_decode(r.content))
        except requests.RequestException as e:
            problems.append(f"{host}{path}: {e}")
    return None


@lru_cache(maxsize=128)
def fetch_mops_month(year: int, month: int) -> pd.DataFrame:
    """
    抓某年月的上市＋上櫃營收彙總（國內 _0 ＋ KY _1；較早年份沒有分檔時改抓無後綴檔名）。
    尚未公告或不存在時回傳空表。
    """
    roc = year - 1911
    frames, problems, n_files = [], [], 0
    for mk, label in MARKETS.items():
        got_any = False
        for suffix in ("_0", "_1", ""):
            if suffix == "" and got_any:
                break
            n_files += 1
            got = _fetch_mops_file(f"/nas/t21/{mk}/t21sc03_{roc}_{month}{suffix}.html", problems)
            time.sleep(0.2)
            if got is not None and not got.empty:
                got["market"] = label
                frames.append(got)
                got_any = True
    if not frames:
        if len(problems) >= n_files * len(MOPS_HOSTS):         # 每個檔案所有 host 都連不上
            raise ScreenerError(f"公開資訊觀測站連線失敗：{problems[0]}")
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).drop_duplicates("code")
    df["ym"] = year * 100 + month
    return df


def _shift(ym: int, k: int) -> int:
    """ym=202608, k=-1 → 202607"""
    y, m = divmod(ym, 100)
    idx = y * 12 + (m - 1) + k
    return (idx // 12) * 100 + idx % 12 + 1


def find_latest_month(as_of: date, max_back: int = 3) -> int:
    """全市場最新有公告的月份（從 as_of 前一個月往回找），回傳 yyyymm。"""
    cur = as_of.year * 100 + as_of.month
    for k in range(1, max_back + 1):
        ym = _shift(cur, -k)
        if not fetch_mops_month(ym // 100, ym % 100).empty:
            return ym
    raise ScreenerError(f"公開資訊觀測站找不到 {as_of} 之前 {max_back} 個月內的營收彙總表")


def _fetch_months(yms: list[int], max_workers: int = 4,
                  progress_cb: Callable[[int, int, str], None] | None = None,
                  label: str = "") -> dict[int, pd.DataFrame]:
    """平行抓多個年月的彙總表（已快取的不會重抓）。連線失敗會拋 ScreenerError。"""
    out: dict[int, pd.DataFrame] = {}
    if not yms:
        return out
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        futs = {ex.submit(fetch_mops_month, ym // 100, ym % 100): ym for ym in yms}
        for done, f in enumerate(as_completed(futs), 1):
            ym = futs[f]
            out[ym] = f.result()
            if progress_cb:
                progress_cb(done, len(futs), f"{label}{ym // 100}/{ym % 100:02d}")
    return out


def load_year_months(year: int, last_month: int, max_workers: int = 4,
                     progress_cb: Callable[[int, int, str], None] | None = None
                     ) -> tuple[pd.DataFrame, list]:
    """載入 year 年 1 月～last_month 月的彙總表（只留普通股）。"""
    yms = [year * 100 + m for m in range(1, last_month + 1)]
    got = _fetch_months(yms, max_workers, progress_cb, "今年 ")
    loaded = [(ym // 100, ym % 100, len(got[ym])) for ym in yms]
    missing = [f"{y}/{m:02d}" for y, m, n in loaded if n == 0]
    if missing:
        raise ScreenerError(f"公開資訊觀測站缺少 {'、'.join(missing)} 的營收彙總表，無法逐月比較")
    rev = pd.concat(got.values(), ignore_index=True)
    rev = rev[rev["code"].str.match(_COMMON_STOCK)]
    return rev.drop_duplicates(["code", "ym"]), loaded


# ---------------------------------------------------------------------------
# 粗篩：條件 B（今年每個月年增率 > 門檻）
# ---------------------------------------------------------------------------
def coarse_revenue(rev: pd.DataFrame, year: int, last_month: int, p: RevenueParams) -> pd.DataFrame:
    """
    回傳每家公司一列：rev_m（今年 m 月營收）、y1_m（去年 m 月）、yoy_m（m 月年增率），
    n_months（要檢查到幾月）、當月的 revenue / mom / yoy_cur，以及 pass_yoy。
    """
    rev = rev.copy()
    rev["month"] = rev["ym"] % 100
    calc = (rev["revenue"] / rev["rev_last_year"] - 1) * 100       # 彙總表沒填年增率時自己算
    rev["yoy"] = rev["yoy"].where(rev["yoy"].notna(), calc.where(rev["rev_last_year"] > 0))
    months = list(range(1, last_month + 1))
    piv = {c: rev.pivot(index="code", columns="month", values=c).reindex(columns=months).astype(float)
           for c in ("revenue", "rev_last_year", "yoy", "mom")}
    codes = piv["revenue"].index

    if p.per_company_month:
        has = piv["revenue"].notna().to_numpy()
        last = np.where(has.any(axis=1), last_month - np.argmax(has[:, ::-1], axis=1), 0)
    else:
        # 全市場同一個當月：還沒公告當月營收的公司，當月年增率為空值 → 不通過
        last = np.full(len(codes), last_month)

    yoy = piv["yoy"].to_numpy()
    in_range = np.arange(1, last_month + 1)[None, :] <= last[:, None]
    ok = np.where(in_range, yoy > p.yoy_min, True).all(axis=1) & (last > 0)   # NaN → 不通過

    df = pd.DataFrame({"code": codes, "latest_ym": year * 100 + last, "n_months": last})
    names = rev.sort_values("ym").groupby("code").last()[["name", "market"]]
    df = df.join(names, on="code")
    for m in months:
        df[f"rev_{m}"] = piv["revenue"][m].to_numpy()
        df[f"y1_{m}"] = piv["rev_last_year"][m].to_numpy()
        df[f"yoy_{m}"] = piv["yoy"][m].to_numpy()
    rows, idx = np.arange(len(codes)), np.clip(last, 1, None) - 1
    for col, src in (("revenue", "revenue"), ("mom", "mom"), ("yoy_cur", "yoy")):
        df[col] = np.where(last > 0, piv[src].to_numpy()[rows, idx], np.nan)
    df["pass_yoy"] = ok
    return df


# ---------------------------------------------------------------------------
# 細篩：條件 A（今年每個月營收 > 過去 N 年同月份最高）
# ---------------------------------------------------------------------------
def _hist_file_years(year: int, n: int) -> list[int]:
    """過去 n 年（year-2 起）需要抓哪些年份的檔案；每個檔案含當年與去年。去年已在今年的檔案裡。"""
    return list(range(year - 2, year - n - 1, -2))


def same_month_ceiling(cands: pd.DataFrame, year: int, last_month: int, p: RevenueParams,
                       max_workers: int = 4,
                       progress_cb: Callable[[int, int, str], None] | None = None
                       ) -> tuple[pd.DataFrame, list]:
    """
    補上 yk_m（year-k 年 m 月營收，k = 1..N）、ceil_m（過去 N 年 m 月最高）、
    beat_m（今年 m 月超過前高幾 %）、min_beat（最弱的那個月超過前高幾 %）、match。
    歷史年份沒有資料（例如當時尚未上市）的不列入比較。
    回傳 (結果, 缺少的彙總表年月清單)。
    """
    out = cands.copy().reset_index(drop=True)
    n_years = max(1, int(p.lookback_years))
    months = list(range(1, last_month + 1))
    fys = _hist_file_years(year, n_years)
    got = _fetch_months([fy * 100 + m for fy in fys for m in months], max_workers, progress_cb, "歷年 ")
    missing = []
    for m in months:
        cols = [f"y1_{m}"]
        for fy in fys:
            h = got[fy * 100 + m]
            if h.empty:
                missing.append(f"{fy}/{m:02d}")
                h = pd.DataFrame(columns=["code", "revenue", "rev_last_year"])
            h = h.drop_duplicates("code").set_index("code")
            k = year - fy
            out[f"y{k}_{m}"] = out["code"].map(h["revenue"]).astype(float)
            cols.append(f"y{k}_{m}")
            if k + 1 <= n_years:
                out[f"y{k + 1}_{m}"] = out["code"].map(h["rev_last_year"]).astype(float)
                cols.append(f"y{k + 1}_{m}")
        out[f"ceil_{m}"] = out[cols].max(axis=1, skipna=True)
        out[f"beat_{m}"] = (out[f"rev_{m}"] / out[f"ceil_{m}"] - 1) * 100

    n = out["n_months"].to_numpy()
    ok = n > 0
    min_beat = np.full(len(out), np.inf)
    for m in months:
        active = m <= n
        r, c = out[f"rev_{m}"].to_numpy(float), out[f"ceil_{m}"].to_numpy(float)
        ok &= ~active | (r > c)                                  # 前高為 NaN → 不通過
        min_beat = np.where(active, np.fmin(min_beat, out[f"beat_{m}"].to_numpy(float)), min_beat)
    out["min_beat"] = np.where(np.isfinite(min_beat), min_beat, np.nan)
    out["match"] = ok
    return out, missing


# ---------------------------------------------------------------------------
# 附加：最新收盤價與成交張數（給視覺化的量能過濾用，失敗不影響選股）
# ---------------------------------------------------------------------------
def latest_quotes(as_of: date, max_back: int = 10) -> pd.DataFrame:
    d = as_of
    for _ in range(max_back):
        tw = fetch_twse_day(d)
        if not tw.empty:
            frames = [tw]
            try:
                frames.append(fetch_tpex_day(d))
            except ScreenerError:
                pass
            q = pd.concat(frames, ignore_index=True)
            q["vol_lots"] = q["volume"] / 1000
            return q[["code", "close", "vol_lots"]]
        d -= timedelta(days=1)
    return pd.DataFrame(columns=["code", "close", "vol_lots"])


# ---------------------------------------------------------------------------
# 對外介面
# ---------------------------------------------------------------------------
def _ym_txt(v: int) -> str:
    return f"{v // 100}/{v % 100:02d}"


def screen_revenue(as_of=None, params: RevenueParams | None = None, max_workers: int = 4,
                   progress_cb: Callable[[int, int, str], None] | None = None,
                   with_quotes: bool = True) -> RevenueResult:
    p = params or RevenueParams()
    as_of = _to_date(as_of)
    year, last_month = divmod(find_latest_month(as_of), 100)
    rev, loaded = load_year_months(year, last_month, max_workers, progress_cb)
    coarse = coarse_revenue(rev, year, last_month, p)
    cands = coarse[coarse["pass_yoy"]].reset_index(drop=True)
    print(f"[月營收] 載入月份 {[(f'{y}/{m:02d}', n) for y, m, n in loaded]}")
    print(f"[月營收] 全市場 {len(coarse)} 檔 → {year} 年 1～{last_month} 月年增率皆 > {p.yoy_min}%：{len(cands)} 檔")

    notes, errors = [], {}
    if len(cands):
        detail, missing = same_month_ceiling(cands, year, last_month, p, max_workers, progress_cb)
        if missing:
            notes.append(f"公開資訊觀測站缺少 {'、'.join(missing)} 的彙總表，這些年月未納入前高比較")
    else:
        detail = cands.assign(match=False, min_beat=np.nan)

    hit = detail[detail["match"]].sort_values("min_beat", ascending=False)
    print(f"[月營收] 每個月都高於過去 {p.lookback_years} 年同月份：{len(hit)} 檔")

    recs = []
    for r in hit.to_dict("records"):
        n = int(r["n_months"])
        rec = {"代碼": r["code"], "名稱": r["name"], "市場": r["market"],
               "營收月份": _ym_txt(int(r["latest_ym"])), "檢查月份": f"1～{n}月",
               "單月營收(億)": round(r["revenue"] / 1e8, 2),
               "月增(%)": r["mom"], "年增(%)": r["yoy_cur"],
               "最弱月超越前高(%)": round(r["min_beat"], 1)}
        for k in range(1, n):                                    # 熱度表用：前k月 = 當月往前 k 個月
            rec[f"前{k}月年增(%)"] = r[f"yoy_{n - k}"]
        recs.append(rec)
    base_cols = ["代碼", "名稱", "市場", "營收月份", "檢查月份", "單月營收(億)", "月增(%)",
                 "年增(%)", "最弱月超越前高(%)"]
    matches = pd.DataFrame(recs) if recs else pd.DataFrame(columns=base_cols)

    if with_quotes and len(matches):
        try:
            q = latest_quotes(as_of)
            matches = matches.merge(q.rename(columns={"code": "代碼", "close": "收盤價",
                                                      "vol_lots": "成交張數"}),
                                    on="代碼", how="left")
            matches["成交張數"] = matches["成交張數"].round(0)
        except ScreenerError as e:
            notes.append(f"最新股價取不到，未附收盤價／成交張數：{e}")

    return RevenueResult(matches, detail, as_of, loaded, len(coarse), errors, notes)


def diagnose(codes: list[str], as_of=None, p: RevenueParams | None = None) -> None:
    p = p or RevenueParams()
    as_of = _to_date(as_of)
    year, lm = divmod(find_latest_month(as_of), 100)
    rev, loaded = load_year_months(year, lm)
    print("今年載入月份（年, 月, 公司數）：", loaded)
    coarse = coarse_revenue(rev, year, lm, p)
    sel = coarse[coarse["code"].isin(codes)]
    det = same_month_ceiling(sel, year, lm, p)[0].set_index("code") if len(sel) else pd.DataFrame()
    N = max(1, int(p.lookback_years))
    yrs = [year] + [year - k for k in range(1, N + 1)]
    for code in codes:
        print(f"\n===== {code} =====")
        if code not in det.index:
            print("  ✗ 不在今年的營收彙總表中")
            continue
        r = det.loc[code]
        n = int(r["n_months"])
        print(f"  {r['name']}（{r['market']}） 檢查 {year}/01～{year}/{max(n, 1):02d}（單位：億）")
        print("  月份" + "".join(f"{y:>9}" for y in yrs) + f"{'前高':>8}{'年增%':>8}   A  B")
        for m in range(1, lm + 1):
            vals = [r[f"rev_{m}"]] + [r.get(f"y{k}_{m}", np.nan) for k in range(1, N + 1)]
            cells = "".join(f"{v / 1e8:9.2f}" if pd.notna(v) else f"{'–':>9}" for v in vals)
            ceil, yoy = r[f"ceil_{m}"], r[f"yoy_{m}"]
            if m > n:
                flags = "（未公告，不檢查）"
            else:
                flags = f"   {'✓' if r[f'rev_{m}'] > ceil else '✗'}  {'✓' if yoy > p.yoy_min else '✗'}"
            ceil_txt = f"{ceil / 1e8:8.2f}" if pd.notna(ceil) else f"{'–':>8}"
            yoy_txt = f"{yoy:8.1f}" if pd.notna(yoy) else f"{'–':>8}"
            print(f"  {m:>2}月{cells}{ceil_txt}{yoy_txt}{flags}")
        print(f"  {'✓' if r['match'] else '✗'} A 每月都高於過去 {N} 年同月份最高"
              f"（最弱月超越 {r['min_beat']:.1f}%）")
        print(f"  {'✓' if r['pass_yoy'] else '✗'} B 每月年增率皆 > {p.yoy_min}%")


def _main():
    ap = argparse.ArgumentParser(description="本機計算月營收選股：今年每個月都創同期新高")
    ap.add_argument("--date", help="基準日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--diag", help="逐月診斷指定代碼，例如 --diag 2344,2330")
    ap.add_argument("--years", type=int, default=4, help="和過去幾年同月份比較（預設 4）")
    ap.add_argument("--yoy-min", type=float, default=0.0, help="每月年增率門檻 %%（預設 0）")
    ap.add_argument("--detail", help="輸出所有候選股明細 CSV")
    a = ap.parse_args()
    p = RevenueParams(yoy_min=a.yoy_min, lookback_years=a.years)
    if a.diag:
        diagnose([c.strip() for c in a.diag.split(",") if c.strip()], a.date, p)
        return
    res = screen_revenue(a.date, p)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    print(res.matches.to_string(index=False) if not res.matches.empty else "（無符合股票）")
    for n in res.notes:
        print("注意：", n)
    if a.detail and not res.detail.empty:
        res.detail.to_csv(a.detail, index=False, encoding="utf-8-sig")
        print(f"候選股明細 → {a.detail}")


if __name__ == "__main__":
    _main()
