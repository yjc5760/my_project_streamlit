# revenue_screener.py
"""
本機計算「月營收選股03」，取代 Goodinfo 爬蟲（monthly_revenue_scraper.scrape_goodinfo）。

Goodinfo 原條件（FL_MARKET=上市/上櫃）：
  1. 單月營收年增率 – 當月  > 15%
  2. 單月營收年增率 – 前1月 > 10%
  3. 單月營收年增率 – 前2月 > 10%
  4. 單月營收年增率 – 前3月 > 10%
  5. 單月營收年增率 – 前4月 > 10%
  6. 單月營收創歷年同期前3高

兩階段：
  1. 粗篩（條件 1–5）：公開資訊觀測站「每月營業收入彙總表」近 6 個月（上市／上櫃 × 國內／KY）。
     「當月」與 Goodinfo 相同：全市場統一取最新有資料的月份（例：10 月初為 9 月，
     尚未公告 9 月營收的公司不會入選）；年增率直接取彙總表的「去年同月增減(%)」。
  2. 細篩（條件 6）：同樣用彙總表往回抓歷年同月份（每個檔案含當年與去年，隔年抓一次），
     比較營收排名；確定掉出前 N 名的就不再追蹤。不需呼叫 FinMind。

用法：
    python revenue_screener.py                     # 今天
    python revenue_screener.py --diag 2330,6488    # 逐條件診斷
    python revenue_screener.py --detail rev.csv
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
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
    yoy_cur_min: float = 15.0        # 1. 當月年增率 >
    yoy_prev_min: float = 10.0       # 2–5. 前 1~4 月年增率 >
    n_prev: int = 4                  # 前幾個月要檢查
    top_n: int = 3                   # 6. 創歷年同期前 N 高（0 = 不檢查）
    months_to_load: int = 6          # 粗篩載入幾個月的彙總表
    per_company_month: bool = False  # False（同 Goodinfo）：全市場統一以「最新有公告的月份」為當月
                                     # True：各公司以自己最新公告的月份為當月


@dataclass
class RevenueResult:
    matches: pd.DataFrame
    detail: pd.DataFrame             # 所有粗篩候選股（含同期排名）
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


def load_revenue_months(as_of: date, n: int) -> tuple[pd.DataFrame, list]:
    """載入 as_of 前 n 個月份（不含 as_of 當月）的彙總表。"""
    cur = as_of.year * 100 + as_of.month
    frames, loaded = [], []
    for k in range(1, n + 1):
        ym = _shift(cur, -k)
        df = fetch_mops_month(ym // 100, ym % 100)
        loaded.append((ym // 100, ym % 100, len(df)))
        if not df.empty:
            frames.append(df)
    if not frames:
        raise ScreenerError("公開資訊觀測站沒有任何月份的營收彙總資料")
    all_ = pd.concat(frames, ignore_index=True)
    all_ = all_[all_["code"].str.match(_COMMON_STOCK)]
    return all_.drop_duplicates(["code", "ym"]), loaded


# ---------------------------------------------------------------------------
# 粗篩：條件 1–5
# ---------------------------------------------------------------------------
def coarse_revenue(rev: pd.DataFrame, p: RevenueParams) -> pd.DataFrame:
    latest = rev.groupby("code")["ym"].max().rename("latest_ym")
    if not p.per_company_month:
        # Goodinfo 的「當月」是全市場同一個月（例：10 月初就是 26M09），
        # 還沒公告該月營收的公司，當月年增率為空值 → 不通過
        latest[:] = rev["ym"].max()
    piv = rev.pivot(index="code", columns="ym", values="yoy")
    out = []
    for code, lym in latest.items():
        yoys = [piv.at[code, _shift(lym, -i)] if _shift(lym, -i) in piv.columns else np.nan
                for i in range(p.n_prev + 1)]
        out.append({"code": code, "latest_ym": lym,
                    **{("yoy_cur" if i == 0 else f"yoy_prev{i}"): v for i, v in enumerate(yoys)}})
    df = pd.DataFrame(out)
    names = rev.sort_values("ym").groupby("code").last()[["name", "market"]]
    cur = rev.set_index(["code", "ym"])[["revenue", "rev_last_year", "mom"]]
    df = df.join(names, on="code")
    keys = list(zip(df["code"], df["latest_ym"]))
    for col in ("revenue", "rev_last_year", "mom"):
        df[col] = [cur[col].get(k, np.nan) for k in keys]
    # Goodinfo 範圍「15 ~ 空白」視為 >= 15
    ok = df["yoy_cur"] >= p.yoy_cur_min
    for i in range(1, p.n_prev + 1):
        ok &= df[f"yoy_prev{i}"] >= p.yoy_prev_min       # NaN（缺月份）→ 不通過
    df["pass_yoy"] = ok
    return df


# ---------------------------------------------------------------------------
# 細篩：條件 6 歷年同期排名
# ---------------------------------------------------------------------------
def same_month_ranks(cands: pd.DataFrame, p: RevenueParams, max_years: int = 30,
                     progress_cb: Callable[[int, int, str], None] | None = None) -> pd.DataFrame:
    """
    條件 6：當月營收在「歷年同月份」中的名次。
    資料來源同為公開資訊觀測站彙總表：每個年份的檔案同時含「當月」與「去年當月」，
    所以每隔兩年抓一次即可。某檔已確定落到前 N 名之外就不再追蹤；
    全部確定（或抓到沒有資料的年份）就提早停止。
    回傳 cands 加上 same_month_rank、years_compared、match 欄位。
    """
    out = cands.copy().reset_index(drop=True)
    out["exceed"] = (out["rev_last_year"] > out["revenue"]).astype(int)   # 去年同月
    out["years_compared"] = 1 + out["rev_last_year"].notna().astype(int)

    groups = list(out.groupby("latest_ym").groups.items())
    total_steps = max(1, len(groups) * (max_years // 2))
    step = 0
    for lym, idx in groups:
        y, m = divmod(int(lym), 100)
        yy, empty_streak = y - 2, 0
        while yy > y - max_years:
            step += 1
            pending = [i for i in idx if out.at[i, "exceed"] < p.top_n
                       and pd.notna(out.at[i, "revenue"])]
            if not pending:
                break
            if progress_cb:
                progress_cb(min(step, total_steps), total_steps, f"{yy}/{m:02d}")
            print(f"[月營收] 同期排名：比對 {yy}/{m:02d}、{yy - 1}/{m:02d}，待定 {len(pending)} 檔")
            hist = fetch_mops_month(yy, m)
            if hist.empty:
                empty_streak += 1
                if empty_streak >= 2:                   # 已抓到資料起始年之前
                    break
                yy -= 2
                continue
            empty_streak = 0
            h = hist.set_index("code")
            for i in pending:
                code = out.at[i, "code"]
                if code not in h.index:
                    continue
                cur = out.at[i, "revenue"]
                for col in ("revenue", "rev_last_year"):
                    v = h.at[code, col]
                    if pd.notna(v):
                        out.at[i, "years_compared"] += 1
                        out.at[i, "exceed"] += int(v > cur)
            yy -= 2
    out["same_month_rank"] = (out["exceed"] + 1).where(out["revenue"].notna())   # 未公告 → NaN
    out["match"] = (p.top_n <= 0) | (out["same_month_rank"] <= p.top_n)
    return out.drop(columns="exceed")


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
def screen_revenue(as_of=None, params: RevenueParams | None = None, max_workers: int = 4,
                   progress_cb: Callable[[int, int, str], None] | None = None,
                   with_quotes: bool = True) -> RevenueResult:
    p = params or RevenueParams()
    as_of = _to_date(as_of)
    rev, loaded = load_revenue_months(as_of, p.months_to_load)
    coarse = coarse_revenue(rev, p)
    cands = coarse[coarse["pass_yoy"]].reset_index(drop=True)
    print(f"[月營收] 載入月份 {[(f'{y}/{m:02d}', n) for y, m, n in loaded]}")
    print(f"[月營收] 全市場 {len(coarse)} 檔 → 年增率條件 {len(cands)} 檔")

    notes, errors = [], {}
    if p.top_n > 0 and len(cands):
        detail = same_month_ranks(cands, p, progress_cb=progress_cb)
    else:
        detail = cands.assign(match=True)

    if detail.empty:
        hit = detail
    else:
        hit = detail[detail["match"]].copy()
    print(f"[月營收] 符合全部條件：{len(hit)} 檔")

    cols = {
        "代碼": hit.get("code"), "名稱": hit.get("name"), "市場": hit.get("market"),
        "營收月份": hit["latest_ym"].map(lambda v: f"{v // 100}/{v % 100:02d}") if len(hit) else None,
        "單月營收(億)": (hit["revenue"] / 1e8).round(2) if len(hit) else None,
        "月增(%)": hit.get("mom"), "年增(%)": hit.get("yoy_cur"),
    }
    for i in range(1, p.n_prev + 1):
        cols[f"前{i}月年增(%)"] = hit.get(f"yoy_prev{i}")
    if "same_month_rank" in hit:
        cols["同期排名"] = hit["same_month_rank"].astype(int).astype(str) + "/" + \
            hit["years_compared"].astype(int).astype(str)
    matches = pd.DataFrame(cols).reset_index(drop=True) if len(hit) else pd.DataFrame(columns=list(cols))

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
    rev, loaded = load_revenue_months(as_of, p.months_to_load)
    print("載入月份（年, 月, 公司數）：", loaded)
    coarse = coarse_revenue(rev, p).set_index("code")
    for code in codes:
        print(f"\n===== {code} =====")
        if code not in coarse.index:
            print("  ✗ 不在營收彙總表中")
            continue
        r = coarse.loc[code]
        lym = int(r["latest_ym"])
        print(f"  {r['name']}（{r['market']}） 最新營收月份 {lym // 100}/{lym % 100:02d}"
              f"  營收 {r['revenue'] / 1e8:.2f} 億")
        print(f"  {'✓' if r['yoy_cur'] > p.yoy_cur_min else '✗'} 1 當月年增: {r['yoy_cur']}")
        for i in range(1, p.n_prev + 1):
            v = r[f"yoy_prev{i}"]
            print(f"  {'✓' if v > p.yoy_prev_min else '✗'} {i + 1} 前{i}月年增"
                  f"（{_shift(lym, -i) // 100}/{_shift(lym, -i) % 100:02d}）: {v}")
        if pd.isna(r["revenue"]):
            print(f"  ✗ 6 歷年同期排名：尚未公告 {lym // 100}/{lym % 100:02d} 營收，無法比較")
            continue
        one = same_month_ranks(coarse.loc[[code]].reset_index(),
                               RevenueParams(top_n=999))          # 不提早停止，算出完整名次
        rk, yrs = int(one.at[0, "same_month_rank"]), int(one.at[0, "years_compared"])
        print(f"  {'✓' if rk <= p.top_n else '✗'} 6 歷年同期排名: {rk} / {yrs} 年")


def _main():
    ap = argparse.ArgumentParser(description="本機計算 Goodinfo「月營收選股03」")
    ap.add_argument("--date", help="基準日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--diag", help="逐條件診斷指定代碼，例如 --diag 2330,6488")
    ap.add_argument("--detail", help="輸出所有候選股明細 CSV")
    a = ap.parse_args()
    if a.diag:
        diagnose([c.strip() for c in a.diag.split(",") if c.strip()], a.date)
        return
    res = screen_revenue(a.date)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    print(res.matches.to_string(index=False) if not res.matches.empty else "（無符合股票）")
    for n in res.notes:
        print("注意：", n)
    if res.errors:
        print("細篩失敗：", res.errors)
    if a.detail and not res.detail.empty:
        res.detail.to_csv(a.detail, index=False, encoding="utf-8-sig")
        print(f"候選股明細 → {a.detail}")


if __name__ == "__main__":
    _main()
