"""mcp_output/ 自動清理規則。"""
import os
import time
from datetime import date

from output_retention import plan_prune, prune_outputs

TODAY = date(2026, 11, 3)


def _touch(d, name, age_days=0.0):
    p = d / name
    p.write_text("x", encoding="utf-8")
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    return p


def _names(paths):
    return sorted(p.name for p in paths)


def test_chart_keeps_only_newest_per_stock_and_kind(tmp_path):
    for day in ("20261020", "20261101"):
        for kind in ("tech", "revenue", "holders"):
            _touch(tmp_path, f"2330_{day}_{kind}.html")
    _touch(tmp_path, "2344_20261020_tech.html")            # 別檔股票：只有一份，保留
    doomed = plan_prune(tmp_path, keep_days=30, today=TODAY)
    assert _names(doomed) == ["2330_20261020_holders.html", "2330_20261020_revenue.html",
                              "2330_20261020_tech.html"]


def test_chart_older_than_keep_days_removed_even_if_only_copy(tmp_path):
    _touch(tmp_path, "2344_20261001_tech.html")             # 33 天前
    _touch(tmp_path, "2344_20261010_revenue.html")          # 24 天前
    assert _names(plan_prune(tmp_path, keep_days=30, today=TODAY)) == ["2344_20261001_tech.html"]
    assert plan_prune(tmp_path, keep_days=0, today=TODAY) == []       # 0＝不依天數刪


def test_legacy_chinese_names_count_as_same_kind(tmp_path):
    _touch(tmp_path, "2330_20261003_技術分析.html")
    _touch(tmp_path, "2330_20261003_tech.html")
    _touch(tmp_path, "2330_20261003_月營收趨勢.html")
    _touch(tmp_path, "2330_20261011_revenue.html")
    doomed = plan_prune(tmp_path, keep_days=60, today=TODAY)
    assert _names(doomed) == ["2330_20261003_技術分析.html", "2330_20261003_月營收趨勢.html"]


def test_pdf_duplicates_keep_newest_no_age_limit(tmp_path):
    _touch(tmp_path, "3037_法說會_2026-07-29_303720260730M001.pdf", age_days=90)
    _touch(tmp_path, "3037_法說會_2026-09-15_303720260730M001.pdf", age_days=40)
    keep = _touch(tmp_path, "3037_法說會_2026-09-16_303720260730M001.pdf", age_days=39)
    old_single = _touch(tmp_path, "3711_法說會_2026-04-29_371120260429M001.pdf", age_days=200)
    doomed = plan_prune(tmp_path, keep_days=30, today=TODAY)
    assert _names(doomed) == ["3037_法說會_2026-07-29_303720260730M001.pdf",
                              "3037_法說會_2026-09-15_303720260730M001.pdf"]
    assert keep not in doomed and old_single not in doomed


def test_debug_files_and_untouchables(tmp_path):
    _touch(tmp_path, "_mops_t100sb02_2330_a_mops.html", age_days=8)
    fresh = _touch(tmp_path, "_mops_t100sb02_2330_b_mops.html", age_days=1)
    _touch(tmp_path, "plotly.min.js", age_days=400)
    (tmp_path / "backtest_103").mkdir()
    _touch(tmp_path / "backtest_103", "2330_20200101_tech.html", age_days=400)   # 子資料夾不掃
    doomed = plan_prune(tmp_path, keep_days=30, today=TODAY)
    assert _names(doomed) == ["_mops_t100sb02_2330_a_mops.html"]
    assert fresh.exists()


def test_prune_dry_run_and_delete(tmp_path):
    _touch(tmp_path, "2330_20261020_tech.html")
    _touch(tmp_path, "2330_20261101_tech.html")
    assert len(prune_outputs(tmp_path, keep_days=0, dry_run=True)) == 1
    assert (tmp_path / "2330_20261020_tech.html").exists()
    deleted = prune_outputs(tmp_path, keep_days=0)
    assert _names(deleted) == ["2330_20261020_tech.html"]
    assert not (tmp_path / "2330_20261020_tech.html").exists()


def test_missing_dir_is_noop(tmp_path):
    assert prune_outputs(tmp_path / "nope") == []
