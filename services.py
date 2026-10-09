# services.py
"""
選股流程的共用邏輯（不依賴 Streamlit）。

streamlit_app.py（網頁）和 mcp_server.py（AI 工具）都呼叫這裡，
篩選規則只寫一份，兩邊結果一定一致。

個股分析函式（analyze_fn）由呼叫端注入，各自決定快取方式：
    analyze_fn(code: str) -> dict   # 格式同 stock_analyzer.analyze_stock(with_chart=False)
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from concentration_1day import filter_stock_data
from local_screener import Screen103Params, ScreenerError, screen_103
from revenue_screener import RevenueParams, screen_revenue
from scrape_utils import ScrapeError, lkg_load, lkg_save

AnalyzeFn = Callable[[str], dict]
ProgressFn = Callable[[int, int, str], None]

MAX_WORKERS = 4                                   # FinMind 併發上限，避免限流
_COMMON_STOCK_RE = re.compile(r'^[1-9]\d{3}$')    # 普通股：4 碼、非 0 開頭

YAHOO_RANK_URL = {
    "上市": "https://tw.stock.yahoo.com/rank/change-up?exchange=TAI",
    "上櫃": "https://tw.stock.yahoo.com/rank/change-up?exchange=TWO",
}


def is_common_stock(code) -> bool:
    """排除 ETF／ETN／權證等（FinMind 常查無日線的商品）。"""
    return bool(_COMMON_STOCK_RE.match(str(code).strip()))


# ──────────────────────────────────────────────────────────────────────────────
# 上次成功結果（last-known-good）
# ──────────────────────────────────────────────────────────────────────────────
def with_lkg(key: str, fn, *args):
    """
    執行 fn(*args)；成功就存檔。抓取失敗（ScrapeError／ScreenerError）時改回傳上次成功結果。
    回傳 (data, stale)：stale 為 None 表示最新資料，否則為 (saved_at, 錯誤)。
    """
    try:
        data = fn(*args)
    except (ScrapeError, ScreenerError) as e:
        old = lkg_load(key)
        if old is None:
            raise
        return old[0], (old[1], e)
    lkg_save(key, data)
    return data, None


# ──────────────────────────────────────────────────────────────────────────────
# 個股批次分析
# ──────────────────────────────────────────────────────────────────────────────
def batch_analyze(codes, analyze_fn: AnalyzeFn, progress_cb: ProgressFn | None = None,
                  max_workers: int = MAX_WORKERS) -> dict[str, dict]:
    """並發分析多檔股票；自動去除空值與重複代碼。回傳 {code: result}。"""
    uniq = list(dict.fromkeys(str(c).strip() for c in codes
                              if c is not None and str(c).strip() not in ("", "nan")))
    results: dict[str, dict] = {}
    if not uniq:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(analyze_fn, c): c for c in uniq}
        for i, f in enumerate(as_completed(futs), 1):
            code = futs[f]
            try:
                results[code] = f.result()
            except Exception as e:                # noqa: BLE001
                results[code] = {'status': 'error', 'error_type': 'unknown', 'message': str(e)}
            if progress_cb:
                progress_cb(i, len(uniq), code)
    return results


def indicator_values(result: dict) -> dict:
    """analyze 結果 → {'k', 'd', 'i_value', 'avg_vol_5'}；失敗時為空 dict。"""
    return result.get('indicators', {}) if result.get('status') == 'success' else {}


def is_no_data(result: dict) -> bool:
    return result.get('error_type') == 'no_data'


# ──────────────────────────────────────────────────────────────────────────────
# 1日籌碼集中度
# ──────────────────────────────────────────────────────────────────────────────
CONCENTRATION_RULES = ("5日集中度 > 10日集中度", "10日集中度 > 20日集中度",
                       "5日與10日集中度皆 > 0")


def filter_concentration(raw: pd.DataFrame, min_volume: int) -> pd.DataFrame:
    """套集中度條件＋10日均量門檻，只留普通股。欄位異常時 raise ValueError。"""
    df = filter_stock_data(raw, min_volume=min_volume)
    if df is None:
        raise ValueError("籌碼集中度資料欄位異常，無法篩選（來源網頁可能改版）")
    return df[df['代碼'].map(is_common_stock)].copy()


# ──────────────────────────────────────────────────────────────────────────────
# 漲幅排行榜
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class RankingResult:
    n_prelim: int = 0                                 # 通過股價／漲幅條件的檔數（已排除非普通股）
    passed: list = field(default_factory=list)        # 通過量能條件：{stock_info, indicators, estimated_volume_lots, avg_vol_5_lots}
    errors: list = field(default_factory=list)        # 分析失敗：{stock_info, error, error_type}
    no_data: list = field(default_factory=list)       # FinMind 查無資料：['名稱(代碼)']
    excluded: list = field(default_factory=list)      # 非普通股：['名稱(代碼)']


def prelim_ranking(df: pd.DataFrame, min_price: float, min_change: float
                   ) -> tuple[pd.DataFrame, list[str]]:
    """股價、漲幅初篩，並排除非普通股。回傳 (通過者, 被排除的非普通股)。"""
    df = df.copy()
    for col in ['Price', 'Change Percent', 'Estimated Volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df[(df['Price'] > min_price) & (df['Change Percent'] > min_change)]
    df = df.dropna(subset=['Price', 'Change Percent', 'Estimated Volume'])
    common = df['Stock Symbol'].map(is_common_stock)
    excluded = [f"{n}({c})" for c, n in zip(df.loc[~common, 'Stock Symbol'], df.loc[~common, 'Stock Name'])]
    return df[common].copy(), excluded


def screen_ranking(df: pd.DataFrame, analyze_fn: AnalyzeFn, min_price: float, min_change: float,
                   vol_ratio: float, progress_cb: ProgressFn | None = None) -> RankingResult:
    """排行榜三條件：成交價 > min_price、漲幅 > min_change、預估量 > vol_ratio × 前5日均量。"""
    out = RankingResult()
    if df is None or df.empty:
        return out
    pre, out.excluded = prelim_ranking(df, min_price, min_change)
    out.n_prelim = len(pre)
    cache = batch_analyze(pre['Stock Symbol'].astype(str).tolist(), analyze_fn, progress_cb)
    for info in pre.to_dict('records'):
        code = str(info['Stock Symbol']).strip()
        res = cache.get(code, {})
        if res.get('status') == 'success':
            ind = res.get('indicators', {})
            avg5_lots = (ind.get('avg_vol_5') or 0) / 1000
            est = info.get('Estimated Volume')
            if pd.notna(est) and avg5_lots > 0 and est > vol_ratio * avg5_lots:
                out.passed.append({'stock_info': info, 'indicators': ind,
                                   'estimated_volume_lots': est, 'avg_vol_5_lots': avg5_lots})
        elif is_no_data(res):
            out.no_data.append(f"{info.get('Stock Name', '')}({code})")
        else:
            out.errors.append({'stock_info': info, 'error': res.get('message', '未知錯誤'),
                               'error_type': res.get('error_type', 'unknown')})
    rank = lambda r: r['stock_info'].get('Rank') if pd.notna(r['stock_info'].get('Rank')) else 999  # noqa: E731
    out.passed.sort(key=rank)
    out.errors.sort(key=rank)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 我的選股103／月營收選股（本機計算）
# ──────────────────────────────────────────────────────────────────────────────
def run_screen_103(params: dict) -> dict:
    """失敗時 raise ScreenerError（不回傳 None，避免快取住失敗結果）。"""
    res = screen_103(params=Screen103Params(**params))
    n_cand = len(res.detail) + len(res.errors)
    if n_cand and len(res.errors) * 2 > n_cand:
        raise ScreenerError(
            f"細篩有 {len(res.errors)}/{n_cand} 檔抓不到日線，可能是 FinMind 限流，請稍後再試")
    return {
        'matches': res.matches, 'trade_date': res.trade_date, 'prev_date': res.prev_date,
        'source': res.source, 'universe_size': res.universe_size, 'n_candidates': n_cand,
        'errors': res.errors, 'notes': res.notes,
        'histories': res.histories,          # 符合者的日線：算 I 值、畫圖都用它，不再重抓
    }


def run_screen_revenue(params: dict) -> dict:
    res = screen_revenue(params=RevenueParams(**params))
    n_cand = len(res.detail) + len(res.errors)
    if n_cand and len(res.errors) * 2 > n_cand:
        raise ScreenerError(
            f"歷年同期比較有 {len(res.errors)}/{n_cand} 檔抓不到公開資訊觀測站月營收，請稍後再試")
    return {
        'matches': res.matches, 'months_loaded': res.months_loaded,
        'universe_size': res.universe_size, 'n_candidates': n_cand,
        'errors': res.errors, 'notes': res.notes,
    }
