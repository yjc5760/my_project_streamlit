@echo off
rem 強制切換到批次檔所在的目錄
cd /d %~dp0

rem 執行 Streamlit 應用程式
python -m streamlit run streamlit_app.py

rem 讓視窗在結束後暫停，方便我們看到任何可能的錯誤訊息
pause
