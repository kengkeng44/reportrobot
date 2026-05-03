"""
本地手動測試入口：python main.py 跑一次每日報。
正式部署用 server.py + uvicorn，cron 由 apscheduler 排程。
"""

import asyncio
from daily_report import run_daily_report


if __name__ == "__main__":
    asyncio.run(run_daily_report())
