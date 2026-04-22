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
from datetime import datetime
from config import CWA_API_KEY, OWM_API_KEY, ANTHROPIC_API_KEY, WEATHER_LOCATIONS
from prompts import WEATHER_PROMPT

# 嘗試使用中文字體
def get_chinese_font():
    """找到可用的中文字體"""
    font_candidates = [
        'Noto Sans CJK TC', 'Microsoft JhengHei', 'PingFang TC',
        'WenQuanYi Micro Hei', 'SimHei', 'Arial Unicode MS'
    ]
    for font_name in font_candidates:
        try:
            font_path = fm.findfont(fm.FontProperties(family=font_name))
            if font_path and 'fallback' not in font_path.lower():
                return fm.FontProperties(fname=font_path)
        except:
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

def generate_temp_chart(cwa_data):
    """畫溫度折線圖，回傳圖片路徑"""
    chart_path = os.path.join(tempfile.gettempdir(), 'weather_chart.png')
    font_prop = get_chinese_font()

    fig, ax = plt.subplots(figsize=(10, 4), dpi=100)
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#1a1a2e')

    colors = {'淡水區': '#00d2ff', '金山區': '#ff6b6b'}

    for location, elements in cwa_data.items():
        # 主 API 回傳「平均溫度」；舊版是「溫度」；備用 API 沒有單點溫度
        temps = elements.get('平均溫度') or elements.get('溫度') or elements.get('T') or []
        if not temps:
            continue

        hours = []
        values = []
        for t in temps[:24]:
            try:
                time_str = t['time']
                dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                hours.append(dt.strftime('%H:%M'))
                values.append(float(t['value']))
            except:
                continue

        if hours and values:
            color = colors.get(location, '#ffffff')
            ax.plot(hours, values, marker='o', color=color,
                    linewidth=2.5, markersize=6, label=location)
            # 標注溫度數值
            for i, (h, v) in enumerate(zip(hours, values)):
                ax.annotate(f'{v:.0f}°', (h, v), textcoords="offset points",
                           xytext=(0, 12), ha='center', fontsize=9,
                           color=color, fontproperties=font_prop)

    ax.set_title('Today Temperature', fontsize=16,
                color='white', pad=15, fontproperties=font_prop)
    ax.set_xlabel('Time', fontsize=11, color='#aaa', fontproperties=font_prop)
    ax.set_ylabel('Temp (°C)', fontsize=11, color='#aaa', fontproperties=font_prop)

    ax.tick_params(colors='#888', labelsize=9)
    ax.spines['bottom'].set_color('#333')
    ax.spines['left'].set_color('#333')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(True, alpha=0.15, color='white')
    ax.legend(prop=font_prop, facecolor='#1a1a2e', edgecolor='#333',
              labelcolor='white', fontsize=10)

    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(chart_path, facecolor='#1a1a2e', bbox_inches='tight')
    plt.close()
    return chart_path

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

    return weather_text, chart_path
