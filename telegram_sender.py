"""
Telegram 傳送模組
- 支援 HTML 格式（粗體、斜體、連結）
- 支援傳送圖片
- 支援多收件者（TELEGRAM_CHAT_IDS 逗號分隔）
"""

import os
import telegram


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
TELEGRAM_CHAT_IDS = _env_list("TELEGRAM_CHAT_IDS")


async def send_message(text):
    """傳送 HTML 格式訊息給所有收件者"""
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    max_length = 4000
    chunks = [text[i:i+max_length] for i in range(0, len(text), max_length)]

    for chat_id in TELEGRAM_CHAT_IDS:
        for chunk in chunks:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
            except Exception as e:
                print(f"HTML 傳送失敗（{chat_id}），改用純文字：{e}")
                import re
                clean = re.sub(r'<[^>]+>', '', chunk)
                await bot.send_message(chat_id=chat_id, text=clean)
    print(f"已傳送 {len(chunks)} 則訊息 × {len(TELEGRAM_CHAT_IDS)} 人")


async def send_photo(photo_path, caption=""):
    """傳送圖片給所有收件者"""
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            with open(photo_path, 'rb') as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    parse_mode='HTML'
                )
            print(f"已傳送圖片給 {chat_id}：{photo_path}")
        except Exception as e:
            print(f"圖片傳送失敗（{chat_id}）：{e}")
