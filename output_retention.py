# output_retention.py
"""
mcp_output/ 自動清理（MCP Server 啟動時與每次存檔後執行；只用標準函式庫）。

規則：
- 個股圖表 HTML（<代碼>_<YYYYMMDD>_<種類>.html）：同一檔、同一種圖只留最新日期那份；
  另外超過 keep_days 天（預設 30，環境變數 MCP_OUTPUT_KEEP_DAYS，0＝不依天數刪）的一律刪除。
  舊版中文檔名（技術分析／月營收趨勢／大戶股權變化）視同 tech／revenue／holders。
- 法說會簡報 PDF（<代碼>_法說會_<日期>_<簡報檔>.pdf）：不設期限；同一份簡報檔重複下載的只留最新一份。
- 除錯用 _mops_*.html：超過 7 天刪除。
- 其他檔案與子資料夾（backtest_103/、plotly.min.js …）一律不動。

手動執行：
    python output_retention.py --dry-run    # 只列出會刪哪些
    python output_retention.py              # 實際刪除
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

DEFAULT_KEEP_DAYS = 30
DEBUG_KEEP_DAYS = 7

_CHART = re.compile(r"^(?P<code>\d{4,6})_(?P<day>\d{8})_(?P<kind>[^_]+)\.html$")
_PDF = re.compile(r"^(?P<code>\d{4,6})_法說會_(?P<day>[^_]+)_(?P<deck>.+\.pdf)$", re.I)
_DEBUG = re.compile(r"^_mops_.*\.html$")
_KIND_ALIAS = {"技術分析": "tech", "月營收趨勢": "revenue", "大戶股權變化": "holders"}


def _keep_days_from_env() -> int:
    try:
        return max(0, int(os.getenv("MCP_OUTPUT_KEEP_DAYS", DEFAULT_KEEP_DAYS)))
    except ValueError:
        return DEFAULT_KEEP_DAYS


def plan_prune(out_dir: Path, keep_days: int | None = None, today: date | None = None,
               now: float | None = None) -> list[Path]:
    """回傳應刪除的檔案（不實際刪除）。"""
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return []
    keep_days = _keep_days_from_env() if keep_days is None else keep_days
    today = today or date.today()
    now = now if now is not None else time.time()

    files = [p for p in out_dir.iterdir() if p.is_file()]
    doomed: set[Path] = set()

    # 個股圖表
    newest: dict[tuple[str, str], str] = {}
    charts = []
    for p in files:
        m = _CHART.match(p.name)
        if not m:
            continue
        try:
            datetime.strptime(m["day"], "%Y%m%d")
        except ValueError:
            continue
        key = (m["code"], _KIND_ALIAS.get(m["kind"], m["kind"]))
        charts.append((p, key, m["day"]))
        if m["day"] > newest.get(key, ""):
            newest[key] = m["day"]
    for p, key, day in charts:
        too_old = keep_days > 0 and (today - datetime.strptime(day, "%Y%m%d").date()).days > keep_days
        if day < newest[key] or too_old:
            doomed.add(p)
    # 同一天、同種類卻有新舊兩種檔名（中文舊名＋英文新名）：留英文新名
    by_key_day: dict[tuple, list[Path]] = {}
    for p, key, day in charts:
        if p not in doomed:
            by_key_day.setdefault((key, day), []).append(p)
    for group in by_key_day.values():
        if len(group) > 1:
            group.sort(key=lambda q: (_CHART.match(q.name)["kind"] in _KIND_ALIAS, q.name))
            doomed.update(group[1:])

    # 法說會 PDF：同一份簡報檔只留最新（修改時間）一份
    decks: dict[tuple[str, str], list[Path]] = {}
    for p in files:
        m = _PDF.match(p.name)
        if m:
            decks.setdefault((m["code"], m["deck"].lower()), []).append(p)
    for group in decks.values():
        if len(group) > 1:
            group.sort(key=lambda q: q.stat().st_mtime, reverse=True)
            doomed.update(group[1:])

    # 除錯檔
    for p in files:
        if _DEBUG.match(p.name) and now - p.stat().st_mtime > DEBUG_KEEP_DAYS * 86400:
            doomed.add(p)

    return sorted(doomed)


def prune_outputs(out_dir: Path, keep_days: int | None = None, dry_run: bool = False) -> list[Path]:
    """依規則清理 out_dir；回傳已刪除（dry_run 時為「將刪除」）的檔案。任何錯誤都不往外拋。"""
    try:
        doomed = plan_prune(out_dir, keep_days)
    except Exception as e:                      # noqa: BLE001  清理失敗不影響主流程
        print(f"[retention] 無法掃描 {out_dir}: {e}", file=sys.stderr)
        return []
    if dry_run:
        return doomed
    deleted = []
    for p in doomed:
        try:
            p.unlink()
            deleted.append(p)
        except OSError as e:
            print(f"[retention] 無法刪除 {p.name}: {e}", file=sys.stderr)
    return deleted


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    target = Path(__file__).resolve().parent / "mcp_output"
    result = prune_outputs(target, dry_run=dry)
    size = sum(p.stat().st_size for p in result if p.exists()) if dry else None
    print(("將刪除" if dry else "已刪除") + f" {len(result)} 個檔案"
          + (f"（約 {size / 1e6:.1f} MB）" if size is not None else ""))
    for p in result:
        print("  " + p.name)
