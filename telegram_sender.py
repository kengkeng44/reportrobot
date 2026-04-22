"""
Telegram 傳送模組
- 支援 HTML 格式（粗體、斜體、連結）
- 支援傳送圖片
"""

import telegram
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

async def send_message(text):
    """傳送 HTML 格式訊息"""
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    max_length = 4000
    chunks = [text[i:i+max_length] for i in range(0, len(text), max_length)]

    for chunk in chunks:
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=chunk,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        except Exception as e:
            # HTML 格式有錯時，改用純文字傳送
            print(f"HTML 傳送失敗，改用純文字：{e}")
            # 移除 HTML 標籤
            import re
            clean = re.sub(r'<[^>]+>', '', chunk)
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=clean
            )
    print(f"已傳送 {len(chunks)} 則訊息")

async def send_photo(photo_path, caption=""):
    """傳送圖片"""
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    try:
        with open(photo_path, 'rb') as photo:
            await bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=photo,
                caption=caption,
                parse_mode='HTML'
            )
        print(f"已傳送圖片：{photo_path}")
    except Exception as e:
        print(f"圖片傳送失敗：{e}")
