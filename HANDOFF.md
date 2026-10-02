# HANDOFF：my_project_streamlit「我的選股」Cookie 被擋問題

> 來源：claude.ai 對話（2026-10-02），移至 Cowork 繼續開發
> Repo：https://github.com/yjc5760/my_project_streamlit
> 部署：Streamlit Cloud（美國機房 IP）

---

## 1. 問題

「我的選股 (Goodinfo)」功能（`scraper.py` → `scrape_goodinfo()`）經常因為 Cookie 變更而被擋，抓不到 Goodinfo「我的選股103」的資料。

## 2. 根因（已確認）

- 2026-09 起，Goodinfo 在原本的 `CLIENT_KEY` cookie 機制之外，**另外加了 Cloudflare JS Challenge**（回應為 403 `Just a moment...`）。
  參考：https://github.com/JacobHsu/goodinfo-xd-xr/issues/1
- 純 `requests` 不執行 JS，算不出 `cf_clearance`。curl_cffi 與 Playwright 也都被回報失敗。
- `cf_clearance` 綁定取得時的 IP、UA 和指紋，所以從台灣家用瀏覽器複製的 Cookie，貼到 Streamlit Cloud（美國機房 IP）後無效。
- **結論：「更新 Cookie」在雲端部署的架構下無解，必須改架構。**

## 3. 解法方案（推薦順序）

### 方案 A（推薦）：自行計算「選股103」，完全不依賴 Goodinfo

`scraper.py` 的 URL 已經包含全部 8 個條件：

| # | 條件 | 需要的資料 |
|---|---|---|
| 1 | 紅K棒幅 2.5%–10% | 今日 OHLC + 昨收 |
| 2 | 成交張數 5,000–900,000 | 今日量 |
| 3 | 季線乖離 -5% ~ +5% | MA60 |
| 4 | 週K值 0–50 | 週線 KD |
| 5 | 週K值向上 | 週線 KD |
| 6 | 月/季線空頭排列（MA20 < MA60） | MA20、MA60 |
| 7 | 日K > 日D | 日線 KD |
| 8 | 今日量 > 1.3 × 昨日量 | 兩日量 |

做法分兩階段：

1. **粗篩**：用 TWSE / TPEx OpenAPI 抓當日與前一日的全市場資料，套條件 1、2、8，把範圍縮到幾十檔。
2. **細篩**：把候選股丟進現有的 `stock_analyzer.analyze_stock()`（已經會從 FinMind 抓 300 天日線），再判斷條件 3–7。

注意事項：
- 紅K棒幅的分母（昨收或開盤）、KD 參數（9 期）與初始值，都要和 Goodinfo 對照校正。
- 若 TWSE 擋雲端 IP，粗篩改用 FinMind 的全市場日資料。需先確認方案權限。
- 可以重用既有的「我的選股103」回測框架裡的條件邏輯。
- 條件可以參數化，接到側邊欄滑桿。

核心判斷草稿：

```python
def match_103(d, w):          # d: 日線, w: 週線（皆含 k, d 欄）
    t, y = d.iloc[-1], d.iloc[-2]
    ma20 = d.close.rolling(20).mean().iloc[-1]
    ma60 = d.close.rolling(60).mean().iloc[-1]
    red_k = (t.close - t.open) / y.close * 100   # 棒幅定義需與 Goodinfo 對照
    return all([
        2.5 <= red_k <= 10,
        5000 <= t.vol_lots <= 900000,
        -5 <= (t.close / ma60 - 1) * 100 <= 5,
        0 <= w.k.iloc[-1] <= 50 and w.k.iloc[-1] > w.k.iloc[-2],
        ma20 < ma60,
        t.k > t.d,
        t.vol_lots > 1.3 * y.vol_lots,
    ])
```

### 方案 B：本機排程爬取，雲端只讀檔

在家用電腦（台灣 IP、真實瀏覽器）上，收盤後由排程抓取資料並存成 CSV，再推到 repo 的 `data/` 或 Google Drive。Streamlit 只讀最新的檔案。
不建議以繞過 Cloudflare 偵測作為主要路線：這是一場軍備競賽，也落在使用條款的灰色地帶，而且 headless 模式無法通過驗證。

### 方案 C：手動備援

加一個 `st.file_uploader`，上傳從 Goodinfo 手動匯出的檔案。上傳後的 KD 與 I 值分析流程照舊。

