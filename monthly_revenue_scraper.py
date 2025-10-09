# monthly_revenue_scraper.py (Updated)
import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st
from io import StringIO

@st.cache_data(ttl=1800)
def scrape_goodinfo():
    """
    爬取 Goodinfo 台灣股市資訊網的月營收強勢股資料並回傳 DataFrame。
    """
    # 1. 設定請求參數 (URL and Headers remain the same)
    url = "https://goodinfo.tw/tw/StockListFilter/StockList.asp?STEP=DATA&SEARCH_WORD=&SHEET=%E5%B9%B4%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&SHEET2=%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&RPT_TIME=%E6%9C%80%E6%96%B0%E8%B3%87%E6%96%99&MARKET_CAT=%E8%87%AA%E8%A8%82%E7%AF%A9%E9%81%B8&INDUSTRY_CAT=%E6%88%91%E7%9A%84%E6%A2%9D%E4%BB%B6&STOCK_CODE=&RANK=0&SORT_FIELD=&SORT=&FL_SHEET=%E5%B9%B4%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&FL_SHEET2=%E7%8D%B2%E5%88%A9%E8%83%BD%E5%8A%9B&FL_MARKET=%E4%B8%8A%E5%B8%82%2F%E4%B8%8A%E6%AB%83&FL_ITEM0=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E7%95%B6%E6%9C%88&FL_VAL_S0=15&FL_VAL_E0=&FL_VAL_CHK0=&FL_ITEM1=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D1%E6%9C%88&FL_VAL_S1=10&FL_VAL_E1=&FL_VAL_CHK1=&FL_ITEM2=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D2%E6%9C%88&FL_VAL_S2=10&FL_VAL_E2=&FL_VAL_CHK2=&FL_ITEM3=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D3%E6%9C%88&FL_VAL_S3=10&FL_VAL_E3=&FL_VAL_CHK3=&FL_ITEM4=%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%B9%B4%E5%A2%9E%E7%8E%87%28%25%29%E2%80%93%E5%89%8D4%E6%9C%88&FL_VAL_S4=10&FL_VAL_E4=&FL_VAL_CHK4=&FL_ITEM5=&FL_VAL_S5=&FL_VAL_E5=&FL_VAL_CHK5=&FL_ITEM6=&FL_VAL_S6=&FL_VAL_E6=&FL_VAL_CHK6=&FL_ITEM7=&FL_VAL_S7=&FL_VAL_E7=&FL_VAL_CHK7=&FL_ITEM8=&FL_VAL_S8=&FL_VAL_E8=&FL_VAL_CHK8=&FL_ITEM9=&FL_VAL_S9=&FL_VAL_E9=&FL_VAL_CHK9=&FL_ITEM10=&FL_VAL_S10=&FL_VAL_E10=&FL_VAL_CHK10=&FL_ITEM11=&FL_VAL_S11=&FL_VAL_E11=&FL_VAL_CHK11=&FL_RULE0=%E6%9C%88%E7%87%9F%E6%94%B6%7C%7C%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%AD%B7%E5%B9%B4%E5%90%8C%E6%9C%9F%E5%89%8D3%E9%AB%98%40%40%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%8E%92%E5%90%8D%E7%B4%80%E9%8C%84%40%40%E5%96%AE%E6%9C%88%E7%87%9F%E6%94%B6%E5%89%B5%E6%AD%B7%E5%B9%B4%E5%90%8C%E6%9C%9F%E5%89%8D3%E9%AB%98&FL_RULE_CHK0=&FL_RULE1=&FL_RULE_CHK1=&FL_RULE2=&FL_RULE_CHK2=&FL_RULE3=&FL_RULE_CHK3=&FL_RULE4=&FL_RULE_CHK4=&FL_RULE5=&FL_RULE_CHK5=&FL_RANK0=&FL_RANK1=&FL_RANK2=&FL_RANK3=&FL_RANK4=&FL_RANK5=&FL_FD0=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD1=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD2=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD3=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD4=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&FL_FD5=%7C%7C%7C%7C%7C%7C%3D%7C%7C%7C%7C%7C%7C&MY_FL_RULE_NM=%E7%87%9F%E6%94%B6%E9%81%B8%E8%82%A102"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko/140.0.0.0 Safari/537.36',
        'Cookie': 'CLIENT%5FID=20250930154352894%5F59%2E120%2E30%2E162; _ga=GA1.1.489566454.1759218236; _cc_id=d1d9f63abdfc8516f9450df55223d69c; LOGIN=EMAIL=yjc5760%40gmail%2Ecom&USER%5FNM=YJ+Chen&ACCOUNT%5FID=107359590931917990151&ACCOUNT%5FVENDOR=Google&NO%5FEXPIRE=T; panoramaId_expiry=1759914818552; panoramaId=7dd09125fab8124072d06e13f222a9fb927ac8a903aa968cefd8e9a67dec526e; panoramaIdType=panoDevice; ad2udid=68e5e6bab111e9.670799353d8d9bb327d340e0019eae44b0f0c301; AviviD_uuid=9666e13e-ba28-479d-87fa-dc6c3082fb65; AviviD_refresh_uuid_status=1; SCREEN_SIZE=WIDTH=1920&HEIGHT=1200; IS_TOUCH_DEVICE=F; __gads=ID=768f461bbe6e2693:T=1759218239:RT=1759909807:S=ALNI_MYPcu5Tf9AFtXvz4jQ2Q2_bnymdMQ; __gpi=UID=0000119caee91eb7:T=1759218239:RT=1759909807:S=ALNI_MYRrniCHs5AYZqf36tBY6ayE7IOuQ; __eoi=ID=32f5e8dff75c7e64:T=1759218239:RT=1759909807:S=AA-AfjYDJQZugctbpPhsR6WPoYVR; cto_bidid=m5O-DF9pNDIlMkJPemFKNnRseXdXOEhRTkhUNjdLSUQlMkIzVDZWWmpDWk9PQ2g1ZXdOdjZxNjBBbjRzSEUyNjJNdjRET2trNDdUNUREJTJGcTRCRVR1QXFoZDMwM0ZYSSUyQlNGUUJ2WngzVEolMkZtZ3JaMnZlZDQlM0Q; FCNEC=%5B%5B%22AKsRol91KZDhEX8Xfx9BCJ5sYSAsNjyVNAw2Qw7LN40hl4F1EXLiWJTktnLbaBs2dZuL5Y4KMGDVES2N_WUzcA9z2wHZAhihF8Eu-nKXt2IB_TiH_zbkMYRwhmiTPvvgbImSHSAXA2N6Dtl8ZSdBR31q_GQNUMh5Vw%3D%3D%22%5D%5D; cto_bundle=ORmnNV90N25jamdVMVlaZW4wVWNjMDJMNDJ4WlpzaEk1c1p2d1Z3Nnl2VTJsNU9NNSUyQlRrTmdzZ0V4VWdhaFR2OSUyRm1vYUdzU2JmdHBxaEhweE11QUhKYXM4TkJMcyUyRnM2JTJGVyUyQlJZMXNpNnBpdUxUck15cXRjazYzU1NaSXd5Vng5Vm95aEslMkJ1VkJ1bldERW45bzJMUVJzYmRpMmRNNGVSS2NuVXllNlFBSWdUZXZCcSUyQmpzclY0cHcyVnpzS3lsMmhsQzFWWCUyQmtJU09JRWxLNlRhOWNOSXElMkJvOEN6ZlhjeDJKUTRGRVljVkNodiUyQmpWeEpGbG5QUTF3V3V0STd3QU1WVnJXaVM; SESSION%5FVAL=57506516%2E58; _ga_0LP5MLQS7E=GS2.1.s1759909809$o5$g1$t1759910315$j58$l0$h0'
    }

    try:
        st.write("正在從 Goodinfo (月營收) 爬取資料...")
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        response.encoding = 'utf-8'

        soup = BeautifulSoup(response.text, 'lxml')
        table = soup.find('table', {'id': 'tblStockList'})

        if not table:
            st.error("在 Goodinfo 頁面中找不到指定的表格 (ID: tblStockList)。請檢查 Cookie 是否已過期。")
            return None

        dfs = pd.read_html(StringIO(str(table)))
        if not dfs:
            st.error("Pandas 無法從 Goodinfo 的 HTML 表格中解析出資料。")
            return None

        df = dfs[0]

        # --- 開始進行更穩健的欄位清理 ---
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        header_row_index = -1
        for i, row in df.iterrows():
            row_str = ''.join(map(str, row.values))
            if '代號' in row_str and '名稱' in row_str:
                header_row_index = i
                break
        
        if header_row_index != -1:
            df.columns = df.iloc[header_row_index]
            df = df.drop(index=range(header_row_index + 1)).reset_index(drop=True)

        df.columns = df.columns.str.strip().str.replace(r'\s+', '', regex=True)
        
        if '代號' in df.columns:
            df.rename(columns={'代號': '代碼'}, inplace=True)

        if '代碼' not in df.columns:
            st.error("爬取資料後，無法找到 '代碼' 欄位。網站結構可能已變更。")
            st.write("偵測到的欄位:", df.columns.tolist())
            return None
        # --- 清理結束 ---
        
        st.success(f"成功爬取到 {len(df)} 筆資料。")
        return df

    except requests.exceptions.RequestException as e:
        st.error(f"請求 Goodinfo 失敗: {e}")
        return None
    except Exception as e:
        st.error(f"處理 Goodinfo (月營收) 資料時發生未預期錯誤: {e}")
        return None