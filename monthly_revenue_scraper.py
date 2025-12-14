import requests
import pandas as pd
from bs4 import BeautifulSoup
import io

# -----------------------------------------------------------------------------
# 設定目標網址與請求標頭
# -----------------------------------------------------------------------------
URL = "https://goodinfo.tw/tw/StockListFilter/StockList.asp?STEP=DATA&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6&SHEET=%E7%87%9F%E6%94%B6%E7%8B%80%E6%B3%81&SHEET2=%E6%9C%88%E7%87%9F%E6%94%B6%E7%8B%80%E6%B3%81&FL_SHEET=%E5%B9%B4%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&FL_SHEET2=%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83&MY_FL_RULE_NM=%E6%9C%88%E7%87%9F%E6%94%B6%E9%81%B8%E8%82%A103&FL_ITEM0=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E7%95%B6%E6%9C%88&FL_VAL_S0=15&FL_ITEM1=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D1%E6%9C%88&FL_VAL_S1=10&FL_ITEM2=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D2%E6%9C%88&FL_VAL_S2=10&FL_ITEM3=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D3%E6%9C%88&FL_VAL_S3=10&FL_ITEM4=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D4%E6%9C%88&FL_VAL_S4=10&FL_RULE0=%E6%9C%88%E7%87%9F%E6%94%B6%7C%7C%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%AD%B7%E5%B9%B4%E5%90%8C%E6%9C%9F%E5%89%8D3%E9%AB%98%40%40%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%8E%92%E5%90%8D%E7%B4%80%E9%8C%84%40%40%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%AD%B7%E5%B9%B4%E5%90%8C%E6%9C%9F%E5%89%8D3%E9%AB%98&FL_FD0=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD1=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD2=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD3=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD4=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&FL_FD5=%7C%7C1%7C%7C0%7C%7C%3D%7C%7C%7C%7C1%7C%7C0&IS_RELOAD_REPORT=T"

# 【重要】：Goodinfo 依賴 Referer 和 User-Agent，且您的篩選條件需要 Cookie
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Referer": "https://goodinfo.tw/",  # 許多網站會檢查這個
    # ------------------------------------------------------------------
    # 【請注意】：因為您提到需要登入才能查看，且網址包含自訂篩選條件，
    # 請務必將瀏覽器中的 Cookie 字串貼在下方引號中，否則爬蟲將無法取得正確資料。
    # ------------------------------------------------------------------
    "Cookie": "__qca=I0-2120255871-1765697107313; CLIENT%5FID=20251110163553320%5F114%2E37%2E222%2E90; _ga=GA1.1.832428120.1762763765; _cc_id=ada7690de47741c349f179f457208c78; LOGIN=EMAIL=yjc5760%40gmail%2Ecom&USER%5FNM=YJ+Chen&ACCOUNT%5FID=107359590931917990151&ACCOUNT%5FVENDOR=Google&NO%5FEXPIRE=T; IS_TOUCH_DEVICE=F; SCREEN_SIZE=WIDTH=1920&HEIGHT=1080; panoramaId_expiry=1766301878708; panoramaId=9e81e8765c9bd09c413245655ac1185ca02c4a4dd131c4f03e107d1ddf5200a4; panoramaIdType=panoDevice; __gads=ID=26c4020a504f4925:T=1762763767:RT=1765697079:S=ALNI_MYlg_2Y6k-m7aDIXN1sqaq0pis3Yw; __gpi=UID=000011b284b2036e:T=1762763767:RT=1765697079:S=ALNI_MbFAko50gF8iaVeMc75CATgrEO8MQ; __eoi=ID=7b4c554b1803ff64:T=1762763767:RT=1765697079:S=AA-AfjaRA69ugEU-b6Qdv3vI8WTB; FCCDCF=%5Bnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B%5B32%2C%22%5B%5C%224073ca81-6853-4cdc-b16a-44f26bfae1c0%5C%22%2C%5B1762763765%2C349000000%5D%5D%22%5D%5D%5D; cto_bidid=b2hFhV9OQVQ0ZUo4V2t5cndUc3E3ejdqWEJKd1FHT3JhZFk0bDhHVmdydEhTWTFlQjBlenBTdlBwRnlRTmV3d0lqaVVZT1l0VUdHOEFhbmRaeVludDdqcnN6WnQweWZ3SFRYM3FsejhWSjJOajdsMCUzRA; FCNEC=%5B%5B%22AKsRol9Bt7FsXXKtCJF8UwBni-lrqnqPD0nYZur4YWfypR8gHChWKzv8n8K7GpvMHYuXxg4m0Lzw0hDnOVGe5BVrb7yVckln82lXSPN4yDo_3jYImevURBqgCICUAEBs5gibJ2Gjts3uMyoCkZ5xCuyXCfJs2zZ5Dg%3D%3D%22%5D%5D; cto_bundle=5nQkw180ZHJ6RVJQd3VzalVnakZybTFaUG1FVGFySlVZRzRWeDJtM0JycXVVYzFSWlNPeDk3dXUzVENCWVlhNVlLc0hnNGt3JTJGbjZhbUtkUTJOUkIyc21OdTNVR3JUTG5XVkRXazRETXd5YXE4SHBlVHdTWnVVMVFCTlJTVjc1TmZKcDJKWHZxRmlidzBKTlhjZW1Md0g0amhiQSUzRCUzRA; SESSION%5FVAL=55647860%2E27; _ga_0LP5MLQS7E=GS2.1.s1765697076$o3$g1$t1765697251$j57$l0$h0" 
}

