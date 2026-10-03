# mcp_server.py
"""
台股分析儀 MCP Server（本機 stdio）——讓 Antigravity 等 MCP 客戶端呼叫既有選股／分析功能。

- 只 import 既有模組，不修改 streamlit_app.py；Streamlit Cloud 只跑 streamlit_app.py，不會執行本檔。
- 安裝：pip install -r requirements-mcp.txt（不要把 mcp 加進 requirements.txt）
- 執行：通常由 Antigravity 依 mcp_config.json 自動啟動；手動測試可用
        npx @modelcontextprotocol/inspector python mcp_server.py
"""
from __future__ import annotations

import sys

# stdio 模式下 stdout 專用於 MCP 協定訊息。既有模組會 print 進度，
# 所以先保留真正的 stdout 給協定使用，其餘 print 一律導到 stderr（Antigravity 的連線 log 看得到）。
_PROTOCOL_STDOUT = sys.stdout
sys.stdout = sys.stderr

import json
import os
import re
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "mcp_output"          # 個股圖表 HTML 輸出（已列入 .gitignore）
TZ = ZoneInfo("Asia/Taipei")


def _load_finmind_token() -> None:
    """環境變數優先；否則讀 .streamlit/secrets.toml（與 Streamlit 本機執行共用同一份）。"""
    if os.getenv("FINMIND_API_TOKEN"):
        return
    p = ROOT / ".streamlit" / "secrets.toml"
    if p.exists():
        m = re.search(r'^\s*FINMIND_API_TOKEN\s*=\s*["\']([^"\']+)["\']',
                      p.read_text(encoding="utf-8"), re.M)
        if m:
            os.environ["FINMIND_API_TOKEN"] = m.group(1)


_load_finmind_token()

import anyio
import pandas as pd
import twstock
from mcp.server.fastmcp import FastMCP

import services
from local_screener import Screen103Params
from revenue_screener import RevenueParams
from yahoo_scraper import scrape_yahoo_stock_rankings
from stock_analyzer import analyze_stock, build_chart, fetch_price_history, AnalysisError
from stock_information_plot import (plot_stock_revenue_trend, plot_stock_major_shareholders,
                                    get_stock_code)
from concentration_1day import fetch_stock_concentration_data
from scrape_utils import lkg_key

mcp = FastMCP(
    "tw-stock-analyzer",
    instructions=(
        "台股分析儀工具。選股工具會抓證交所／櫃買／FinMind／Yahoo 等外部資料，"
        "可能需要 30 秒到數分鐘；FinMind 有限流，請勿短時間內重複呼叫同一選股工具。"
        "回傳中的 K、D 為日 KD，I 為 I 值（1 = 偏多訊號）。"
        "stale 欄位不為空時，表示即時抓取失敗、改用上次成功的結果。"
    ),
)

# ──────────────────────────────────────────────────────────────────────────────
# 共用輔助
# ──────────────────────────────────────────────────────────────────────────────
_PRICE_TTL = 3600
_price_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_cache_lock = threading.Lock()