## 4. 程式碼問題清單（不論採用哪個方案都該修）

1. **[Bug] 失敗結果會被快取**：`scrape_goodinfo()` 失敗時回傳 `None`，這個 `None` 會被 `@st.cache_data` 快取住（盤後 TTL 長達 1 小時），導致更新 Cookie 後畫面仍然失敗。
   修法：失敗時改成 `raise` 例外，或在結果為 `None` 時呼叫 `cached_scrape_goodinfo.clear()`。
2. **錯誤分類**：依回應內容判斷失敗原因，分別顯示對應訊息：
   - 出現 `Just a moment` → 被 Cloudflare 擋下
   - 被導回首頁 → Cookie 失效
   - 找不到 `#tblStockList` → 網頁改版
3. **側邊欄燈號**：目前只檢查 Cookie 是否已設定，沒有檢查是否可用。建議加一個「測試連線」按鈕。
4. **Last-known-good fallback**：抓取成功時存檔；失敗時顯示舊資料，並標註資料日期。
5. **`.gitignore` 沒有生效**：檔名是 `gitignore.txt`，所以 `__pycache__/` 被 commit 進去了。
   修法：改名為 `.gitignore`，再執行 `git rm -r --cached __pycache__`。
6. **重複程式碼**：`scraper.py` 與 `monthly_revenue_scraper.py` 高度重複。可以合併成 `goodinfo_client.py`，兩組 Cookie secret 也一併整併。月營收選股之後同樣會遇到 Cloudflare，可改用 FinMind 的 `TaiwanStockMonthRevenue` 自行計算。
7. **檔案過大**：`streamlit_app.py` 約 1,400 行，建議改成 `pages/` 多頁架構。
8. **README 格式**：清單格式錯亂（出現 `- -` 巢狀），需要修正。

## 5. 下一步（待 YJ 決定）

- [ ] 選定方案（A / B / C，或 A + C 組合）
- [ ] 若選 A：撰寫 `local_screener.py`（粗篩 + 細篩），再寫一支與 Goodinfo 歷史結果比對的驗證腳本
- [ ] 修正第 4 節的問題 1（快取 Bug）與問題 5（`.gitignore`），這兩項成本最低

## 6. 在 Cowork 開場可以這樣說

> 請先讀 HANDOFF.md。專案在 `<本機 repo 路徑>`。我決定採用方案 ___，請從 ___ 開始。

## 7. 進度（2026-10-02 Cowork）

- [x] 採用方案 A；新增 `local_screener.py`（粗篩 TWSE+TPEx，備援 FinMind；細篩 FinMind 日線）、`indicators.py`（台灣遞迴 KD）
- [x] `streamlit_app.py` 改接本機選股103（按鈕「我的選股103（本機計算）」），側邊欄可調條件；不再需要 `GOODINFO_COOKIE_MY_STOCK`
- [x] 問題 1：所有快取函式失敗時改為 raise，失敗結果（含個股分析 429）不進快取
- [x] 問題 5：`gitignore.txt` → `.gitignore`，`__pycache__/` 已 `git rm --cached`（已 stage，未 commit）
- [x] `stock_analyzer` 的 KD 改為台灣遞迴式，與 Goodinfo 一致（所有策略表格與圖表的 K/D 數值會改變）
- [x] 2026-10-02 實測與 Goodinfo 選股103 完全一致：2327 國巨、4989 榮科、5328 華容、8086 宏捷科（全市場 1974 檔 → 粗篩 50 → 4）
- [x] TPEx 實測：新版 `dailyQ` 為 404；舊版 `stk_quote_result.php` 忽略日期、只給最新交易日 → 上櫃前一日量改用 FinMind 日線判斷（條件 8）
- [x] 問題 6：月營收選股也改為本機計算（`revenue_screener.py`），使用公開資訊觀測站 (mops) 資料，取代 `GOODINFO_COOKIE_MONTHLY`
- [ ] 已知限制：查歷史日期時 TPEx 拿不到當日行情，上櫃部分會報錯；做歷史比對腳本前需找可查歷史的 TPEx 端點，或上櫃改全用 FinMind
- [ ] 與 Goodinfo 歷史結果比對：紅K棒幅分母、週 KD 是否含未收完的本週
- [ ] 問題 2、3、4、7、8
