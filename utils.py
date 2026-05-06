"""
utils.py
共用工具模組：HTTP session 建立、fetch_html、retry 機制、get_stock_code
"""

import os
import sys
import platform
import time
import requests
import twstock

# 只在 Windows 環境才設定 UTF-8 編碼
if platform.system() == 'Windows' and hasattr(sys.stdout, 'reconfigure'):
      sys.stdout.reconfigure(encoding='utf-8', errors='replace')

USER_AGENT = (
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36"
)


def get_goodinfo_session(env_key: str) -> requests.Session:
      """
          建立並回傳設定好 Cookie 的 Goodinfo requests Session。
              env_key: 環境變數名稱（例如 'GOODINFO_COOKIE_MY_STOCK'）
                  若環境變數未設定則拋出 ValueError。
                      """
      cookie = os.getenv(env_key)
      if not cookie:
                raise ValueError(
                              f"未設定環境變數 '{env_key}'，"
                              "請在 Streamlit Cloud 的 Secrets 中設定此值。"
                )
            session = requests.Session()
    session.headers.update({
              "User-Agent": USER_AGENT,
              "Cookie": cookie,
    })
    return session


def fetch_html(url: str, session: requests.Session, timeout: int = 20) -> str | None:
      """
          使用給定的 session 抓取 URL，回傳 HTML 字串。
              失敗時印出錯誤並回傳 None。
                  """
    try:
              response = session.get(url, timeout=timeout)
              response.raise_for_status()
              response.encoding = 'utf-8'
              return response.text
except requests.exceptions.RequestException as e:
        print(f"請求失敗 [{url[:60]}...]: {e}")
        return None


def fetch_html_with_retry(
      url: str,
      session: requests.Session,
      retries: int = 3,
      delay: float = 2.0,
      timeout: int = 20,
) -> str | None:
      """
          帶有重試機制的 HTML 抓取，失敗後等待 delay 秒再重試。
              """
    for attempt in range(1, retries + 1):
              html = fetch_html(url, session, timeout=timeout)
              if html is not None:
                            return html
                        if attempt < retries:
                                      print(f"第 {attempt} 次嘗試失敗，{delay} 秒後重試...")
                                      time.sleep(delay)
                              print(f"已重試 {retries} 次，全部失敗。")
    return None


def get_stock_code(stock_identifier) -> str | None:
      """
          根據股票代碼或名稱查找並回傳股票代碼。
              此為各模組共用的查詢邏輯，統一維護於此。
                  """
    try:
              identifier_str = str(stock_identifier).strip()
        if identifier_str in twstock.codes:
                      return identifier_str
                  for code, stock_info in twstock.codes.items():
              if hasattr(stock_info, 'name') and stock_info.name == identifier_str:
                                return code
                        return None
except Exception as e:
        print(f"查詢股票代碼時發生錯誤: {e}")
        return None
