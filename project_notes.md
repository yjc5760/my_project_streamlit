# 專案筆記

## 2026-10-03 收工

### 完成事項
1. 抽離並整合核心邏輯至 services.py，使 Streamlit 應用與 MCP Server 可以共用選股、畫圖與分析邏輯。
2. 實作 mcp_server.py，透過 stdio 暴露 MCP 工具（選股、籌碼、營收、漲幅排行與個股圖表），供 Antigravity 呼叫。
3. 新增 finmind_client.py 統一管理 FinMind 連線與快取，避免重複實作。
4. 新增 market_calendar.py 判斷台股交易日與非交易日的盤中量能預估邏輯。
5. 新增 tests/ 單元測試目錄並加入 pytest 測試。
6. 更新 README.md 詳細記錄 MCP 安裝步驟與專案架構。

### 下一步
- 測試 MCP Server 透過 Antigravity 的運作是否順暢。
- 根據未來需求擴充新的選股策略或指標。
- 考慮加上 GitHub Actions 自動執行 pytest。

### 踩坑
- 無特殊踩坑，主要為架構解耦與依賴注入的重構。

## 2026-10-09 收工

### 完成事項
1. 於 Streamlit UI 側邊欄新增選股策略（我的選股103、月營收選股）的參數設定介面（Sliders、Checkboxes 等），達成參數動態化。
2. 實作並整合「月營收選股」邏輯，新增自訂比較年份、YoY 下限、及「最新公告月作為基準」的彈性功能。
3. 完善 `services.py` 裡的選股策略與對應的 `revenue_screener.py` 的快取資料處理。
4. 增強了側邊欄連線狀態與盤中交易狀態指示。

### 下一步
- 測試並驗證新加入的選股策略參數動態調整是否穩定。
- 確認在盤中時即時排行榜與量能預估邏輯是否如預期運作。

### 踩坑
- 參數化過程中，需確保 Streamlit session state 在切換選單時能正確保留或同步設定，避免刷新流失設定。


## 2026-10-29 收工

### 完成事項
1. 優化 `scrape_utils.py` 之 `disk_memo` 裝飾器：支援關鍵字參數 (`**kwargs`)，並使用 `inspect.signature` 自動綁定與補全預設引數，讓 `f(code)`、`f(code, 3)` 與 `f(code, years=3)` 等呼叫形式共用同一快取鍵，避免重複抓取。
2. 於 `tests/test_caching.py` 補齊 `disk_memo` 針對關鍵字參數與預設值快取命中的單元測試。

### 下一步
- 持續觀察各爬蟲/分析邏輯使用 `disk_memo` 之命中率與快取效益。
- 檢視是否有其他模組呼叫帶有 kwargs 需要磁碟快取的場景。

### 踩坑
- 無。使用標準函式庫 `inspect.signature` 即可純粹處理參數標準化，不增加額外外部依賴。


## 2026-11-03 收工

### 完成事項
1. **Streamlit UI 模組化拆分**：將龐大的 `streamlit_app.py` 拆解重構，把畫面呈現、側邊欄參數與各選股視圖模組化移入 `views/`（`common.py`、`sidebar.py`、`concentration.py`、`screen103.py`、`revenue.py`、`quality.py`、`ranking.py`、`single_stock.py`），使進入點維持約 100 行清晰路由。
2. **上次成功結果（LKG）雲端備份**：於 `scrape_utils.py` 實作選用的 GitHub Gist 遠端備份與 HMAC 簽章驗證機制，並透過背景線程非同步上傳，解決 Streamlit Cloud 重新部署後本地快取遺失的問題；新增 `tests/test_lkg_remote.py` 測試。
3. **`mcp_output/` 自動清理策略**：新增 `output_retention.py`，支援 HTML 個股圖表（同檔/同種類留最新一份、預設 >30 天清理）、法說會 PDF 重複副本清理與除錯暫存檔清理，並整合至 `mcp_server.py` 啟動與存檔觸發；新增 `tests/test_output_retention.py` 測試。
4. **文件與測試完善**：更新 `README.md` 專案架構、Gist 備份設定與 MCP 清理說明；全量 108 項單元測試皆順利通過。

### 下一步
- 觀察 Streamlit Cloud 與 GitHub Gist 整合之實際運作情況。
- 觀察 MCP Server 長期運作下 `mcp_output/` 檔案清理表現。

### 踩坑
- 無。拆分 views 模組時維持 session state 與函式介面一致性，確保既有行為無縫過渡。

