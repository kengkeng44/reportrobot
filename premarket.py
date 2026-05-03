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


def _format_pct(pct):
    """漲跌百分比格式化成固定寬度，並在百分比區段前墊半形空白讓視覺對齊。
    例： '+0.34%' / '-1.20%' / '+12.5%' / '-100%'。
    LINE 字型非等寬，無法完美對齊，但 % 永遠在第 6 字內。"""
    sign = "+" if pct >= 0 else "-"
    abs_pct = abs(pct)
    if abs_pct >= 100:
        body = f"{abs_pct:.0f}%"
    elif abs_pct >= 10:
        body = f"{abs_pct:.1f}%"
    else:
        body = f"{abs_pct:.2f}%"
    return f"{sign}{body}"


def _quote_line(symbol, label):
    q = get_index_quote(symbol)
    if not q:
        return f"⚪ ─────｜{label}｜N/A"
    price, change, pct = q
    emoji = "🟢" if change >= 0 else "🔴"
    pct_str = _format_pct(pct)
    return f"{emoji} {pct_str}｜{label}｜{_format_price(price)}"


def _format_chip(value):
    """三大法人金額：±NNN.NN 億，固定寬度方便視覺對齊。"""
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value):.2f} 億"


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
        lines.append(f"{emoji} {_format_chip(v)}｜{label}")
    return "\n".join(lines)


def _build_ai_summary():
    """用 Claude web_search 整理盤前重點。失敗回空字串。"""
    today = date.today().strftime("%Y-%m-%d")
    prompt = (
        f"今天是 {today}（台北時間）。請用網路搜尋整理今日台股開盤前重點。\n"
        f"輸出 8-10 條 bullet，每點 `• ` 開頭，純文字繁體中文，不要 Markdown。\n\n"
        f"請涵蓋以下面向（找不到資訊就跳過該面向，不要編造）：\n"
        f"1. 美聯準會（Fed）動向：近期談話、會議紀要、利率機率變化\n"
        f"2. 重要經濟數據：近期已公布或本週將公布的 CPI/PPI/非農/PMI/GDP/零售銷售\n"
        f"3. 地緣政治與重大事件：貿易戰、關稅、戰爭、選舉、央行政策對股市的影響\n"
        f"4. 強勢/弱勢類股輪動：昨夜美股與最近台股的明顯資金流向（AI/半導體/PCB/航運/金融等）\n"
        f"5. 重要個股動態：權值股法說重點、財報、併購、減資等\n"
        f"6. 今日台股召開法說會的重要公司（如有）\n"
        f"7. 美股盤後/盤前重要科技股表現（NVDA/TSM/AAPL/MSFT/AMD 等）\n"
        f"8. 國際原物料、加密貨幣異動（油金、比特幣，如果有顯著變化）\n\n"
        f"規則：\n"
        f"- 每點 1-2 句話，要有具體數字或事件名稱（不要寫「市場關注 Fed」這種空話）\n"
        f"- 8-10 條 bullet，重要面向多列，不重要的面向就略過\n"
        f"- 直接列出 bullet，禁止開場白與結語"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 5,
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


def build_premarket_report(force=False):
    """組成盤前報告 HTML 字串；週末回 None（呼叫端會 skip）。force=True 強跑。"""
    if is_weekend() and not force:
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
