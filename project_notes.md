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
