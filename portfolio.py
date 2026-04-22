"""
持倉概覽：用 Yahoo Finance 抓現價，算市值、損益、損益%，格式化成 Telegram 區塊。
"""

import requests
from stock_news import get_stock_name


def _to_yahoo_symbol(ticker):
    return f"{ticker}.TW" if ticker.isdigit() else ticker


def get_live_price(ticker):
    """用 Yahoo Finance v8 chart API 抓最新股價，失敗回傳 None。"""
    symbol = _to_yahoo_symbol(ticker)
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        resp = requests.get(
            url,
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        data = resp.json()
        result = (data.get('chart', {}) or {}).get('result') or []
        if not result:
            return None
        meta = result[0].get('meta', {}) or {}
        return meta.get('regularMarketPrice') or meta.get('previousClose')
    except Exception as e:
        print(f"  Yahoo 股價失敗 {symbol}：{e}")
        return None


def _format_price(value, is_us):
    if value is None:
        return "N/A"
    prefix = "$" if is_us else ""
    if abs(value) >= 100:
        return f"{prefix}{value:,.0f}"
    return f"{prefix}{value:,.2f}"


def _format_pnl_amount(value, is_us):
    sign = "+" if value >= 0 else "-"
    prefix = "$" if is_us else ""
    return f"{sign}{prefix}{abs(value):,.0f}"


def build_portfolio_summary(portfolio):
    """
    portfolio: {ticker: {'shares': N, 'avg_cost': X}}
    回傳 HTML 字串（Telegram 用），若無持倉回傳空字串。
    """
    if not portfolio:
        return ""

    lines = ["<b>📊 持倉概覽</b>"]
    rows = []
    for ticker, p in portfolio.items():
        is_us = not ticker.isdigit()
        shares = p['shares']
        avg_cost = p['avg_cost']
        current = get_live_price(ticker)
        name = get_stock_name(ticker)

        if current is None:
            rows.append({
                'sort_key': shares * avg_cost,
                'line': f"{ticker} {name}｜{shares}股｜均價{_format_price(avg_cost, is_us)}｜現價N/A",
            })
            continue

        cost_value = shares * avg_cost
        market_value = shares * current
        pnl = market_value - cost_value
        pnl_pct = (current - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
        sign = "+" if pnl_pct >= 0 else ""

        line = (
            f"{ticker} {name}｜{shares}股"
            f"｜均價{_format_price(avg_cost, is_us)}"
            f"｜現價{_format_price(current, is_us)}"
            f"｜{sign}{pnl_pct:.1f}% {_format_pnl_amount(pnl, is_us)}"
        )
        rows.append({'sort_key': market_value, 'line': line})

    rows.sort(key=lambda r: r['sort_key'], reverse=True)
    lines.extend(r['line'] for r in rows)
    return "\n".join(lines)
