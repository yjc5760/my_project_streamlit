# 台股分析儀 (Taiwan Stock Analyzer)

一個以 [Streamlit](https://streamlit.io/) 為基礎的台灣股市多功能分析儀表板，整合多個資料來源，提供選股篩選、技術分析、籌碼集中度及個股深度分析等功能。

---

## 功能特色

### 📊 Goodinfo 自訂選股
- 從 Goodinfo 台灣股市資訊網爬取「我的選股」自訂條件清單（日線紅 K、KD 黃金交叉、均線多頭排列等）
- - 支援表格資料下載為 CSV 檔案
 
  - ### 📈 月營收選股
  - - 從 Goodinfo 爬取「月營收選股」清單，篩選連續多月年增率均達門檻的強勢股
    - - 支援表格資料下載為 CSV 檔案
     
      - ### 🏆 Yahoo 股市排行榜
      - - 爬取 Yahoo 股市各類即時排行榜（漲幅榜、成交量榜等）
        - - 根據股價、漲跌幅、預估成交量等條件進行初步篩選
          - - 利用 FinMind API 進行多執行緒並發技術指標分析，標記符合多重條件的潛力個股
            - - 支援表格資料下載為 CSV 檔案
             
              - ### 🎯 籌碼集中度排行
              - - 爬取外部籌碼集中度排行資料（1日、5日、10日等）
                - - 篩選籌碼持續集中且均量達標的個股
                  - - 支援表格資料下載為 CSV 檔案
                   
                    - ### 🔍 個股深度分析
                    - 輸入股票代碼或名稱，可查詢：
                    - - **技術指標圖表**：K 線圖、成交量、MACD、KD 指標（使用 Plotly 互動式圖表）
                      - - **月營收趨勢圖**：近 3 年單月營收 vs 去年同期比較折線圖（資料來源：FinMind API）
                        - - **大戶持股變化圖**：近 12 週持股超過 400 張的大股東持有比例趨勢（資料來源：norway.twsthr.info）
                         
                          - ---

                          ## 技術架構

                          | 模組 | 功能 |
                          |---|---|
                          | `streamlit_app.py` | 主應用程式，整合所有功能模組，負責 UI 顯示與資料快取 |
                          | `scraper.py` | 爬取 Goodinfo「我的選股」自訂篩選清單 |
                          | `monthly_revenue_scraper.py` | 爬取 Goodinfo「月營收選股」清單 |
                          | `yahoo_scraper.py` | 爬取 Yahoo 股市排行榜，並計算盤中預估成交量因子 |
                          | `concentration_1day.py` | 爬取並解析籌碼集中度排行資料 |
                          | `stock_analyzer.py` | 呼叫 FinMind API 抓取個股歷史股價，計算 KD、MACD、WMA 等技術指標 |
                          | `stock_information_plot.py` | 生成個股月營收趨勢圖與大戶持股變化圖（Plotly） |

                          ---

                          ## 使用技術

                          - **前端框架**：Streamlit >= 1.30.0
                          - - **資料處理**：Pandas >= 2.0.0、NumPy >= 1.26.0
                            - - **視覺化**：Plotly >= 5.18.0
                              - - **網路爬蟲**：Requests >= 2.31.0、BeautifulSoup4 >= 4.12.0、lxml >= 5.0.0
                                - - **台股資料**：twstock >= 1.3.0
                                  - - **外部 API**：FinMind API（個股歷史股價與月營收）
                                   
                                    - ---

                                    ## 安裝與執行

                                    ### 1. 複製專案

                                    ```bash
                                    git clone https://github.com/yjc5760/my_project_streamlit.git
                                    cd my_project_streamlit
                                    ```

                                    ### 2. 安裝相依套件

                                    ```bash
                                    pip install -r requirements.txt
                                    ```

                                    ### 3. 設定環境變數 / Secrets

                                    本專案需要以下 API 金鑰與 Cookie，請在 `.streamlit/secrets.toml` 或 Streamlit Cloud 的 Secrets 管理介面中設定：

                                    ```toml
                                    # .streamlit/secrets.toml

                                    FINMIND_API_TOKEN = "你的 FinMind API Token"
                                    GOODINFO_COOKIE_MY_STOCK = "你的 Goodinfo 選股 Cookie"
                                    GOODINFO_COOKIE_MONTHLY = "你的 Goodinfo 月營收 Cookie"
                                    ```

                                    > **注意**：Goodinfo Cookie 需從瀏覽器登入後手動複製，有效期限有限，過期需更新。
                                    >
                                    > ### 4. 啟動應用程式
                                    >
                                    > ```bash
                                    > streamlit run streamlit_app.py
                                    > ```
                                    >
                                    > ---
                                    >
                                    > ## 部署至 Streamlit Cloud
                                    >
                                    > 1. 將此 repository Fork 或匯入至你的 GitHub 帳號
                                    > 2. 2. 前往 [Streamlit Cloud](https://streamlit.io/cloud) 建立新 App，選擇此 repository 的 `streamlit_app.py`
                                    >    3. 3. 在 App 設定中的 **Secrets** 區塊填入上述三個環境變數
                                    >       4. 4. 部署完成後即可透過公開 URL 存取
                                    >         
                                    >          5. ---
                                    >         
                                    >          6. ## 資料來源聲明
                                    >         
                                    >          7. - [Goodinfo! 台灣股市資訊網](https://goodinfo.tw) — 選股清單與月營收資料
                                    > - [Yahoo 股市](https://tw.stock.yahoo.com) — 即時排行榜
                                    > - - [FinMind](https://finmindtrade.com) — 個股歷史股價與月營收 API
                                    >   - - [norway.twsthr.info](https://norway.twsthr.info) — 大戶持股資料
                                    >     - - [peicheng.com.tw](http://asp.peicheng.com.tw) — 籌碼集中度排行
                                    >      
                                    >       - > 本專案僅供個人學習與研究使用，爬蟲行為請遵守各網站使用條款。
                                    >         >
                                    >         > ---
                                    >         >
                                    >         > ## 授權
                                    >         >
                                    >         > MIT License
