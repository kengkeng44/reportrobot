"""
LINE Messaging API push 模組（並送用，純文字、不傳圖）。
- 目標：群組（LINE_GROUP_ID）
- HTML 標籤會去掉，<a href="x">y</a> 會被攤成 "y (x)"
- 未設 LINE_CHANNEL_TOKEN 或 LINE_GROUP_ID 時靜默跳過（讓 Telegram 單獨運作）
"""

import os
import re
import requests
from html import unescape


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


LINE_CHANNEL_TOKEN = _env("LINE_CHANNEL_TOKEN")
LINE_GROUP_ID = _env("LINE_GROUP_ID")

API_URL = "https://api.line.me/v2/bot/message/push"
MAX_CHARS = 4500  # LINE 文字訊息上限 5000，留一點 buffer


def _strip_html(text):
    """LINE 不支援 HTML：把 <a href="x">y</a> → "y (x)"，其他 tag 直接拿掉。"""
    if not text:
        return ""
    text = re.sub(
        r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>',
        lambda m: f"{m.group(2)} ({m.group(1)})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


async def send_message(text):
    """並送用：把 Telegram HTML 文字轉純文字，push 到 LINE 群組。"""
    if not (LINE_CHANNEL_TOKEN and LINE_GROUP_ID):
        return  # LINE 沒設定就不送，不影響 Telegram
    plain = _strip_html(text)
    if not plain:
        return
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
    }
    # 超長就切片送多則
    for i in range(0, len(plain), MAX_CHARS):
        chunk = plain[i:i + MAX_CHARS]
        try:
            r = requests.post(
                API_URL,
                json={"to": LINE_GROUP_ID,
                      "messages": [{"type": "text", "text": chunk}]},
                headers=headers,
                timeout=10,
            )
            if r.status_code != 200:
                print(f"LINE push 失敗 {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"LINE send_message 例外：{e}")


async def send_photo(image_path, caption=None):
    """LINE 不傳圖（圖由 Telegram 負責），這裡 no-op。"""
    return
