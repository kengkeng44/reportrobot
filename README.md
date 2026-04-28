# 每日情報機器人 (cheng.robot)

每天台灣早上 8 點自動推播一份完整情報到 Telegram，內容包括：

- 從 Gmail 擷取富邦【複委託】美股/港股對帳單 PDF + 【台股】日成交回報 / 月對帳單，解密、解析、累計持倉
- Yahoo Finance 即時股價 + 損益計算（同時支援台股 ETF 如 00631L）
- 多來源股票新聞（Yahoo / 鉅亨網）+ 論壇討論（PTT / Reddit / StockTwits / Dcard）
- 北台灣天氣預報（中央氣象署 + OpenWeatherMap）+ 溫度折線圖
- AI 產業鏈分析（Claude）

## 設計亮點 / Project Highlights

> 個人 side project · 約 1,343 行 Python · 模組化 8 個檔 · 部署於 Railway 每日穩定運行

**解決的痛點**：早上要分別查股價、看新聞、查天氣太瑣碎；券商對帳單是加密 PDF 沒辦法直接看持倉；英文新聞看得慢、散戶情緒分散在各論壇。一份每日推播全部解決。

### 技術亮點

1. **Gmail → 持倉狀態管線（雙市場支援）**
   - **複委託（美股/港股）**：OAuth2 抓信 → `pikepdf` 用「身分證+生日」格式密碼解密 → `pdfplumber` 抽純文字 → 用「交易所代碼白名單 + ticker 黑名單」雙重防呆解析（避免把 `USD`/`PDF`/`SEC` 誤判為股票）
   - **台股日成交回報**：純 email 內文（無附檔無密碼），用 regex 在整段文字上匹配，兼容 plain text 與 HTML table 兩種寄送格式
   - **台股月對帳單**：自動辨識零股 vs 整股欄位差（零股「交易單位」即股數；整股則「張數 + 股數」兩欄並存），並用 `成交股數 × 成交價格 ≈ 成交金額` 反推驗證解析結果
   - **雙 pass 對照**：先解析日報建立「證券名稱↔代號」對照表，再回頭把月對帳單只有名稱的紀錄回填正確 4-6 位代號（含槓桿 ETF 如 `00631L`）
   - 最終累計成 `{ticker: {shares, avg_cost}}`，台美股共用同一份持倉結構

2. **OAuth Token 雲端化**
   Railway 唯讀檔案系統無法寫回 `token.pickle`，解法是本機 OAuth 完成後將 token base64 編碼存進環境變數 `TOKEN_PICKLE_B64`。`_load_creds()` 優先讀環境變數、退回本機檔案，**同份程式碼支援本機開發與雲端部署**。

3. **5 種資料來源融合**
   股價（Yahoo Finance v8 chart API）、新聞（Yahoo RSS + 鉅亨網）、論壇（PTT/Reddit/StockTwits/Dcard）、天氣（中央氣象署 F-D0047-071 為主、OpenWeatherMap 為輔）、AI（Anthropic Claude）。資料源衝突時以權威來源為準（例：天氣以 CWA 為準）。

4. **AI 整合與 Prompt 集中管理**
   所有 Prompt 集中在 `prompts.py`（天氣分析、產業鏈分析、論壇摘要、英文標題翻譯），改 prompt 不用翻多個檔。英文新聞標題批次塞進一個 prompt 要求 Claude 回 JSON 陣列，正則抽 `[...]` 防它前後加廢話、失敗 fallback 用原英文。

5. **Serverless Cron 部署**
   `railway.json` 設 `cronSchedule: "0 0 * * *"` + `restartPolicyType: NEVER`，跑完一次就退出，不常駐 = 不燒 Railway 額度。每日 UTC 00:00（台灣 08:00）自動觸發。

6. **無視窗環境圖表**
   matplotlib 強制 `Agg` backend 在雲端無顯示器環境渲染溫度折線圖成 PNG，再透過 Telegram Bot API 推送圖片。

### 設計取捨

| 決策 | 理由 |
|---|---|
| Cron 跑完即退 vs. 常駐排程 | Railway 計時收費，跑完退出每天只用幾秒；常駐會燒整天額度 |
| Prompt 集中在 `prompts.py` | 改 prompt 不用翻三個檔；接手者只看一個檔就懂所有 AI 行為 |
| `config.py` + 環境變數雙層 fallback | 本機改 `config.py` 即可；雲端強制走環境變數，credentials 不會誤上 git |
| token.pickle 改 base64 上雲 | Railway 唯讀檔案系統無法寫回，base64 環境變數是唯一可行解 |
| Ticker 雙重白/黑名單 | 對帳單格式雜亂，純正則容易把 `USD`/`PDF`/`SEC` 誤判為股票 |
| Yahoo v8 chart API 取代付費行情 | 免 key、夠用，個人專案不需要 tick-level 即時性 |

## 功能清單

| 模組 | 說明 |
|---|---|
| `gmail_reader.py` | 抓 Gmail 對帳單，支援 4 種來源：複委託 PDF（pikepdf 解密 + pdfplumber 抽文字）、台股日成交回報（email 內文 regex）、台股月對帳單（PDF 或內文）；雙 pass 把名稱與 4-6 位代號對齊，累計成 `{ticker: {shares, avg_cost}}` |
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
git clone https://github.com/kengkeng44/ReportRobot.git
cd ReportRobot
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

1. **連 GitHub repo**：Railway → New Project → Deploy from GitHub → 選 `kengkeng44/ReportRobot`
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
