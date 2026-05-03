"""
解析使用者輸入文字，dispatch 到對應的查詢函式。
支援多種觸發：/2330、2330、查2330、AAPL、查AAPL、仁和持股、我的持股、持股 等。
"""

import re


_PORTFOLIO_KEYWORDS = {
    "仁和持股", "我的持股", "持股", "持倉", "我的股票",
    "portfolio", "Portfolio", "PORTFOLIO",
}

_HELP_KEYWORDS = {
    "help", "Help", "HELP", "說明", "指令", "幫助", "教學", "?", "？",
}

HELP_TEXT = (
    "🤖 喵管家指令清單\n"
    "\n"
    "📈 查股票（直接打代號）\n"
    "  • 台股：2330 / /2330 / 查2330\n"
    "  • 美股：AAPL / /aapl / 查TSLA\n"
    "  • ETF：00631L\n"
    "\n"
    "💼 查仁和持倉\n"
    "  • 仁和持股 / 我的持股 / 持股\n"
    "\n"
    "🆘 顯示這個說明\n"
    "  • help / 說明 / ?\n"
    "\n"
    "📅 每天 08:00 自動推送\n"
    "  • 🌤️ 淡水區天氣 + 近期活動\n"
    "  • 📊 盤前報告（週末略過）\n"
    "\n"
    "ℹ️ 一般聊天不會被當指令，家人聊天不會被打擾。\n"
    "查股票要等 5-10 秒（爬新聞 + AI 分析）。"
)

# 偵測前綴：開頭是 / 或「查」
_HAS_PREFIX_RE = re.compile(r"^\s*[/查]")
# 真正去掉前綴 + 內外空白
_STRIP_PREFIX_RE = re.compile(r"^\s*[/查]?\s*")

_TW_RE = re.compile(r"^(\d{4,6}[A-Z]?)$")               # 台股 4-6 位數字（可選一個英文）
_US_LOOSE_RE = re.compile(r"^([A-Za-z]{1,5})$")         # 帶前綴時：放寬大小寫
_US_STRICT_RE = re.compile(r"^([A-Z]{2,5})$")           # 不帶前綴：全大寫且 ≥ 2 字
                                                          # 避免 'hi'/'ok' 等日常字觸發
_CJK_RE = re.compile(r"[一-鿿]")                # 中日韓統一漢字


def _strip_prefix(text):
    if not text:
        return ""
    return _STRIP_PREFIX_RE.sub("", text).strip()


def _find_tw_ticker_by_name(query):
    """從 twstock 對照表反查包含 query 的 ticker；多個 match 取最短代號（通常是主要的）。"""
    if not query or not _CJK_RE.search(query):
        return None
    try:
        import twstock
        candidates = [code for code, info in twstock.codes.items()
                      if info.name and query in info.name]
        if not candidates:
            return None
        # 過濾掉超過 6 位的（權證、特殊金融商品代號通常 6 位以上）
        normal = [c for c in candidates if len(c) <= 6]
        pool = normal or candidates
        return min(pool, key=len)
    except Exception as e:
        print(f"twstock 中文名查詢失敗 ({query}): {e}")
        return None


def parse(text):
    """回 (kind, arg) 或 None。kind ∈ {'help', 'portfolio', 'stock'}。"""
    if not text:
        return None
    has_prefix = bool(_HAS_PREFIX_RE.match(text))
    cleaned = _strip_prefix(text)
    if not cleaned:
        return None

    if cleaned in _HELP_KEYWORDS:
        return ("help", None)

    if cleaned in _PORTFOLIO_KEYWORDS:
        return ("portfolio", None)

    if _TW_RE.match(cleaned):
        return ("stock", cleaned)

    # 美股：帶前綴接受任意大小寫；不帶前綴必須全大寫且 ≥ 2 字
    if has_prefix:
        m = _US_LOOSE_RE.match(cleaned)
        if m:
            return ("stock", cleaned.upper())
    else:
        m = _US_STRICT_RE.match(cleaned)
        if m:
            return ("stock", cleaned)

    # 中文公司名 → 反查 twstock 拿 ticker（例：/鼎天 → 3306、台積 → 2330）
    # 必須帶前綴 / 或 查 才接受，避免家人講「我有買台積」誤觸發
    if has_prefix and _CJK_RE.search(cleaned):
        ticker = _find_tw_ticker_by_name(cleaned)
        if ticker:
            return ("stock", ticker)

    return None  # 不認得就靜默不回應，避免騷擾家人聊天


def handle(text):
    """parse + dispatch；回字串（給 reply_message 直接送）或 None。"""
    parsed = parse(text)
    if not parsed:
        return None

    kind, arg = parsed
    try:
        if kind == "help":
            return HELP_TEXT

        if kind == "portfolio":
            from gmail_reader import get_portfolio_from_gmail
            from portfolio import build_portfolio_summary
            portfolio = get_portfolio_from_gmail()
            summary = build_portfolio_summary(portfolio)
            return summary or "目前無持倉資料"

        if kind == "stock":
            from stock_news import get_stock_report
            return get_stock_report(arg)
    except Exception as e:
        print(f"指令處理失敗 ({kind}/{arg})：{e}")
        import traceback; traceback.print_exc()
        return f"查詢失敗：{e}"

    return None
