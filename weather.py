"""
天氣模組 v2
- 中央氣象署為主
- OpenWeatherMap 輔助
- matplotlib 畫溫度折線圖
- AI 整理報告（不含來源狀態表）
"""

import os
import tempfile
import requests
import anthropic
import matplotlib
matplotlib.use('Agg')  # 無視窗環境
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from datetime import datetime, timedelta
from prompts import WEATHER_PROMPT


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
    return getattr(config, name)


CWA_API_KEY = _env("CWA_API_KEY")
OWM_API_KEY = _env("OWM_API_KEY")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
WEATHER_LOCATIONS = _env_list("WEATHER_LOCATIONS")

# 嘗試使用中文字體
def get_chinese_font():
    """找到可用的中文字體（fallback_to_default=False 才不會被 mpl 偷偷塞 DejaVu Sans）"""
    font_candidates = [
        'Noto Sans CJK TC', 'Noto Sans TC', 'Noto Sans CJK SC',
        'Microsoft JhengHei', 'Microsoft YaHei',
        'PingFang TC', 'WenQuanYi Micro Hei', 'SimHei', 'Arial Unicode MS',
    ]
    for font_name in font_candidates:
        try:
            font_path = fm.findfont(
                fm.FontProperties(family=font_name),
                fallback_to_default=False,
            )
            if font_path:
                return fm.FontProperties(fname=font_path)
        except (ValueError, RuntimeError):
            continue
    return fm.FontProperties()

def _extract_element_value(ev_list):
    """從 ElementValue/elementValue 陣列取出值（新 API 有 Temperature/WindSpeed 等各種鍵名）。"""
    if not ev_list:
        return ''
    ev = ev_list[0] if isinstance(ev_list, list) else ev_list
    if not isinstance(ev, dict):
        return ''
    if 'value' in ev:
        return ev.get('value', '')
    for v in ev.values():
        if v not in (None, ''):
            return v
    return ''


def get_cwa_weather():
    """
    主：新北市鄉鎮逐 3 小時預報 F-D0047-071，直接用 LocationName 篩淡水區、金山區。
    使用 v1 REST API 新版大寫欄位（LocationName / WeatherElement / ElementName / Time / ElementValue）。
    """
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-071"
        params = {
            "Authorization": CWA_API_KEY,
            "LocationName": ",".join(WEATHER_LOCATIONS),
            # 省略 ElementName — F-D0047-071 的正確欄位是「平均溫度/最高溫度/最低溫度/
            # 平均相對濕度/最高體感溫度/最低體感溫度/風速/風向/12小時降雨機率/天氣現象/天氣預報綜合描述」等，
            # 不帶參數就一次拿全部，避免名稱不符被 CWA 靜默丟掉。
        }
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()

        records = data.get('records', {})
        locations_wrapper = records.get('Locations') or records.get('locations') or []
        if not locations_wrapper:
            print(f"CWA F-D0047-071 回傳無 Locations：{str(data)[:200]}")
            return {}
        first = locations_wrapper[0] if isinstance(locations_wrapper, list) else locations_wrapper
        locations = first.get('Location') or first.get('location') or []

        results = {}
        for loc in locations:
            name = loc.get('LocationName') or loc.get('locationName', '')
            if name not in WEATHER_LOCATIONS:
                continue
            elements = {}
            for elem in (loc.get('WeatherElement') or loc.get('weatherElement') or []):
                elem_name = elem.get('ElementName') or elem.get('elementName', '')
                time_data = []
                for t in (elem.get('Time') or elem.get('time') or []):
                    dt = (t.get('DataTime') or t.get('dataTime')
                          or t.get('StartTime') or t.get('startTime', ''))
                    value = _extract_element_value(
                        t.get('ElementValue') or t.get('elementValue')
                    )
                    time_data.append({'time': dt, 'value': value})
                elements[elem_name] = time_data
            results[name] = elements

        if results:
            print(f"CWA F-D0047-071 成功，抓到 {list(results.keys())}")
        return results
    except Exception as e:
        print(f"CWA F-D0047-071 失敗：{e}")
        return {}


def get_cwa_weather_fallback():
    """
    備用：F-C0032-001（36小時預報）— 只有縣市層級，新北市共用給淡水、金山。
    回傳結構跟主方案一致，方便下游共用。
    """
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
        params = {"Authorization": CWA_API_KEY, "locationName": "新北市"}
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()

        locations = data.get('records', {}).get('location', [])
        if not locations:
            print(f"CWA F-C0032-001 回傳空：{str(data)[:200]}")
            return {}

        loc = locations[0]
        elements = {}
        for elem in loc.get('weatherElement', []):
            elem_name = elem.get('elementName', '')
            time_data = []
            for t in elem.get('time', []):
                param = t.get('parameter', {}) or {}
                time_data.append({
                    'time': t.get('startTime', ''),
                    'value': param.get('parameterName', ''),
                })
            elements[elem_name] = time_data

        # 新北市的預報同時套用到淡水、金山兩個顯示名稱
        results = {name: elements for name in WEATHER_LOCATIONS}
        print(f"CWA F-C0032-001 備用成功，共用給 {list(results.keys())}")
        return results
    except Exception as e:
        print(f"CWA F-C0032-001 備用也失敗：{e}")
        return {}

