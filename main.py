"""
情報機器人 v2 - 主程式
- HTML 格式（粗體、連結）
- 天氣折線圖
- PTT 熱門排序 + 可點擊連結
"""

import asyncio
import time
from datetime import date
from gmail_reader import get_portfolio_from_gmail
from weather import get_weather_report
from stock_news import get_stock_report
from portfolio import build_portfolio_summary
from telegram_sender import send_message, send_photo
from config import MANUAL_STOCKS

def run_daily_report():
    print("開始執行每日情報...")
    today = date.today().strftime("%Y-%m-%d")

    # 1. 從 Gmail 抓持倉（完整 {ticker: {shares, avg_cost}}）
    portfolio = get_portfolio_from_gmail()

    # 從持倉挑前 3 大 + 手動股票合併為要追新聞的清單
    sorted_by_cost = sorted(
        portfolio.items(),
        key=lambda x: x[1]['shares'] * x[1]['avg_cost'],
        reverse=True,
    )
    gmail_top3 = [t for t, _ in sorted_by_cost[:3]]
    all_stocks = list(dict.fromkeys(gmail_top3 + MANUAL_STOCKS))
    print(f"追蹤股票：{all_stocks}")

    # 2. 天氣報告
    weather_msg, chart_path = get_weather_report()
    weather_full = f"""<b>🌅 每日情報</b>  {today}

━━━━━━━━━━━━━━━━━
<b>🌤️ 天氣報告</b>
━━━━━━━━━━━━━━━━━

{weather_msg}"""
    asyncio.run(send_message(weather_full))

    if chart_path:
        asyncio.run(send_photo(chart_path, caption="📈 今日氣溫變化"))

    # 3. 持倉概覽（Yahoo 現價 + 損益）
    portfolio_summary = build_portfolio_summary(portfolio)
    if portfolio_summary:
        asyncio.run(send_message(
            f"""━━━━━━━━━━━━━━━━━
<b>💼 股票情報</b>
━━━━━━━━━━━━━━━━━
{portfolio_summary}"""
        ))
        time.sleep(1)

    # 4. 每檔股票個別情報
    for stock_id in all_stocks:
        stock_msg = get_stock_report(stock_id)
        asyncio.run(send_message(stock_msg))
        time.sleep(1)

    print("每日情報傳送完成！")

if __name__ == "__main__":
    run_daily_report()
