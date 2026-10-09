# 專案筆記

## 2026-10-03 收工

### 完成事項
1. 抽離並整合核心邏輯至 services.py，使 Streamlit 應用與 MCP Server 可以共用選股、畫圖與分析邏輯。
2. 實作 mcp_server.py，透過 stdio 暴露 MCP 工具（選股、籌碼、營收、漲幅排行與個股圖表），供 Antigravity 呼叫。
3. 新增 inmind_client.py 統一管理 FinMind 連線與快取，避免重複實作。
4. 新增 market_calendar.py 判斷台股交易日與非交易日的盤中量能預估邏輯。
5. 新增 	ests/ 單元測試目錄並加入 pytest 測試。
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