def get_owm_weather():
    """抓 OpenWeatherMap 天氣"""
    coords = {
        "淡水區": {"lat": 25.1692, "lon": 121.4418},
        "金山區": {"lat": 25.2025, "lon": 121.6418},
    }
    results = {}
    for name, coord in coords.items():
        if name not in WEATHER_LOCATIONS:
            continue
        try:
            url = "https://api.openweathermap.org/data/2.5/forecast"
            params = {
                "lat": coord["lat"], "lon": coord["lon"],
                "appid": OWM_API_KEY, "units": "metric",
                "lang": "zh_tw", "cnt": 8
            }
            resp = requests.get(url, params=params, timeout=10)
            results[name] = resp.json()
        except Exception as e:
            print(f"OWM 失敗 ({name})：{e}")
    return results

def _parse_cwa_time(time_str):
    """CWA 通常給帶 +08:00 的本地時間；都當本地時間解析。"""
    if not time_str:
        return None
    s = time_str.replace('Z', '').split('+')[0].strip()
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _pop_for(point_time, pop_data, window_hours=12):
    """給一個 datetime，從 12 小時降雨機率資料找對應段；找不到回 None。"""
    if not point_time or not pop_data:
        return None
    for p in pop_data:
        start = _parse_cwa_time(p.get('time', ''))
        if start is None:
            continue
        end = start + timedelta(hours=window_hours)
        if start <= point_time < end:
            try:
                return int(float(p.get('value')))
            except (TypeError, ValueError):
                return None
    return None


def generate_temp_chart(cwa_data):
    """畫淡水區未來 24 小時氣溫 + 降雨機率，雙 y 軸；回傳圖片路徑。"""
    chart_path = os.path.join(tempfile.gettempdir(), 'weather_chart.png')
    font_prop = get_chinese_font()

    POINTS = 8  # 8 × 3hr = 24hr

    # 只畫一個地點：優先淡水區，沒有就拿第一個有資料的
    target = '淡水區' if '淡水區' in cwa_data else next(iter(cwa_data), None)
    if not target:
        return None
    elements = cwa_data[target]

    temps = elements.get('平均溫度') or elements.get('溫度') or elements.get('T') or []
    if not temps:
        return None
    pop_data = elements.get('12小時降雨機率') or elements.get('PoP12h') or []

    times, values = [], []
    for t in temps[:POINTS]:
        dt = _parse_cwa_time(t.get('time', ''))
        try:
            v = float(t.get('value'))
        except (TypeError, ValueError):
            continue
        if dt is None:
            continue
        times.append(dt)
        values.append(v)
    if not times:
        return None

    pop_series = [_pop_for(dt, pop_data) for dt in times]

    BG = '#0f1424'
    TEMP_COLOR = '#00d2ff'   # 青色（氣溫線）
    POP_COLOR = '#3a7bd5'    # 藍色（降雨機率柱）

    fig, ax = plt.subplots(figsize=(11, 5.4), dpi=120)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    x_idx = list(range(len(times)))
    x_labels = []
    last_day = None
    for dt in times:
        day = dt.strftime('%m/%d')
        if day != last_day:
            x_labels.append(f"{day}\n{dt.strftime('%H:%M')}")
            last_day = day
        else:
            x_labels.append(dt.strftime('%H:%M'))

    # 降雨機率：第二 y 軸，半透明柱狀圖（在氣溫線下層）
    ax2 = ax.twinx()
    bar_x = [i for i, p in enumerate(pop_series) if p is not None]
    bar_h = [pop_series[i] for i in bar_x]
    if bar_x:
        ax2.bar(bar_x, bar_h, width=0.85, color=POP_COLOR, alpha=0.35,
                label='降雨機率', zorder=1)
        for i, p in zip(bar_x, bar_h):
            if p > 0:
                ax2.annotate(f'{p}%', (i, p), textcoords="offset points",
                             xytext=(0, 4), ha='center', fontsize=10,
                             fontweight='bold', color='#9ec5ff',
                             fontproperties=font_prop, zorder=2)
    ax2.set_ylim(0, 110)  # 多留 10% 給數字標籤
    ax2.set_ylabel('降雨機率 (%)', fontsize=12, color='#9ec5ff',
                   fontproperties=font_prop)
    ax2.tick_params(axis='y', colors='#9ec5ff', labelsize=10)
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_color('#333')
    ax2.grid(False)

    # 氣溫：主 y 軸折線
    ax.plot(x_idx, values, marker='o', color=TEMP_COLOR,
            linewidth=3.2, markersize=11, label=f'{target} 氣溫', zorder=3)
    for i, v in enumerate(values):
        ax.annotate(f'{v:.0f}°', (i, v), textcoords="offset points",
                    xytext=(0, 13), ha='center', fontsize=12,
                    fontweight='bold', color=TEMP_COLOR,
                    fontproperties=font_prop, zorder=4)

    ymin, ymax = min(values), max(values)
    ax.set_ylim(ymin - 2.5, ymax + 4.0)

    ax.set_xticks(x_idx)
    ax.set_xticklabels(x_labels, fontproperties=font_prop, fontsize=13,
                       fontweight='bold', color='#f0f0f0')

    ax.set_title(f'{target} 未來 24 小時氣溫與降雨機率',
                 fontsize=17, color='white', pad=16,
                 fontweight='bold', fontproperties=font_prop)
    ax.set_ylabel('氣溫 (°C)', fontsize=12, color=TEMP_COLOR,
                  fontproperties=font_prop)

    ax.tick_params(axis='x', colors='#f0f0f0', labelsize=13, pad=8)
    ax.tick_params(axis='y', colors=TEMP_COLOR, labelsize=10)
    for s in ('top',):
        ax.spines[s].set_visible(False)
    for s in ('bottom', 'left'):
        ax.spines[s].set_color('#333')
    ax.grid(True, alpha=0.18, color='white', linestyle='--', linewidth=0.5)

    # 合併兩軸 legend
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    leg = ax.legend(h1 + h2, l1 + l2, prop=font_prop, facecolor=BG,
                    edgecolor='#555', labelcolor='white', fontsize=14,
                    loc='upper right', markerscale=1.3,
                    handlelength=2.2, borderpad=0.8, labelspacing=0.6)
    leg.get_frame().set_linewidth(1.2)
    for text in leg.get_texts():
        text.set_fontweight('bold')

    plt.tight_layout()
    plt.savefig(chart_path, facecolor=BG, bbox_inches='tight')
    plt.close()
    return chart_path