def _records(df: pd.DataFrame | None) -> list[dict]:
    """DataFrame → JSON 安全的 list[dict]（處理 NaN、numpy 型別、日期）。"""
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def _num(v, nd: int = 2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


def _price_history(code: str) -> pd.DataFrame:
    """日線加 1 小時記憶體快取（只快取成功結果）；指標與圖表都從它現算，降低 FinMind 用量。"""
    now = time.time()
    with _cache_lock:
        hit = _price_cache.get(code)
        if hit and now - hit[0] < _PRICE_TTL:
            return hit[1]
    df = fetch_price_history(code)
    with _cache_lock:
        _price_cache[code] = (now, df)
    return df


def _analyze(code: str, price_data: pd.DataFrame | None = None) -> dict:
    """只算指標、不畫圖（格式同 analyze_stock(with_chart=False)）。"""
    if price_data is None:
        try:
            price_data = _price_history(code)
        except AnalysisError as e:
            return {"status": "error", "error_type": e.kind, "message": str(e)}
    return analyze_stock(code, with_chart=False, price_data=price_data)


def _kdi(result: dict) -> dict:
    if result.get("status") != "success":
        return {"K": None, "D": None, "I": None,
                "analysis_error": result.get("message", "分析失敗")}
    ind = result.get("indicators", {})
    return {"K": _num(ind.get("k")), "D": _num(ind.get("d")), "I": _num(ind.get("i_value"), 0)}


def _with_lkg(key: str, fn, *args):
    """services.with_lkg 的包裝：stale 轉成給 AI 看的一句說明。"""
    data, stale = services.with_lkg(key, fn, *args)
    note = (f"即時抓取失敗（{stale[1]}），改用 {stale[0]:%Y/%m/%d %H:%M} 的上次成功結果"
            if stale else None)
    return data, note


async def _in_thread(fn, *args):
    """耗時的同步爬蟲放到背景執行緒，避免卡住 MCP 事件迴圈。"""
    return await anyio.to_thread.run_sync(lambda: fn(*args))


def _now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────────────────────────────────────
# 工具 1：1日籌碼集中度選股
# ──────────────────────────────────────────────────────────────────────────────
def _concentration(min_avg_volume: int, with_kd: bool) -> dict:
    raw, stale = _with_lkg("concentration", fetch_stock_concentration_data)
    rows = _records(services.filter_concentration(raw, min_avg_volume))
    skipped: list[str] = []
    if with_kd and rows:
        cache = services.batch_analyze([r["代碼"] for r in rows], _analyze)
        kept = []
        for r in rows:
            res = cache.get(str(r["代碼"]).strip(), {})
            if services.is_no_data(res):
                skipped.append(f"{r.get('股票名稱', '')}({r['代碼']})")
                continue
            r.update(_kdi(res))
            kept.append(r)
        rows = kept
    return {
        "queried_at": _now(),
        "criteria": "、".join(services.CONCENTRATION_RULES) + f"、10日均量 > {min_avg_volume} 張、僅普通股",
        "count": len(rows),
        "stocks": rows,
        "skipped_no_finmind_data": skipped,
        "stale": stale,
    }


@mcp.tool()
async def concentration_pick(min_avg_volume: int = 2000, with_kd: bool = True) -> dict:
    """1日籌碼集中度選股：5日集中度 > 10日 > 20日（皆 > 0）且 10日均量大於門檻的普通股。

    Args:
        min_avg_volume: 10日均量下限（張），預設 2000。
        with_kd: 是否逐檔計算日 K、D 與 I 值（會呼叫 FinMind，較慢）。
    """
    return await _in_thread(_concentration, min_avg_volume, with_kd)


# ──────────────────────────────────────────────────────────────────────────────
# 工具 2：我的選股103（本機計算）
# ──────────────────────────────────────────────────────────────────────────────
def _run_103(params: dict) -> dict:
    res, stale = _with_lkg(lkg_key("screen103", params), services.run_screen_103, params)
    stocks = _records(res["matches"])
    # 細篩已抓過符合者的日線，直接算 I 值，不再呼叫 FinMind
    histories = res.get("histories") or {}
    for r in stocks:
        code = str(r.get("代碼", "")).strip()
        r["I"] = _kdi(_analyze(code, histories.get(code)))["I"] if code in histories else None
    return {
        "queried_at": _now(),
        "trade_date": str(res["trade_date"]),
        "prev_date": str(res["prev_date"]),
        "source": res["source"],
        "params": params,
        "universe_size": res["universe_size"],
        "n_candidates": res["n_candidates"],
        "count": len(stocks),
        "stocks": stocks,
        "fetch_errors": res["errors"],
        "notes": res["notes"],
        "stale": stale,
    }


@mcp.tool()
async def my_stock_103(
    red_k_min: float = 2.5,
    red_k_max: float = 10.0,
    red_k_base: Literal["prev_close", "open"] = "prev_close",
    vol_min: float = 5000,
    ma60_dev_min: float = -5.0,
    ma60_dev_max: float = 5.0,
    wk_max: float = 50.0,
    vol_ratio: float = 1.3,
) -> dict:
    """我的選股103（本機計算，等同 Goodinfo 我的選股103 的 8 個條件）。

    條件：紅K棒幅、成交張數、季線乖離、週K值 ≤ 上限且向上、MA20 < MA60、日K > 日D、今日量 > 倍數 × 昨日量。
    粗篩用證交所＋櫃買行情，細篩逐檔抓 FinMind 日線，約需 1～3 分鐘。

    Args:
        red_k_min: 紅K棒幅下限（%）。
        red_k_max: 紅K棒幅上限（%）。
        red_k_base: 棒幅分母，prev_close＝昨收、open＝開盤價。
        vol_min: 最低成交張數。
        ma60_dev_min: 季線乖離下限（%）。
        ma60_dev_max: 季線乖離上限（%）。
        wk_max: 週K值上限。
        vol_ratio: 量增倍數（今日量 / 昨日量）。
    """
    params = asdict(Screen103Params(
        red_k_min=red_k_min, red_k_max=red_k_max, red_k_base=red_k_base, vol_min=float(vol_min),
        ma60_dev_min=ma60_dev_min, ma60_dev_max=ma60_dev_max, wk_max=float(wk_max), vol_ratio=vol_ratio,
    ))
    return await _in_thread(_run_103, params)


# ──────────────────────────────────────────────────────────────────────────────
# 工具 3：月營收選股（本機計算）
# ──────────────────────────────────────────────────────────────────────────────
def _run_rev(params: dict) -> dict:
    res, stale = _with_lkg(lkg_key("screen_revenue", params), services.run_screen_revenue, params)
    return {
        "queried_at": _now(),
        "params": params,
        "months_loaded": [list(m) for m in res["months_loaded"]],
        "universe_size": res["universe_size"],
        "n_candidates": res["n_candidates"],
        "count": len(res["matches"]),
        "stocks": _records(res["matches"]),
        "fetch_errors": res["errors"],
        "notes": res["notes"],
        "stale": stale,
    }


@mcp.tool()
async def monthly_revenue_pick(
    yoy_cur_min: float = 15.0,
    yoy_prev_min: float = 10.0,
    n_prev: int = 4,
    top_n: int = 3,
    per_company_month: bool = False,
) -> dict:
    """月營收選股（本機計算，等同 Goodinfo 月營收選股03）。

    Args:
        yoy_cur_min: 當月營收年增率下限（%）。
        yoy_prev_min: 前幾個月年增率下限（%）。
        n_prev: 連續檢查前幾個月（1～5）。
        top_n: 當月營收須創歷年同期前 N 高（0 = 不檢查）。
        per_company_month: True＝各公司以自己最新公告月份為當月；False（同 Goodinfo）＝全市場統一以最新公告月份為當月。
    """
    params = asdict(RevenueParams(
        yoy_cur_min=float(yoy_cur_min), yoy_prev_min=float(yoy_prev_min), n_prev=n_prev,
        top_n=top_n, months_to_load=max(6, n_prev + 2), per_company_month=per_company_month,
    ))
    return await _in_thread(_run_rev, params)


# ──────────────────────────────────────────────────────────────────────────────
# 工具 4、5：漲幅排行榜（上市／上櫃）
# ──────────────────────────────────────────────────────────────────────────────
def _ranking(market: str, min_price: float, min_change: float, vol_ratio: float) -> dict:
    key = "yahoo_rank_otc" if market == "上櫃" else "yahoo_rank_listed"
    df, stale = _with_lkg(key, scrape_yahoo_stock_rankings, services.YAHOO_RANK_URL[market])
    notes = []
    if not stale and df.attrs.get("parser") == "fallback":
        notes.append("Yahoo 主要解析方式失效，已改用備援解析；網頁可能改版，數字請再核對")
    res = services.screen_ranking(df, _analyze, min_price, min_change, vol_ratio)
    if res.excluded:
        notes.append("已排除 ETF／ETN 等非普通股：" + "、".join(res.excluded))
    passed = []
    for r in res.passed:
        info, est, avg5 = r["stock_info"], r["estimated_volume_lots"], r["avg_vol_5_lots"]
        passed.append({
            "排名": info.get("Rank"), "代碼": str(info["Stock Symbol"]).strip(), "名稱": info.get("Stock Name"),
            "成交價": _num(info.get("Price")), "漲跌幅%": _num(info.get("Change Percent")),
            "預估量(張)": _num(est, 0), "5日均量(張)": _num(avg5, 0), "量比": _num(est / avg5),
            "預估量因子": _num(info.get("Factor")),
            **_kdi({"status": "success", "indicators": r["indicators"]}),
        })
    return {
        "queried_at": _now(),
        "market": market,
        "criteria": f"成交價 > {min_price}、漲幅 > {min_change}%、預估成交量 > {vol_ratio} 倍前5日均量、僅普通股",
        "n_after_price_change_filter": res.n_prelim,
        "count": len(passed),
        "stocks": passed,
        "analysis_errors": [{"代碼": str(e["stock_info"]["Stock Symbol"]), "名稱": e["stock_info"].get("Stock Name"),
                             "error_type": e["error_type"], "error": e["error"]} for e in res.errors],
        "skipped_no_finmind_data": res.no_data,
        "notes": notes,
        "stale": stale,
    }


@mcp.tool()
async def gain_ranking(
    market: Literal["上市", "上櫃"] = "上市",
    min_price: float = 35,
    min_change: float = 2.0,
    vol_ratio: float = 2.0,
) -> dict:
    """盤中即時漲幅排行榜（Yahoo 股市）再加上量能篩選，回傳通過者與其日 KD、I 值。

    Args:
        market: 上市 或 上櫃。
        min_price: 最低股價（元）。
        min_change: 最低漲幅（%）。
        vol_ratio: 預估成交量須大於前 5 日均量的倍數。
    """
    return await _in_thread(_ranking, market, min_price, min_change, vol_ratio)


# ──────────────────────────────────────────────────────────────────────────────
# 工具 6：個股分析
# ──────────────────────────────────────────────────────────────────────────────
def _save_fig(fig, path: Path) -> str:
    """存成可離線開啟的 HTML：plotly.min.js 只在 mcp_output/ 放一份，各圖以相對路徑引用（不靠 CDN）。"""
    OUT_DIR.mkdir(exist_ok=True)
    fig.write_html(path, include_plotlyjs="directory", full_html=True)
    return str(path)


def _single(stock: str, save_charts: bool) -> dict:
    code = get_stock_code(stock)
    if not code:
        raise ValueError(f"找不到股票「{stock}」，請輸入代碼（如 2330）或名稱（如 台積電）")
    info = twstock.codes.get(code)
    name = info.name if info else code
    out: dict = {"queried_at": _now(), "code": code, "name": name}

    res = _analyze(code)
    out.update(_kdi(res))
    if res.get("status") == "success":
        avg5 = res.get("indicators", {}).get("avg_vol_5")
        out["5日均量(張)"] = _num(avg5 / 1000, 0) if avg5 else None
    else:
        out["error_type"] = res.get("error_type")

    if save_charts:
        stamp = datetime.now(TZ).strftime("%Y%m%d")
        charts, chart_errors = {}, {}
        if res.get("status") == "success":
            try:     # 用同一份快取日線建圖，不重抓
                fig = build_chart(code, _price_history(code))
                charts["技術分析"] = _save_fig(fig, OUT_DIR / f"{code}_{stamp}_tech.html")
            except Exception as e:              # noqa: BLE001
                chart_errors["技術分析"] = str(e)
        else:
            chart_errors["技術分析"] = res.get("message", "分析失敗")
        for label, slug, fn in (("月營收趨勢", "revenue", plot_stock_revenue_trend),
                                ("大戶股權變化", "holders", plot_stock_major_shareholders)):
            try:
                fig, err = fn(code)
            except Exception as e:             # noqa: BLE001
                fig, err = None, str(e)
            if fig is not None and not err:
                charts[label] = _save_fig(fig, OUT_DIR / f"{code}_{stamp}_{slug}.html")
            else:
                chart_errors[label] = err
        out["chart_files"] = charts
        out["open_hint"] = "在檔案總管雙擊 HTML 即可用瀏覽器開啟；或在 cmd 執行 start \"\" \"<檔案路徑>\""
        out["chart_errors"] = chart_errors
    return out


@mcp.tool()
async def stock_analysis(stock: str, save_charts: bool = True) -> dict:
    """個股分析：回傳日 K、D、I 值與 5 日均量；可另存技術分析、月營收趨勢、大戶股權變化三張互動圖（HTML）。

    Args:
        stock: 股票代碼或名稱，例如 "2330" 或 "台積電"。
        save_charts: 是否把三張圖存成 HTML 到專案的 mcp_output/ 資料夾，並回傳檔案路徑。
    """
    return await _in_thread(_single, stock, save_charts)


# ──────────────────────────────────────────────────────────────────────────────
# 啟動（stdio）
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    from io import TextIOWrapper
    from mcp.server.stdio import stdio_server

    async def _run():
        out = anyio.wrap_file(TextIOWrapper(_PROTOCOL_STDOUT.buffer, encoding="utf-8"))
        async with stdio_server(stdout=out) as (read_stream, write_stream):
            server = mcp._mcp_server
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(_run)


if __name__ == "__main__":
    main()
