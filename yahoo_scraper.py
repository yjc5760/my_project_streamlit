# yahoo_scraper.py (已修正時區問題)

import pandas as pd
from bs4 import BeautifulSoup
from io import StringIO
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from market_calendar import is_trading_day
from scrape_utils import ScrapeError, fetch_html


def _time_to_seconds(t) -> int:
    """將 datetime.time 物件轉換為秒數（模組層級輔助函式）"""
    return t.hour * 3600 + t.minute * 60 + t.second

# 預估成交量因子表：模組載入時建立一次，後續查表不重複解析
_CSV_DATA = """Time,Factor
9:00,20.00
9:05,14.99
9:10,9.48
9:15,7.12
9:20,5.83
9:25,4.99
9:30,4.42
9:35,3.99
9:40,3.66
9:45,3.39
9:50,3.18
9:55,2.99
10:00,2.83
10:05,2.70
10:10,2.58
10:15,2.48
10:20,2.39
10:25,2.30
10:30,2.23
10:35,2.15
10:40,2.09
10:45,2.03
10:50,1.97
10:55,1.92
11:00,1.87
11:05,1.83
11:10,1.79
11:15,1.74
11:20,1.71
11:25,1.67
11:30,1.63
11:35,1.60
11:40,1.57
11:45,1.54
11:50,1.51
11:55,1.48
12:00,1.46
12:05,1.43
12:10,1.41
12:15,1.38
12:20,1.36
12:25,1.34
12:30,1.32
12:35,1.30
12:40,1.28
12:45,1.25
12:50,1.23
12:55,1.21
13:00,1.19
13:05,1.17
13:10,1.14
13:15,1.12
13:20,1.09
13:25,1.06
13:30,1.00
"""
_FACTOR_TABLE = pd.read_csv(StringIO(_CSV_DATA), skipinitialspace=True)
_FACTOR_TABLE['Time'] = pd.to_datetime(_FACTOR_TABLE['Time'], format='%H:%M').dt.time


def _get_volume_factor(now: datetime | None = None) -> float:
    """
    根據當前時間從模組層級的因子表查表並內插計算成交量預估因子。
    非交易日（週末、國定假日）Yahoo 顯示的是上一個交易日的全天量，因子一律為 1.0。
    """
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    if not is_trading_day(now.date()):
        return 1.0
    now_time = now.time()

    nine_am = datetime.strptime("09:00", "%H:%M").time()
    one_thirty_pm = datetime.strptime("13:30", "%H:%M").time()

    if now_time <= nine_am or now_time >= one_thirty_pm:
        return 1.0

    df_factor = _FACTOR_TABLE

    exact_match = df_factor[df_factor['Time'] == now_time]
    if not exact_match.empty:
        return exact_match['Factor'].iloc[0]

    upper_bound_df = df_factor[df_factor['Time'] > now_time]
    if upper_bound_df.empty:
        return df_factor.iloc[-1]['Factor']

    upper_bound = upper_bound_df.iloc[0]

    lower_bound_df = df_factor[df_factor['Time'] < now_time]
    if lower_bound_df.empty:
        # 時間介於 09:00~09:05 之間，直接回傳上界因子（最保守估計）
        return upper_bound['Factor']
    lower_bound = lower_bound_df.iloc[-1]

    t1_sec = _time_to_seconds(lower_bound['Time'])
    f1 = lower_bound['Factor']
    t2_sec = _time_to_seconds(upper_bound['Time'])
    f2 = upper_bound['Factor']
    now_sec = _time_to_seconds(now_time)

    if t2_sec == t1_sec:
        return f1

    return f1 + (now_sec - t1_sec) * (f2 - f1) / (t2_sec - t1_sec)


