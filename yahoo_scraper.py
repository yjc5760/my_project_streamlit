# yahoo_scraper.py (整合 yahoo_stock.py 和 yahoo_stock_otc.py)

import requests
import pandas as pd
from bs4 import BeautifulSoup
from io import StringIO
import re
from datetime import datetime

def _get_volume_factor() -> float:
    """
    根據當前時間從內建的資料表查表並內插計算成交量預估因子。
    """
    now_time = datetime.now().time()
    
    nine_am = datetime.strptime("09:00", "%H:%M").time()
    one_thirty_pm = datetime.strptime("13:30", "%H:%M").time()
    
    if now_time <= nine_am or now_time >= one_thirty_pm:
        return 1.0
    
    # 將 預估量.csv 的資料直接寫在程式碼中
    csv_data = """Time,Factor
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

     
    try:
        df_factor = pd.read_csv(StringIO(csv_data), skipinitialspace=True)
        df_factor['Time'] = pd.to_datetime(df_factor['Time'], format='%H:%M').dt.time
    except Exception as e:
        print(f"錯誤：處理內建的預估因子資料時發生錯誤: {e}。預估因子將設為 1。")
        return 1.0

    exact_match = df_factor[df_factor['Time'] == now_time]
    if not exact_match.empty:
        return exact_match['Factor'].iloc[0]

    upper_bound_df = df_factor[df_factor['Time'] > now_time]
    if upper_bound_df.empty:
        return df_factor.iloc[-1]['Factor']
        
    upper_bound = upper_bound_df.iloc[0]
    lower_bound = df_factor[df_factor['Time'] < now_time].iloc[-1]
    
    def time_to_seconds(t):
        return t.hour * 3600 + t.minute * 60 + t.second

    t1_sec = time_to_seconds(lower_bound['Time'])
    f1 = lower_bound['Factor']
    t2_sec = time_to_seconds(upper_bound['Time'])
    f2 = upper_bound['Factor']
    now_sec = time_to_seconds(now_time)

    if t2_sec == t1_sec:
        return f1

    return f1 + (now_sec - t1_sec) * (f2 - f1) / (t2_sec - t1_sec)


def scrape_yahoo_stock_rankings(url: str) -> pd.DataFrame | None:
    """
    通用函式：從指定的 Yahoo 股市排行榜 URL 抓取資料。
    """
    print(f"正在使用 Requests 從 {url} 抓取資料...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
    }

    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        res.encoding = 'utf-8'

        soup = BeautifulSoup(res.text, 'html.parser')
        rows = soup.find_all('li', class_='List(n)')

        if not rows:
            print("錯誤：找不到股票排名列表的行元素。Yahoo Finance 的網頁結構可能已變更。")
            return None
        
        all_stocks = []
        for i, row in enumerate(rows):
            try:
                sticky_cell = row.find('div', style='position:sticky;min-width:184px')
                if not sticky_cell: continue

                rank_span = sticky_cell.find('span', class_=re.compile(r'Fz\(24px\)'))
                name = sticky_cell.find('div', class_='Lh(20px) Fw(600) Fz(16px) Ell').text.strip()
                symbol = sticky_cell.find('span', class_='Fz(14px) C(#979ba7) Ell').text.strip()
                
                data_containers = row.find_all('div', class_=lambda x: x and 'Fxg(1)' in x and 'Ta(end)' in x)
                if len(data_containers) < 8: continue

                rank = pd.to_numeric(rank_span.text.strip(), errors='coerce') if rank_span else i + 1
                price = pd.to_numeric(data_containers[0].text.strip(), errors='coerce')
                change_percent_str = data_containers[2].text.strip().replace('%', '')
                change_percent = pd.to_numeric(change_percent_str, errors='coerce')
                volume_str = data_containers[6].text.strip().replace(',', '')
                volume = pd.to_numeric(volume_str, errors='coerce')
                
                all_stocks.append({
                    'Rank': int(rank),
                    'Stock Symbol': symbol,
                    'Stock Name': name,
                    'Price': price,
                    'Change Percent': change_percent,
                    'Volume (Shares)': volume,
                })
            except Exception as e:
                print(f"處理第 {i+1} 行資料時發生錯誤：{e}")
                continue

        df = pd.DataFrame(all_stocks)
        if df.empty:
            print("未能成功解析任何股票資料。")
            return None
            
        factor = _get_volume_factor()
        print(f"當前時間 {datetime.now().strftime('%H:%M:%S')}，預估成交量因子: {factor:.2f}")
        
        df['Factor'] = factor
        df['Volume (Shares)'] = pd.to_numeric(df['Volume (Shares)'], errors='coerce')
        df['Estimated Volume'] = (df['Volume (Shares)'] * factor).round(0).astype('Int64')

        df['Stock Symbol'] = df['Stock Symbol'].astype(str).apply(lambda x: re.findall(r'\d+', x)[0] if re.findall(r'\d+', x) else None)
        df['Stock Symbol'] = pd.to_numeric(df['Stock Symbol'], errors='coerce').fillna(0).astype('int64')

        return df

    except requests.exceptions.RequestException as e:
        print(f"網路請求失敗：{e}")
        return None
    except Exception as e:
        print(f"發生未預期的錯誤：{e}")
        return None