# 台股分析儀 (Taiwan Stock Analyzer)

以 [Streamlit](https://streamlit.io/) 建置的台股選股與技術分析儀表板。選股條件全部在本機計算，資料來自證交所、櫃買中心、公開資訊觀測站與 FinMind，不需要 Goodinfo Cookie。

---

## 功能

### ⭐ 我的選股103（本機計算）

等同 Goodinfo「我的選股103」的 8 個條件，已於 2026-10-02 實測和 Goodinfo 結果一致：

1. 紅K棒幅 2.5%～10%
2. 成交張數 5,000～900,000 張
3. 季線乖離 -5%～+5%
4. 週K值 0～50
5. 週K值向上
6. 月線 < 季線（空頭排列）
7. 日K > 日D
8. 今日量 > 1.3 × 昨日量

- 粗篩：證交所＋櫃買中心每日收盤行情（條件 1、2、8）
- 細篩：候選股逐檔抓 FinMind 日線（條件 3～7）
- 櫃買中心只提供最新一天的行情，上櫃股的條件 8 改用 FinMind 日線判斷
- 條件可在側邊欄調整

### 📈 月營收選股（本機計算）

等同 Goodinfo「月營收選股03」：

1. 單月營收年增率－當月 ≥ 15%
2. 前 1～4 月年增率皆 ≥ 10%
3. 單月營收創歷年同期前 3 高

- 資料來源：公開資訊觀測站「每月營業收入彙總表」（上市／上櫃、含 KY），不需 FinMind
- 「當月」為全市場最新有公告的月份（例：10 月初為 9 月），還沒公告的公司不會入選
- 同期排名比對 2001 年至今的同月份營收
- 條件可在側邊欄調整

### 📊 1日籌碼集中度選股

爬取籌碼集中度排行，篩選 5 日 > 10 日 > 20 日集中度、且 10 日均量達門檻的股票。

### 🚀 漲幅排行榜（上市／上櫃）

爬取 Yahoo 股市漲幅排行，依股價、漲幅、預估成交量（盤中依時間換算）篩選後做技術分析。

### 🔍 個股查詢

輸入代碼或名稱，顯示：

- **技術分析圖**：K 線、均線、成交量、KD、乖離率、階梯訊號、MACD、WMA
- **月營收趨勢圖**：近 3 年單月營收與去年同期比較（FinMind）
- **大戶持股變化圖**：近 12 週 400 張以上大股東持股比例

### 共通機制

- **KD 指標**：採台灣慣用的遞迴式 KD（9 期，與 Goodinfo 相同），程式在 `indicators.py`
- **失敗不快取**：任何抓取失敗都不會被快取，重新按一次即可重試
- **上次成功結果**：資料源暫時故障時，改顯示上次成功的結果並標註時間（存於 `.cache/last_good/`；Streamlit Cloud 重新部署後會清空）
- **錯誤分類**：爬蟲失敗時會標示「連線問題／被網站阻擋／網頁改版／沒有資料」

---

## 程式架構

| 模組 | 功能 |
|---|---|
| `streamlit_app.py` | 主程式：UI、快取、上次成功結果 |
| `local_screener.py` | 我的選股103（粗篩＋細篩），可獨立執行 |
| `revenue_screener.py` | 月營收選股，可獨立執行 |
| `indicators.py` | 共用技術指標（台灣遞迴 KD） |
| `stock_analyzer.py` | 個股技術指標計算與圖表（FinMind 日線） |
| `stock_information_plot.py` | 月營收趨勢圖、大戶持股變化圖 |
| `concentration_1day.py` | 籌碼集中度爬蟲與篩選 |
| `yahoo_scraper.py` | Yahoo 漲幅排行爬蟲（含備援解析）與盤中預估量因子 |
| `scrape_utils.py` | 爬蟲錯誤分類、上次成功結果存取 |

---

## 安裝與執行

```bash
git clone https://github.com/yjc5760/my_project_streamlit.git
cd my_project_streamlit
pip install -r requirements.txt
streamlit run streamlit_app.py
```

套件版本已固定在 `requirements.txt`，升級前請先在本機測試。

### Secrets

在 `.streamlit/secrets.toml` 或 Streamlit Cloud 的 Secrets 設定：

```toml
FINMIND_API_TOKEN = "你的 FinMind token"
```

沒有 token 也能執行，但 FinMind 匿名存取有請求上限，選股103 的細篩與個股圖表可能失敗。

---

## 命令列工具

```bash
# 我的選股103
python local_screener.py                          # 最新交易日
python local_screener.py --date 2026-10-02        # 指定日期（上櫃部分僅支援最新交易日）
python local_screener.py --diag 5328,8086         # 逐條件診斷指定股票
python local_screener.py --detail detail.csv      # 輸出所有候選股明細

# 月營收選股
python revenue_screener.py
python revenue_screener.py --diag 2402,6712       # 逐條件診斷＋歷年同月營收前 6 名
```

---

## 資料來源

| 資料 | 來源 |
|---|---|
| 上市每日行情 | 臺灣證券交易所 |
| 上櫃每日行情 | 證券櫃檯買賣中心 |
| 月營收彙總 | 公開資訊觀測站 |
| 個股日線、月營收圖 | [FinMind](https://finmindtrade.com/) |
| 籌碼集中度 | peicheng 籌碼集中度排行 |
| 漲幅排行 | Yahoo 奇摩股市 |
| 大戶持股 | norway.twsthr.info |

本工具僅供研究參考，不構成任何投資建議。
