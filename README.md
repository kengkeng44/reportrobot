# 每日情報機器人 (cheng.robot)

每天台灣早上 8 點自動推播一份完整情報到 Telegram，內容包括：

- 從 Gmail 擷取富邦複委託對帳單 PDF，解析買賣紀錄並累計持倉
- Yahoo Finance 即時股價 + 損益計算
- 多來源股票新聞（Yahoo / 鉅亨網）+ 論壇討論（PTT / Reddit / StockTwits / Dcard）
- 北台灣天氣預報（中央氣象署 + OpenWeatherMap）+ 溫度折線圖
- AI 產業鏈分析（Claude）

## 功能清單

| 模組 | 說明 |
|---|---|
| `gmail_reader.py` | 抓 Gmail 對帳單 PDF、用 pikepdf 解密、用 pdfplumber 抽交易明細，累計成 `{ticker: {shares, avg_cost}}` 持倉 |
| `portfolio.py` | 用 Yahoo Finance v8 chart API 取現價，算市值與損益% |
| `stock_news.py` | 多來源新聞與論壇爬蟲、英文標題 AI 翻譯、Claude 產業分析 |
| `weather.py` | CWA F-D0047-071 鄉鎮預報 + F-C0032-001 備用、OWM 輔助、matplotlib 折線圖、Claude 整理報告 |
| `telegram_sender.py` | Telegram HTML 訊息 + 圖片推送，支援自動分段 |
| `main.py` | 排程入口：執行一次完整情報後結束（給 Railway Cron 用） |

## 技術棧

- **語言**：Python 3.11+
- **部署**：Railway（NIXPACKS builder + Cron schedule）
- **AI**：Anthropic Claude (claude-sonnet-4-5)
- **API**：Gmail API (OAuth2)、Telegram Bot API、CWA Open Data、OpenWeatherMap
- **PDF**：pikepdf（解密）、pdfplumber（抽文字）
- **圖表**：matplotlib（無視窗模式 Agg backend）

## 專案結構

```
cheng.robot/
├── main.py                # 入口，跑一次完整流程後 exit
├── gmail_reader.py        # Gmail PDF 抓取與交易解析
├── portfolio.py           # 持倉現價/損益計算
├── stock_news.py          # 新聞、論壇、AI 分析
├── weather.py             # 天氣資料 + 折線圖 + AI 整理
├── telegram_sender.py     # Telegram 推播
├── prompts.py             # 集中管理所有 AI prompt
├── config.py              # 本機備用設定（Railway 用環境變數覆蓋）
├── requirements.txt       # 套件版本鎖定
├── railway.json           # Railway 部署 + Cron 設定
├── Procfile               # Railway / Heroku worker 指令
├── credentials.json       # Gmail OAuth client（不進 git）
├── token.pickle           # Gmail OAuth refresh token（不進 git）
└── .env.example           # 環境變數範本
```

## 本機安裝

```bash
git clone https://github.com/kengkeng44/reportrobot.git
cd reportrobot
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

接著準備兩份憑證（都不進 git）：

1. **Gmail OAuth**：到 [Google Cloud Console](https://console.cloud.google.com/) 開啟 Gmail API，建立 OAuth Client ID（Desktop App），下載 `credentials.json` 放專案根目錄。第一次跑 `python gmail_reader.py` 會跳瀏覽器授權，產生 `token.pickle`。
2. **設定值**：複製 `.env.example` → `.env`（或直接編輯 `config.py` 的預設值）。`config.py` 設計成「先讀環境變數、缺值才用內建預設」，所以本機開發兩種方式都行。

執行：

```bash
python main.py
```

跑完會把當日報告推到 Telegram 然後結束（不常駐）。

## Railway 部署

1. **連 GitHub repo**：Railway → New Project → Deploy from GitHub → 選 `kengkeng44/reportrobot`
2. **設環境變數**：到專案 Variables 頁，把 `.env.example` 列出的變數全部貼上去（用 Raw Editor 一次貼整批最快）
3. **產生 `TOKEN_PICKLE_B64`**：本機跑完 OAuth 拿到 `token.pickle` 後，base64 編碼貼到 Railway：

   ```bash
   base64 -w 0 token.pickle
   ```

   把整段字串設成 `TOKEN_PICKLE_B64` 環境變數。`gmail_reader.py` 在雲端會優先從這個變數還原憑證。
4. **Cron 排程**：`railway.json` 已寫好 `cronSchedule: "0 0 * * *"`（UTC 00:00 = 台灣 08:00），Railway 會每天自動觸發一次 `python main.py`，跑完就結束（`restartPolicyType: NEVER`）。

部署成功後不需要做別的事，每天早上 8 點 Telegram 會收到報告。

## 環境變數清單

| 變數 | 必填 | 說明 |
|---|---|---|
| `TELEGRAM_TOKEN` | ✅ | Telegram Bot Token（@BotFather 申請） |
| `TELEGRAM_CHAT_ID` | ✅ | 接收訊息的 Chat ID（可用 @userinfobot 查） |
| `GMAIL_USER` | ✅ | Gmail 帳號 email（OAuth 授權用） |
| `PDF_PASSWORD_PREFIX` | ✅ | 富邦對帳單 PDF 解密密碼（通常是身分證+生日） |
| `MANUAL_STOCKS` | ✅ | 額外追蹤的股票，逗號分隔（台股直接用代號，美股直接用 ticker），例：`2330,AAPL,TSLA` |
| `WEATHER_LOCATIONS` | ✅ | 天氣地點，逗號分隔，需符合 CWA 鄉鎮市區名，例：`淡水區,金山區` |
| `CWA_API_KEY` | ✅ | 中央氣象署開放資料 API Key（[申請連結](https://opendata.cwa.gov.tw/)） |
| `OWM_API_KEY` | ✅ | OpenWeatherMap API Key（[申請連結](https://openweathermap.org/api)） |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic Claude API Key（[申請連結](https://console.anthropic.com/)） |
| `SCHEDULE_UTC` | ⬜ | 預留欄位（目前實際排程在 `railway.json` 的 cronSchedule） |
| `TOKEN_PICKLE_B64` | Railway 必填 | Gmail OAuth token 的 base64 編碼，雲端用此還原憑證；本機可省略 |
| `PDF_PASSWORD` | ⬜ | 覆蓋 `PDF_PASSWORD_PREFIX`（用於密碼非預設格式時） |

## 排程時間說明

Railway Cron 設定在 `railway.json`：

```json
"cronSchedule": "0 0 * * *"
```

- UTC `00:00` = 台灣時間 `08:00`（UTC+8）
- 每天執行一次，跑完就退出
- 若要改時間，調整 cron 表達式（例如 `0 23 * * *` 是台灣早上 7 點）

## 授權條款

MIT License — 詳見 [LICENSE](LICENSE)。
