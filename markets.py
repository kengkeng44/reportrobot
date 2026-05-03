"""
大盤指數模組：用 Yahoo Finance chart API 抓主要指數即時報價。
"""

import requests


INDEX_LABELS = [
    ("^TWII", "台股加權"),
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq"),
    ("^DJI", "Dow Jones"),
    ("^VIX", "VIX"),
]


def get_index_quote(symbol):
    """回 (price, change, change_pct) 或 None。"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = requests.get(
            url,
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        data = r.json()
        result = (data.get("chart", {}) or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta", {}) or {}
        price = meta.get("regularMarketPrice")
        prev = meta.get("previousClose") or meta.get("chartPreviousClose")
        if price is None or prev is None:
            return None
        change = price - prev
        pct = (change / prev * 100) if prev else 0
        return price, change, pct
    except Exception as e:
        print(f"指數抓取失敗 {symbol}: {e}")
        return None


def _format_price(value):
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def build_market_summary():
    """回 HTML 字串（LINE 收到時 _strip_html 後變純文字）。"""
    lines = ["<b>📊 大盤指數</b>"]
    for symbol, label in INDEX_LABELS:
        q = get_index_quote(symbol)
        if not q:
            lines.append(f"{label}｜N/A")
            continue
        price, change, pct = q
        emoji = "🟢" if change >= 0 else "🔴"
        sign = "+" if change >= 0 else ""
        lines.append(
            f"{emoji} {label}｜{_format_price(price)}｜"
            f"{sign}{change:,.2f}｜{sign}{pct:.2f}%"
        )
    return "\n".join(lines)
