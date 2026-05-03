"""
盤前報告（每日 08:00 推、週末略過）：
- 國際指數隔夜收盤（含費半 SOX）
- 重要 ADR 與盤後價（TSMC / NVIDIA）
- 匯率與原物料（USD/TWD、DXY、USD/JPY、油、金）
- 三大法人買賣超
- AI web_search 整理 Fed / 總經 / 地緣 / 類股 / 法說會
"""

import os
from datetime import date

import anthropic

from chips import get_institutional_trades
from markets import _format_price, get_index_quote


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")


# 國際指數（隔夜美股 + 費半）
INTL_INDICES = [
    ("^DJI", "Dow"),
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq"),
    ("^SOX", "費半"),
]

# 重要 ADR / 美股科技股盤後
ADR_STOCKS = [
    ("TSM", "TSMC ADR"),
    ("NVDA", "NVIDIA"),
]

# 匯率
FX_LIST = [
    ("TWD=X", "USD/TWD"),
    ("DX-Y.NYB", "美元指數"),
    ("JPY=X", "USD/JPY"),
]

# 原物料
COMMODITIES = [
    ("CL=F", "原油"),
    ("GC=F", "黃金"),
]


def is_weekend():
    return date.today().weekday() >= 5  # Sat=5, Sun=6


def _quote_line(symbol, label):
    q = get_index_quote(symbol)
    if not q:
        return f"{label}｜N/A"
    price, change, pct = q
    emoji = "🟢" if change >= 0 else "🔴"
    sign = "+" if change >= 0 else ""
    return (
        f"{emoji} {label}｜{_format_price(price)}｜"
        f"{sign}{change:,.2f}｜{sign}{pct:.2f}%"
    )


def _format_chip(value):
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}"


def _build_chip_block():
    chips = get_institutional_trades()
    if not chips:
        return "N/A（資料尚未公布或抓取失敗）"
    lines = [f"📅 {chips['date']} 收盤"]
    for label, key in [("外資", "foreign"), ("投信", "investment_trust"), ("自營商", "dealer")]:
        v = chips.get(key)
        if v is None:
            continue
        emoji = "🟢" if v >= 0 else "🔴"
        lines.append(f"{emoji} {label}｜{_format_chip(v)} 億元")
    return "\n".join(lines)


def _build_ai_summary():
    """用 Claude web_search 整理盤前重點。失敗回空字串。"""
    today = date.today().strftime("%Y-%m-%d")
    prompt = (
        f"今天是 {today}（台北時間），請用網路搜尋整理今日台股開盤前重點。\n"
        f"輸出格式：純文字繁體中文，不要 Markdown，每點 `• ` 開頭，總共 5-7 條：\n\n"
        f"請涵蓋以下面向（找不到資訊就跳過該面向，不要編造）：\n"
        f"1. 美聯準會（Fed）動向：近期談話、會議紀要、利率機率變化\n"
        f"2. 重要經濟數據：近期已公布或本週將公布的 CPI/PPI/非農/PMI/GDP\n"
        f"3. 地緣政治與重大事件：貿易戰、關稅、戰爭、選舉等對股市可能有影響\n"
        f"4. 強勢/弱勢類股輪動：昨日台股或美股的明顯資金流向\n"
        f"5. 今日台股召開法說會的重要公司（如有）\n\n"
        f"規則：\n"
        f"- 每點 1-2 句話，總共 5-7 條 bullet（• 開頭）\n"
        f"- 直接列出 bullet，不要開場白與結語"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=900,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 4,
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in message.content:
            if getattr(block, "type", None) == "text":
                text = block.text
        return text.strip()
    except Exception as e:
        print(f"AI 盤前整理失敗：{e}")
        return ""


def build_premarket_report():
    """組成盤前報告 HTML 字串；週末回 None（呼叫端會 skip）。"""
    if is_weekend():
        print("週末，盤前報告 skip")
        return None

    intl_lines = [_quote_line(s, l) for s, l in INTL_INDICES + ADR_STOCKS]
    fx_lines = [_quote_line(s, l) for s, l in FX_LIST + COMMODITIES]
    chip_block = _build_chip_block()
    ai_block = _build_ai_summary()

    sections = [
        "<b>📊 盤前報告</b>",
        "<b>🌍 國際市場（隔夜）</b>\n" + "\n".join(intl_lines),
        "<b>💱 匯率與原物料</b>\n" + "\n".join(fx_lines),
        "<b>🏛️ 三大法人買賣超</b>\n" + chip_block,
    ]
    if ai_block:
        sections.append(f"<b>🧠 盤前重點</b>\n{ai_block}")
    return "\n\n".join(sections)