# 目標 CSS 選擇器 (您提供的 ID 是一個 div，我們將解析這個 div 內的表格)
TARGET_SELECTOR = "#divStockList"

def main():
    # 使用 Session 物件來保持連線狀態
    session = requests.Session()

    try:
        print("1. 正在發送請求...")
        response = session.get(URL, headers=HEADERS, timeout=15)
        
        # 強制設定編碼為 utf-8 (Goodinfo 有時會需要)
        response.encoding = 'utf-8'

        if response.status_code == 200:
            print("2. 成功接收回應，開始解析 HTML...")
            
            # 使用 BeautifulSoup 解析 HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 根據選擇器定位目標元素
            target_element = soup.select_one(TARGET_SELECTOR)
            
            if target_element:
                print(f"   已找到目標區塊: {TARGET_SELECTOR}")
                
                # 將找到的區塊 HTML 轉為字串，交給 pandas 解析表格
                # pandas 會自動抓取該區塊內的所有 <table>
                # io.StringIO 用於避免 pandas 未來版本的警告
                html_io = io.StringIO(str(target_element))
                dfs = pd.read_html(html_io, flavor='bs4')
                
                if dfs:
                    # 通常第一個表格就是主要資料，如果有錯位，可以嘗試 dfs[1]
                    df = dfs[0]
                    
                    print("3. 資料整理完成！")
                    print("-" * 30)
                    print(f"   擷取到的資料維度: {df.shape}")
                    print("-" * 30)
                    print(df.head()) # 顯示前 5 筆資料
                    
                    # 選擇性：將資料儲存為 CSV
                    # df.to_csv("goodinfo_stock_data.csv", index=False, encoding="utf-8-sig")
                else:
                    print("錯誤：在目標區塊內找不到任何表格 (<table>)。")
            else:
                print(f"錯誤：找不到符合選擇器 '{TARGET_SELECTOR}' 的區塊。")
                print("偵錯提示：這可能是因為 Cookie 失效，導致網站重新導向到首頁或登入頁。")
        else:
            print(f"錯誤：請求失敗，狀態碼: {response.status_code}")

    except Exception as e:
        print(f"發生未預期的錯誤: {e}")

if __name__ == "__main__":
    main()
