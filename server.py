"""
長駐 web service：
- POST /line/webhook：接 LINE Messaging API 訊息事件，dispatch 到 command_router
- 背景 scheduler：每天 UTC 00:00（台北 08:00）跑 run_daily_report
- GET /：健康檢查
"""

import base64
import hashlib
import hmac
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException, Request

import command_router
from daily_report import run_daily_report
from line_sender import reply_message


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


LINE_CHANNEL_SECRET = _env("LINE_CHANNEL_SECRET")
DAILY_CRON = os.environ.get("DAILY_CRON", "0 0 * * *")  # 預設 UTC 00:00 = 台北 08:00


scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 啟動時掛排程
    minute, hour, day, month, dow = DAILY_CRON.split()
    scheduler.add_job(
        run_daily_report,
        CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow),
        id="daily_report",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    print(f"Scheduler 啟動，每日報排程：{DAILY_CRON} (UTC)")
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


def verify_line_signature(body: bytes, signature: str | None) -> bool:
    """LINE webhook 簽章驗證；沒設 secret 就 skip（dev only）。"""
    if not LINE_CHANNEL_SECRET:
        print("⚠️ LINE_CHANNEL_SECRET 未設定，跳過簽章驗證")
        return True
    if not signature:
        return False
    h = hmac.new(LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(h).decode()
    return hmac.compare_digest(expected, signature)


@app.get("/")
async def root():
    return {"status": "ok", "service": "reportrobot"}


@app.get("/admin/env-check")
async def env_check():
    """Server 看到的環境變數狀態（只回 set/len，不洩漏值）。Debug 用。"""
    keys = [
        "ADMIN_TOKEN",
        "LINE_CHANNEL_TOKEN",
        "LINE_CHANNEL_SECRET",
        "LINE_GROUP_ID",
        "GMAIL_USER",
        "TOKEN_PICKLE_B64",
        "ANTHROPIC_API_KEY",
        "CWA_API_KEY",
        "OWM_API_KEY",
        "PDF_PASSWORD_PREFIX",
        "MANUAL_STOCKS",
        "WEATHER_LOCATIONS",
        "PYTHONUNBUFFERED",
        "TZ",
    ]
    return {
        k: {
            "set": bool(os.environ.get(k)),
            "len": len(os.environ.get(k, "")),
        }
        for k in keys
    }


@app.post("/line/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: str | None = Header(None),
):
    body = await request.body()

    if not verify_line_signature(body, x_line_signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    events = payload.get("events", []) or []

    for event in events:
        if event.get("type") != "message":
            continue
        msg = event.get("message", {}) or {}
        if msg.get("type") != "text":
            continue
        text = msg.get("text", "")
        reply_token = event.get("replyToken")
        if not reply_token:
            continue

        response = command_router.handle(text)
        if response:
            print(f"LINE 指令命中：{text[:30]} → 回覆 {len(response)} 字")
            await reply_message(reply_token, response)
        # 沒命中就靜默不回，避免騷擾家人聊天

    return {"ok": True}


@app.post("/admin/run-daily")
async def trigger_daily(request: Request, force: int = 0):
    """手動觸發每日報。?force=1 會 bypass 週末略過盤前段的檢查（測試用）。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin trigger disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    await run_daily_report(force_premarket=bool(force))
    return {"ok": True, "force_premarket": bool(force)}
