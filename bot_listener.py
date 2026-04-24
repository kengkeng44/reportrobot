"""
家人互動機器人 - 聽訊息、回股票情報
用法：家人傳一個 4 位數字台股代號（例：2330），回該股票完整情報。

這是長駐程序（polling），跟 main.py 的每日 cron 是兩個分開的 Railway service。
只對 TELEGRAM_CHAT_IDS 白名單內的 chat 回應。
"""

import asyncio
import os
import re

import telegram
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from stock_news import get_stock_report


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    import config
    return getattr(config, name)


def _env_list(name):
    val = os.environ.get(name)
    if val:
        return [x.strip() for x in val.split(",") if x.strip()]
    import config
    raw = getattr(config, name)
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return list(raw)


TELEGRAM_TOKEN = _env("TELEGRAM_TOKEN")
ALLOWED_CHAT_IDS = set(_env_list("TELEGRAM_CHAT_IDS"))

# 台股代號：4 位數字（抓第一個出現的）
STOCK_CODE_RE = re.compile(r'(?<!\d)(\d{4})(?!\d)')

HELP_TEXT = (
    "👋 歡迎使用情報機器人！\n\n"
    "直接傳送台股代號（4 位數字），例如：\n"
    "  <code>2330</code>   → 台積電\n"
    "  <code>2317</code>   → 鴻海\n\n"
    "我會幫你抓新聞、論壇熱門討論、AI 分析，約 30 秒回覆。"
)


async def _send_long_html(message, text):
    """Telegram 4096 字限制，分段 HTML 回。"""
    max_length = 4000
    for i in range(0, len(text), max_length):
        chunk = text[i:i + max_length]
        try:
            await message.reply_html(chunk, disable_web_page_preview=True)
        except Exception as e:
            print(f"HTML 回覆失敗，改純文字：{e}")
            clean = re.sub(r'<[^>]+>', '', chunk)
            await message.reply_text(clean)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    chat_id = str(update.effective_chat.id)
    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        print(f"拒絕未授權 chat_id={chat_id}")
        return

    text = message.text.strip()
    if text.lower() in ("/start", "/help", "help"):
        await message.reply_html(HELP_TEXT)
        return

    match = STOCK_CODE_RE.search(text)
    if not match:
        await message.reply_html(
            "請傳送 4 位數台股代號（例：<code>2330</code>）。\n"
            "或傳 <code>/help</code> 看說明。"
        )
        return

    stock_id = match.group(1)
    await message.reply_text(f"🔍 查詢 {stock_id} 中，大約 30 秒...")

    try:
        # get_stock_report 會呼叫多個 API（阻塞 I/O）與 Claude，
        # 丟到 thread 避免卡住 event loop。
        report = await asyncio.to_thread(get_stock_report, stock_id)
        await _send_long_html(message, report)
    except Exception as e:
        print(f"查詢 {stock_id} 失敗：{e}")
        await message.reply_text(f"❌ 查詢 {stock_id} 失敗：{e}")


def main():
    print(f"啟動互動機器人，授權 chat_id：{ALLOWED_CHAT_IDS or '（全開放）'}")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.COMMAND, handle_message))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
