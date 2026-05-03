"""
每日報告（08:00 推送）：天氣 → 大盤指數 → 盤前報告。
持倉與個股改成 on-demand：使用者用 /仁和持股、/2330、/AAPL 等指令查。
週末略過盤前段，其他照發。
"""

from datetime import date
from weather import get_weather_report
from markets import build_market_summary
from premarket import build_premarket_report
from line_sender import push_message


SEP = "━━━━━━━━━━━━━━━━━"


async def run_daily_report():
    print("開始執行每日情報...")
    today = date.today().strftime("%Y-%m-%d")

    # 1. 天氣（含近期活動）
    weather_msg, _ = get_weather_report()  # chart_path 不用了，LINE 不傳圖
    await push_message(
        f"<b>🌅 每日情報</b>  {today}\n\n"
        f"{SEP}\n<b>🌤️ 天氣報告</b>\n{SEP}\n\n{weather_msg}"
    )

    # 2. 大盤指數（簡版，每天都發）
    market = build_market_summary()
    await push_message(f"{SEP}\n{market}\n{SEP}")

    # 3. 盤前報告（完整版，週末略過）
    premarket = build_premarket_report()
    if premarket:
        await push_message(f"{SEP}\n{premarket}\n{SEP}")

    print("每日情報傳送完成！")
