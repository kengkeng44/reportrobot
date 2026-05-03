"""
台股盤後籌碼資料：三大法人買賣超。資料源 = 證交所 OpenAPI。
"""

import requests
from datetime import date, timedelta


HEADERS = {"User-Agent": "Mozilla/5.0"}
TWSE_BFI82U = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"


def _last_trading_day(today=None):
    """取最近一個交易日（週末推到週五，不考慮國定假日）。"""
    today = today or date.today()
    wd = today.weekday()  # Mon=0, Sun=6
    if wd == 0:           # Monday → 上週五
        return today - timedelta(days=3)
    if wd == 5:           # Saturday → 週五
        return today - timedelta(days=1)
    if wd == 6:           # Sunday → 週五
        return today - timedelta(days=2)
    return today - timedelta(days=1)


def get_institutional_trades(target_date=None):
    """
    抓三大法人買賣超。回 dict 或 None：
      {
        'date': '2026-05-02',
        'foreign': 12.34,            # 億元，買賣差額
        'investment_trust': -2.34,
        'dealer': 5.67,
        'total': 15.67,
      }
    證交所 API 會在收盤後（約 14:30）才有資料；找不到就往前 5 天試（吸收假日）。
    """
    target = target_date or _last_trading_day()
    for delta in range(5):
        d = target - timedelta(days=delta)
        date_str = d.strftime("%Y%m%d")
        try:
            r = requests.get(
                TWSE_BFI82U,
                params={"response": "json", "dayDate": date_str},
                headers=HEADERS,
                timeout=10,
            )
            data = r.json()
            rows = data.get("data") or []
            if not rows:
                continue

            result = {"date": d.strftime("%Y-%m-%d")}
            dealer_self = 0.0
            dealer_hedge = 0.0
            has_dealer_subtype = False

            for row in rows:
                category = (row[0] or "").strip()
                try:
                    diff = float(row[3].replace(",", "")) / 1e8  # 億元
                except (ValueError, IndexError, AttributeError):
                    continue

                if "外資" in category:
                    result["foreign"] = diff
                elif "投信" in category:
                    result["investment_trust"] = diff
                elif "自營商" in category and "自行買賣" in category:
                    dealer_self = diff
                    has_dealer_subtype = True
                elif "自營商" in category and "避險" in category:
                    dealer_hedge = diff
                    has_dealer_subtype = True
                elif "自營商" in category:
                    result["dealer"] = diff
                elif "合計" in category:
                    result["total"] = diff

            if has_dealer_subtype:
                result["dealer"] = dealer_self + dealer_hedge

            if "foreign" in result or "investment_trust" in result or "dealer" in result:
                return result
        except Exception as e:
            print(f"三大法人抓取失敗 {date_str}: {e}")
            continue
    return None
