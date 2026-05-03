"""
每日報告（08:00 推送）：天氣 → 持倉 → 大盤指數 → 盤前報告。
個股新聞改成讓使用者用 /2330 /AAPL 等指令查。
週末略過盤前段，其他照發。
"""

from datetime import date
from gmail_reader import get_portfolio_from_gmail
from weather import get_weather_report
from markets import build_market_summary
from portfolio import build_portfolio_summary
from premarket import build_premarket_report
from line_sender import push_message


SEP = "━━━━━━━━━━━━━━━━━"


async def run_daily_report():
    print("開始執行每日情報...")
    today = date.today().strftime("%Y-%m-%d")

    # 1. 從 Gmail 抓持倉
    portfolio = get_portfolio_from_gmail()

    # 2. 天氣（含近期活動）
    weather_msg, _ = get_weather_report()  # chart_path 不用了，LINE 不傳圖
    await push_message(
        f"<b>🌅 每日情報</b>  {today}\n\n"
        f"{SEP}\n<b>🌤️ 天氣報告</b>\n{SEP}\n\n{weather_msg}"
    )

    # 3. 持倉概覽
    summary = build_portfolio_summary(portfolio)
    if summary:
        await push_message(
            f"{SEP}\n<b>💼 股票情報</b>\n{SEP}\n{summary}"
        )

    # 4. 大盤指數（簡版，每天都發）
    market = build_market_summary()
    await push_message(f"{SEP}\n{market}\n{SEP}")

    # 5. 盤前報告（完整版，週末略過）
    premarket = build_premarket_report()
    if premarket:
        await push_message(f"{SEP}\n{premarket}\n{SEP}")

    print("每日情報傳送完成！")
