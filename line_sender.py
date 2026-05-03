"""
LINE Messaging API 模組：
- push_message(text)：推播到群組（每日報用）
- reply_message(reply_token, text)：回覆使用者訊息（webhook 互動用）
- 兩者都會自動 strip Telegram 風格的 HTML <b>/<a>，並切片送多則
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

PUSH_URL = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
MAX_CHARS = 4500       # 單則文字上限（LINE 5000，留 buffer）
MAX_MESSAGES = 5       # 一次最多 5 則訊息（LINE limit）


def _headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
    }


def _strip_html(text):
    """LINE 不支援 HTML：<a href="x">y</a> → "y (x)"，其他 tag 拿掉。"""
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


def _chunks(text):
    """切成 LINE 單則上限的 chunks，最多 MAX_MESSAGES 則。"""
    plain = _strip_html(text)
    if not plain:
        return []
    return [plain[i:i + MAX_CHARS] for i in range(0, len(plain), MAX_CHARS)][:MAX_MESSAGES]


def _post(url, payload):
    try:
        r = requests.post(url, json=payload, headers=_headers(), timeout=10)
        if r.status_code != 200:
            print(f"LINE {url.rsplit('/', 1)[-1]} 失敗 {r.status_code}: {r.text[:300]}")
            return False
        return True
    except Exception as e:
        print(f"LINE post 例外：{e}")
        return False


async def push_message(text):
    """推播到群組（每日報用）。LINE 沒設定就 no-op。"""
    if not (LINE_CHANNEL_TOKEN and LINE_GROUP_ID):
        return
    chunks = _chunks(text)
    if not chunks:
        return
    # push 一次只能對一個 to，多 chunks 拆多次 request
    for chunk in chunks:
        _post(PUSH_URL, {
            "to": LINE_GROUP_ID,
            "messages": [{"type": "text", "text": chunk}],
        })


async def reply_message(reply_token, text):
    """回覆使用者訊息（webhook 互動用）。reply_token 只能用 1 次、有效 30 秒。"""
    if not (LINE_CHANNEL_TOKEN and reply_token):
        return
    chunks = _chunks(text)
    if not chunks:
        return
    # reply API 一次最多 5 則，全塞同一 request
    _post(REPLY_URL, {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": c} for c in chunks],
    })


# ── 舊介面相容（main.py 過渡期）──
async def send_message(text):
    """向後相容：等同 push_message。"""
    await push_message(text)


async def send_photo(image_path, caption=None):
    """LINE 不傳圖，no-op。"""
    return