def _parse_rows_primary(soup) -> list[dict]:
    """原解析法：依 Yahoo 的 atomic CSS class 抓欄位（改版時最容易壞）。"""
    rows = soup.find_all('li', class_='List(n)')
    out = []
    for i, row in enumerate(rows):
        try:
            sticky_cell = row.find('div', style='position:sticky;min-width:184px')
            if not sticky_cell:
                continue
            rank_span = sticky_cell.find('span', class_=re.compile(r'Fz\(24px\)'))
            name = sticky_cell.find('div', class_='Lh(20px) Fw(600) Fz(16px) Ell').text.strip()
            symbol = sticky_cell.find('span', class_='Fz(14px) C(#979ba7) Ell').text.strip()
            data_containers = row.find_all('div', class_=lambda x: x and 'Fxg(1)' in x and 'Ta(end)' in x)
            if len(data_containers) < 8:
                continue
            rank = pd.to_numeric(rank_span.text.strip(), errors='coerce') if rank_span else i + 1
            out.append({
                'Rank': int(rank),
                'Stock Symbol': symbol,
                'Stock Name': name,
                'Price': pd.to_numeric(data_containers[0].text.strip(), errors='coerce'),
                'Change Percent': pd.to_numeric(data_containers[2].text.strip().replace('%', ''), errors='coerce'),
                'Volume (Shares)': pd.to_numeric(data_containers[6].text.strip().replace(',', ''), errors='coerce'),
            })
        except Exception as e:                        # noqa: BLE001
            print(f"處理第 {i+1} 行資料時發生錯誤：{e}")
    return out


_NUM = re.compile(r'^[+-]?[\d,]+(\.\d+)?%?$')


def _parse_rows_fallback(soup) -> list[dict]:
    """
    備援解析法：不依賴 CSS class，只找「含 /quote/代號 連結的列」，再依數字欄位順序取值
    （股價、漲跌、漲跌幅、最高、最低、價差、成交量…，與主解析法相同的欄位位置）。
    """
    out = []
    for li in soup.find_all('li'):
        a = li.find('a', href=re.compile(r'/quote/\d{4,6}'))
        if not a:
            continue
        code = re.search(r'/quote/(\d{4,6})', a['href']).group(1)
        texts = [t for t in li.stripped_strings]
        nums = [t for t in texts if _NUM.match(t.replace('▲', '').replace('▼', ''))]
        name = next((t for t in texts if not _NUM.match(t) and code not in t), code)
        if len(nums) < 8:
            continue
        # 第一個數字可能是名次
        if len(nums) >= 9 and '.' not in nums[0] and '%' not in nums[0]:
            rank, nums = int(nums[0].replace(',', '')), nums[1:]
        else:
            rank = len(out) + 1
        clean = lambda t: pd.to_numeric(t.replace(',', '').replace('%', ''), errors='coerce')
        out.append({'Rank': rank, 'Stock Symbol': code, 'Stock Name': name,
                    'Price': clean(nums[0]), 'Change Percent': clean(nums[2]),
                    'Volume (Shares)': clean(nums[6])})
    return out


def scrape_yahoo_stock_rankings(url: str) -> pd.DataFrame:
    """
    通用函式：從指定的 Yahoo 股市排行榜 URL 抓取資料。
    失敗時 raise ScrapeError（kind：network / blocked / layout / empty）。
    """
    print(f"正在使用 Requests 從 {url} 抓取資料...")
    html = fetch_html(url, label="Yahoo 股市排行榜", timeout=10, encoding='utf-8')
    soup = BeautifulSoup(html, 'html.parser')

    all_stocks = _parse_rows_primary(soup)
    parser = "primary"
    if not all_stocks:
        all_stocks = _parse_rows_fallback(soup)
        parser = "fallback"
    if not all_stocks:
        raise ScrapeError("layout", "Yahoo 排行榜頁面中解析不到任何股票列（兩種解析法都失敗），網頁可能改版")
    if parser == "fallback":
        print("⚠️ 主解析法失敗，改用備援解析法（Yahoo 可能改版，欄位位置請留意）")

    df = pd.DataFrame(all_stocks)
    df = df.dropna(subset=['Price'])
    if df.empty:
        raise ScrapeError("empty", "Yahoo 排行榜沒有可用的股價資料")

    factor = _get_volume_factor()
    print(f"當前時間 {datetime.now(ZoneInfo('Asia/Taipei')).strftime('%H:%M:%S')}，預估成交量因子: {factor:.2f}")

    df['Factor'] = factor
    df['Volume (Shares)'] = pd.to_numeric(df['Volume (Shares)'], errors='coerce')
    df['Estimated Volume'] = (df['Volume (Shares)'] * factor).round(0).astype('Int64')

    def _extract_digits(x):
        # 用 search 取第一段連續數字並保留字串格式，避免 int('0056') → 56 截斷前導零
        m = re.search(r'\d+', str(x))
        return m.group(0) if m else None
    df['Stock Symbol'] = df['Stock Symbol'].astype(str).apply(_extract_digits).fillna('')
    df.attrs['parser'] = parser
    return df
