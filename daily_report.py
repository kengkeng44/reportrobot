"""
每日報告（08:00 推送）：天氣 → 大盤指數 → 盤前報告。
持倉與個股改成 on-demand：使用者用 /仁和持股、/2330、/AAPL 等指令查。
週末略過盤前段，其他照發。
任一段失敗只 log，不中斷其他段推送。
"""

import traceback
from datetime import date
from weather import get_weather_report
from premarket import build_premarket_report
from line_sender import push_message


async def _push_safe(label, body_fn):
    """執行 body_fn() 拿段落字串；失敗只 log 不中斷整體流程。"""
    try:
        body = body_fn()
        if body:
            await push_message(body)
    except Exception as e:
        print(f"[{label}] 失敗：{e}")
        traceback.print_exc()


async def run_daily_report(force_premarket=False):
    print(f"開始執行每日情報... (force_premarket={force_premarket})")
    today = date.today().strftime("%Y-%m-%d")

    # 1. 天氣 + 近期活動
    def _weather():
        weather_msg, _ = get_weather_report()  # chart_path 不用，LINE 不傳圖
        return f"<b>🌅 每日情報</b>  {today}\n\n<b>🌤️ 天氣報告</b>\n\n{weather_msg}"
    await _push_safe("天氣", _weather)

    # 2. 盤前報告（含國際指數/匯率/三大法人/AI 重點；週末略過，force=True bypass）
    await _push_safe("盤前", lambda: build_premarket_report(force=force_premarket))

    print("每日情報傳送完成！")
