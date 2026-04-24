# 安全政策 (Security Policy)

## 本專案的資安做法

這個專案會處理 Telegram Bot Token、Gmail OAuth 憑證、Anthropic API Key 等敏感資料，因此採取了以下保護措施：

- **環境變數優先**：`telegram_sender.py` / `gmail_reader.py` / `weather.py` / `stock_news.py` / `main.py` 全部優先讀 `os.environ`，找不到才 fallback 到 `config.py`。Railway 部署時完全不需要 `config.py`，所有金鑰存在 Railway Variables 頁。
- **`.gitignore` 全面攔截**：`config.py`、`credentials.json`、`token.pickle`、`*.pickle`、`.env`、`railway_env.txt`、`token_b64.txt` 都被列入 ignore，避免任何金鑰意外進入版本控制。
- **OAuth token 不入庫**：Gmail OAuth 的 `token.pickle` 在本機由 `InstalledAppFlow` 產生；雲端則用 base64 編碼後放在 `TOKEN_PICKLE_B64` 環境變數，由 `gmail_reader._load_creds()` 還原。憑證檔本身永遠不會出現在 git 歷史。
- **PDF 密碼動態覆蓋**：富邦對帳單密碼支援用 `PDF_PASSWORD` 環境變數覆蓋 `PDF_PASSWORD_PREFIX`，密碼非預設格式時不必改程式碼。
- **最小權限 OAuth scope**：Gmail API 只要 `gmail.readonly` 一個 scope，無法寫入或刪除信件。

## Fork 後的使用者須知

如果你 fork 這個 repo 自己用，請務必：

1. **自行建立 `config.py`**：repo 不含 `config.py`（已 gitignore），你需要手動建立一份本機備用設定檔。建議從 `.env.example` 對照欄位，把預設值填進 `config.py` 的 `_env(name, default)` 第二個參數。或是更乾淨的做法：建一個 `.env` 用 `python-dotenv` 載入，完全不寫 `config.py`。
2. **絕對不要 commit 敏感檔案**：
   - 不要 `git add config.py`
   - 不要 `git add credentials.json`、`token.pickle`、`*.pickle`
   - 不要把含真實金鑰的 `.env` / `railway_env.txt` 提交上來
   - commit 前永遠先跑 `git status` 確認沒帶到敏感檔案
3. **不要 force push 把金鑰歷史塗掉就以為沒事**：一旦金鑰進入 GitHub（即使是 private repo 然後 force push 移除），都應視同外洩。請立刻到對應 console 撤銷並重新發行：
   - Telegram Bot：`@BotFather` → `/revoke` → `/token`
   - Google OAuth：[Google Cloud Console](https://console.cloud.google.com/apis/credentials) 撤銷 client 後重建
   - Anthropic：[console.anthropic.com](https://console.anthropic.com/) 撤銷 API key
   - CWA / OWM：到對應後台重新申請
4. **本機 Telegram Token 與你 fork 的人不同**：請申請自己的 Bot，不要直接拿原作者的 token。

## 漏洞回報方式

發現安全性問題（程式碼漏洞、依賴套件 CVE、敏感資料外洩風險等）請透過以下方式回報：

- **開 GitHub Issue**：到 [Issues](https://github.com/kengkeng44/reportrobot/issues) 開新 issue，標題前綴加 `[SECURITY]`。
- **不要在 issue 內公開貼出真實金鑰、token、PII**：
  - 描述問題本身（例如「`gmail_reader.py:123` 沒驗證 PDF 來源，惡意 PDF 可觸發 X」）即可
  - 如果你必須示範，請用假值（`sk-ant-XXXXXXXX`、`123456:fake_token`）
  - 真實的金鑰外洩證據請私下透過 GitHub 的 [Private Vulnerability Reporting](https://github.com/kengkeng44/reportrobot/security/advisories) 提交，不要貼在公開 issue
- **不要在 PR 描述、commit message、討論串貼真實金鑰**：即使打算事後刪除，GitHub 的 webhook、email 通知、第三方 mirror 都已收到內容。

收到回報後會在合理時間內回覆並評估修復。