def get_local_events(locations):
    """用 Anthropic web_search server tool 查近期當地活動，最多 3 個或回 '無'。"""
    if not locations:
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    locs = "、".join(locations)
    prompt = (
        f"今天是 {today}。請用網路搜尋查詢「{locs}」（位於新北市）"
        f"今天起一週內舉辦的重要在地活動，例如節慶、市集、表演、展覽、廟會、馬拉松等。\n\n"
        f"輸出規則（純文字、繁體中文、不要 Markdown）：\n"
        f"- 找到至少 1 個 → 最多列 3 個，每行格式：`• 活動名稱｜日期｜地點`\n"
        f"- 完全沒搜到 → 只輸出兩個字：「無」\n"
        f"- 不要加開場白或結語，直接輸出活動列表或「無」"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        # web_search 是 server-side tool；回傳 content 含多個 block，取最後一個 text
        text = ""
        for block in message.content:
            if getattr(block, 'type', None) == 'text':
                text = block.text
        return text.strip()
    except Exception as e:
        print(f"近期活動查詢失敗：{e}")
        return ""


def get_weather_report():
    """取得完整天氣報告（文字 + 圖片路徑）"""
    cwa_data = get_cwa_weather()
    if not cwa_data:
        print("詳細預報（F-D0047-071）沒資料，改用 36 小時備用預報（F-C0032-001）")
        cwa_data = get_cwa_weather_fallback()
    owm_data = get_owm_weather()

    # 畫折線圖（只有詳細預報含逐 3 小時溫度才能畫）
    chart_path = None
    has_hourly_temp = any(
        elements.get('平均溫度') or elements.get('溫度') or elements.get('T')
        for elements in cwa_data.values()
    )
    if cwa_data and has_hourly_temp:
        try:
            chart_path = generate_temp_chart(cwa_data)
        except Exception as e:
            print(f"畫圖失敗：{e}")
    elif cwa_data:
        print("備用預報沒有逐小時溫度，本次不產圖")

    # AI 整理
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = WEATHER_PROMPT.format(
        date=today,
        cwa_data=str(cwa_data)[:3000],
        owm_data=str(owm_data)[:1500],
        locations="、".join(WEATHER_LOCATIONS)
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        weather_text = message.content[0].text
    except Exception as e:
        print(f"AI 天氣整理失敗：{e}")
        weather_text = "天氣資料暫時無法取得"

    # 接在「今日重點提醒」之後加「📅 近期活動」
    events = get_local_events(WEATHER_LOCATIONS)
    if events:
        weather_text = f"{weather_text}\n\n📅 近期活動\n{events}"

    return weather_text, chart_path
