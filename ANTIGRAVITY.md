# 台股分析儀 (Taiwan Stock Analyzer) 專案守則

## 專案目的
以 Streamlit 打造台股選股與技術分析儀表板，支援本機計算，並提供 MCP 介面供 Antigravity AI 呼叫。

## 開發規範
1. 邏輯解耦：業務邏輯（如選股規則、快取處理）應置於 services.py 或獨立模組，避免與 UI (Streamlit) 耦合。
2. 錯誤處理：任何爬蟲或 API 失敗不應被快取，需妥善顯示錯誤並保留上次成功資料。
3. 環境隔離：Streamlit 應用使用 equirements.txt；MCP 服務使用獨立的 .venv-mcp 與 equirements-mcp.txt 以免干擾。

## 開工與收工
- 參考專案根目錄的 project_notes.md 追蹤最新進度、完成事項與下一步。
