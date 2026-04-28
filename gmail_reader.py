"""
Gmail 讀取模組 v5
- 同時支援富邦【複委託】(美股/港股) 與【台股】對帳單
- 美股/港股：PDF 加密對帳單，pikepdf 解密 → pdfplumber 抽文字
- 台股日成交回報：純 email 內文（無附檔、無密碼）
- 台股月對帳單（有價證券對帳單）：可能是 PDF 或內文
- 把每筆買賣交易累計成持倉：shares + avg_cost
- 雙 pass 策略：先解析台股日報建立「名稱→代號」對照，再回頭處理月對帳單
"""

import os
import base64
import pickle
import re
import tempfile
import pdfplumber
import pikepdf
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    import config
    return getattr(config, name)


GMAIL_USER = _env("GMAIL_USER")
PDF_PASSWORD_PREFIX = _env("PDF_PASSWORD_PREFIX")

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# ── 美股/港股（複委託）解析常數 ──────────────────────────
_EXCHANGES = {
    'NASD', 'NYSE', 'AMEX', 'ARCA', 'BATS', 'OTC',
    'SEHK', 'HKEX', 'TSE', 'JPX', 'SGX', 'LSE',
    'KRX', 'SZSE', 'SSE',
}

_US_BLACKLIST = {
    'TWD', 'USD', 'NTD', 'HKD', 'JPY', 'EUR', 'GBP', 'CNY', 'RMB', 'KRW',
    'PDF', 'ETF', 'ATM', 'ROE', 'EPS', 'PE', 'PB', 'NT', 'US',
    'USA', 'TW', 'HK', 'CN', 'JP', 'UK', 'EU', 'ADR', 'API',
    'CEO', 'CFO', 'GDP', 'FBS', 'AI', 'VIP', 'END', 'SEC',
    'INC', 'LTD', 'CORP', 'CO', 'PLC', 'LLC', 'AG', 'SA',
    'TLAC', 'PTP', 'BBB', 'IRS',
} | _EXCHANGES

_BUY = {'買進', '買入', 'BUY', 'Buy', 'buy'}
_SELL = {'賣出', '賣', 'SELL', 'Sell', 'sell'}

# ── 台股解析常數 ─────────────────────────────────────
# 台股代號：4-6 位數字（一般股票/ETF）+ 可選 1 個英文字母（槓桿/反向 ETF 如 00631L、00632R）
_TW_NAME_CODE_RE = re.compile(r'^(\d{4,6}[A-Z]?)(\S.*)$')
# 6 碼 ROC 民國日期（114/10/27）
_ROC_DATE_RE = re.compile(r'^\d{3}[./]\d{1,2}[./]\d{1,2}$')

_TW_ACTIONS = {
    '現買': 'buy', '現賣': 'sell',
    '普買': 'buy', '普賣': 'sell',
    '零買': 'buy', '零賣': 'sell',
}

# 日成交回報整段文字 regex（兼容 plain text / HTML 抽出後不規則空白）
# 範例：`00631L元大台灣50正2    現賣    50    347.95    17,397    oE377    13:14:31`
_TW_DAILY_LINE_RE = re.compile(
    r'(\d{4,6}[A-Z]?)'                       # group 1: 代號
    r'([^\s\d][^\s]{0,20}?)'                 # group 2: 名稱（非空白，1-21 字）
    r'[\s　]+(現買|現賣|普買|普賣|零買|零賣)'  # group 3: 交易類別
    r'[\s　]+([\d,]+)'                   # group 4: 股數
    r'[\s　]+([\d,.]+)'                  # group 5: 價格
    r'[\s　]+([\d,]+)',                  # group 6: 金額
    re.UNICODE,
)


def _load_creds():
    """環境變數 TOKEN_PICKLE_B64 優先；否則退回本機 token.pickle。"""
    b64 = os.environ.get('TOKEN_PICKLE_B64')
    if b64:
        return pickle.loads(base64.b64decode(b64))
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            return pickle.load(token)
    return None


def _save_creds(creds):
    """盡力寫回本機 token.pickle；雲端唯讀檔案系統失敗就忽略。"""
    try:
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    except OSError as e:
        print(f"token.pickle 寫回失敗（忽略，改用環境變數時正常）：{e}")


def get_gmail_service():
    creds = _load_creds()
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        _save_creds(creds)
    return build('gmail', 'v1', credentials=creds)


def _tempfile(name):
    return os.path.join(tempfile.gettempdir(), name)


def _iter_pdf_parts(payload):
    if not isinstance(payload, dict):
        return
    filename = payload.get('filename', '') or ''
    body = payload.get('body', {}) or {}
    if filename.lower().endswith('.pdf') and body.get('attachmentId'):
        yield payload
    for sub in payload.get('parts', []) or []:
        yield from _iter_pdf_parts(sub)


def _get_email_body(payload):
    """
    從 Gmail payload 遞迴蒐集所有 text/plain 與 text/html 部分。
    優先回傳 plain；無 plain 才回 HTML 去標籤後的純文字。
    """
    plain_parts = []
    html_parts = []

    def walk(node):
        if not isinstance(node, dict):
            return
        mime = node.get('mimeType', '')
        body = node.get('body', {}) or {}
        data = body.get('data')
        if data and mime in ('text/plain', 'text/html'):
            try:
                raw = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            except Exception:
                raw = ''
            if raw:
                if mime == 'text/plain':
                    plain_parts.append(raw)
                else:
                    # HTML 用 '\n' 當 separator → 每個 cell/row 成獨立 line
                    plain_text = BeautifulSoup(raw, 'html.parser').get_text('\n')
                    html_parts.append(plain_text)
        for sub in node.get('parts', []) or []:
            walk(sub)

    walk(payload)
    if plain_parts:
        return '\n'.join(plain_parts)
    return '\n'.join(html_parts)


def _parse_roc_date(token):
    """民國日期 115/03/11 或 115.03.11 → (2026, 3, 11)；失敗回 None。"""
    m = re.fullmatch(r'(\d{3})[./](\d{1,2})[./](\d{1,2})', token)
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    if 100 <= y <= 200 and 1 <= mo <= 12 and 1 <= d <= 31:
        return (y + 1911, mo, d)
    return None


def _subject_to_date(subject):
    """從郵件主旨抽日期。日報取當天，月報取該月 28 日（足夠當去重 key）。"""
    s = subject or ''
    # 台股日報：富邦證券2025年10月27日證券成交回報
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', s)
    if m:
        return tuple(int(x) for x in m.groups())
    # 台股月報：【富邦證券】有價證券月對帳單-2025年10月
    m = re.search(r'(\d{4})年(\d{1,2})月', s)
    if m:
        return (int(m.group(1)), int(m.group(2)), 28)
    # 複委託日報：2026/03/11
    m = re.search(r'(\d{4})/(\d{1,2})/(\d{1,2})', s)
    if m:
        return tuple(int(x) for x in m.groups())
    # 複委託月報：2026~03
    m = re.search(r'(\d{4})~(\d{1,2})', s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return (y, mo, 28)
    return None


# ════════════════════════════════════════════════════════════
# 解析器：一行交易記錄 → trade dict 或 None
# ════════════════════════════════════════════════════════════

def _parse_us_record(line, fallback_date=None):
    """
    複委託（美股/港股）買賣記錄。支援兩種格式：
      日報：`NASD TSLA 買進 1 386.72 386.72 ...`
      月報：`115/03/11 NASD AAPL 買進 2 260.22 520.44 ...`
    """
    tokens = line.split()
    line_date = _parse_roc_date(tokens[0]) if tokens else None

    for i, tok in enumerate(tokens):
        if tok not in _EXCHANGES:
            continue
        if i + 4 >= len(tokens):
            return None
        ticker = tokens[i + 1]
        action = tokens[i + 2]
        if not re.fullmatch(r'[A-Z]{1,5}', ticker) or ticker in _US_BLACKLIST:
            return None
        if action not in _BUY and action not in _SELL:
            return None
        try:
            shares = int(float(tokens[i + 3].replace(',', '')))
            price = float(tokens[i + 4].replace(',', ''))
        except ValueError:
            return None
        if shares <= 0 or price <= 0:
            return None
        return {
            'ticker': ticker,
            'name': None,
            'action': 'buy' if action in _BUY else 'sell',
            'shares': shares,
            'price': price,
            'date': line_date or fallback_date,
        }
    return None


def _amount_matches(shares, price, amount, tol=0.05):
    """成交金額 ≈ 股數 × 價格（容忍 5% 用來吸收手續費或四捨五入）。"""
    if amount <= 0:
        return True  # 無法驗證就放行
    expected = shares * price
    return abs(expected - amount) / amount <= tol


def _extract_tw_daily_from_text(text, fallback_date=None):
    """
    從整段日成交回報文字 regex 抽出所有交易行（兼容 HTML 抽取後不規則空白）。
    """
    trades = []
    if not text:
        return trades
    for m in _TW_DAILY_LINE_RE.finditer(text):
        code, name, action_zh, shares_s, price_s, amount_s = m.groups()
        try:
            shares = int(shares_s.replace(',', ''))
            price = float(price_s.replace(',', ''))
            amount = float(amount_s.replace(',', ''))
        except ValueError:
            continue
        if shares <= 0 or price <= 0:
            continue
        if not _amount_matches(shares, price, amount):
            continue
        trades.append({
            'ticker': code,
            'name': name,
            'action': _TW_ACTIONS[action_zh],
            'shares': shares,
            'price': price,
            'date': fallback_date,
        })
    return trades


def _parse_tw_monthly_record(line, fallback_date=None, name_to_code=None):
    """
    台股有價證券月對帳單一行。格式範例：
      零股：`114/10/27 114/10/29 普賣 元大台灣50正2 50 347.9500 17,397 24 17 17,356 0`
      整股：`114/10/27 114/10/29 普賣 中信中國50正2 6 6,000 13.8900 83,340 118 83 83,139 0`

    區分零股/整股：交易單位後一欄含小數點 → 零股（單位即股數）；
                   否則為整股（單位=張數，下一欄才是股數）。
    """
    tokens = line.split()
    if len(tokens) < 7:
        return None

    # 必須以民國日期開頭
    if not _ROC_DATE_RE.fullmatch(tokens[0]):
        return None
    line_date = _parse_roc_date(tokens[0])
    if not line_date:
        return None

    # 找到交易類別欄位
    action_idx = None
    for i in range(1, len(tokens)):
        if tokens[i] in _TW_ACTIONS:
            action_idx = i
            break
    if action_idx is None:
        return None
    action = _TW_ACTIONS[tokens[action_idx]]

    # 證券名稱緊接於後（假設單 token，名稱含空白的特例先不處理）
    if action_idx + 1 >= len(tokens):
        return None
    name = tokens[action_idx + 1]

    rest = tokens[action_idx + 2:]
    if len(rest) < 3:
        return None

    try:
        # rest[1] 是價格 → 零股（rest[0]=股數, rest[1]=價格, rest[2]=金額）
        # rest[1] 是股數 → 整股（rest[0]=張數, rest[1]=股數, rest[2]=價格, rest[3]=金額）
        if '.' in rest[1]:
            shares = int(rest[0].replace(',', ''))
            price = float(rest[1].replace(',', ''))
            amount = float(rest[2].replace(',', ''))
        else:
            if len(rest) < 4:
                return None
            shares = int(rest[1].replace(',', ''))
            price = float(rest[2].replace(',', ''))
            amount = float(rest[3].replace(',', ''))
    except ValueError:
        return None

    if shares <= 0 or price <= 0:
        return None
    if not _amount_matches(shares, price, amount):
        return None

    code = (name_to_code or {}).get(name, name)
    return {
        'ticker': code,
        'name': name,
        'action': action,
        'shares': shares,
        'price': price,
        'date': line_date or fallback_date,
    }


def _parse_record(line, fallback_date=None, name_to_code=None):
    """依序試三種解析器，第一個成功者為主。"""
    return (
        _parse_us_record(line, fallback_date)
        or _parse_tw_monthly_record(line, fallback_date, name_to_code)
    )


# ════════════════════════════════════════════════════════════
# 內容解析：PDF / 純文字
# ════════════════════════════════════════════════════════════

def extract_trades_from_pdf(pdf_path, fallback_date=None, name_to_code=None):
    """解密 PDF（無密碼也能跑），逐行嘗試所有解析器。"""
    password = os.environ.get('PDF_PASSWORD', PDF_PASSWORD_PREFIX)
    decrypted_path = _tempfile('fbs_decrypted.pdf')
    trades = []

    try:
        with pikepdf.open(pdf_path, password=password) as pdf:
            pdf.save(decrypted_path)
    except Exception as e:
        print(f"  PDF 解密失敗（密碼前四碼：{password[:4]}***）：{e}")
        return []

    try:
        with pdfplumber.open(decrypted_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.split('\n'):
                    trade = _parse_record(line, fallback_date, name_to_code)
                    if trade:
                        trades.append(trade)
    except Exception as e:
        print(f"  PDF 解析失敗：{e}")
        return []
    return trades


def extract_trades_from_text(text, fallback_date=None, daily=True, name_to_code=None):
    """
    從純文字（email 內文）解析交易記錄。
    daily=True 用 regex 抽日報（兼容 HTML/plain）；
    daily=False 逐行走通用解析器（複委託 / 台股月對帳單）。
    """
    if not text:
        return []
    if daily:
        return _extract_tw_daily_from_text(text, fallback_date)

    trades = []
    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue
        trade = _parse_record(line, fallback_date, name_to_code)
        if trade:
            trades.append(trade)
    return trades


# ════════════════════════════════════════════════════════════
# 持倉累計
# ════════════════════════════════════════════════════════════

def _aggregate_portfolio(trades):
    """依時間順序累計：買入加股數加成本；賣出按均價減成本。最終 shares<=0 的剔除。"""
    # 確保依日期排序，避免 dict 順序影響成本累計（None 日期排最前）
    trades_sorted = sorted(trades, key=lambda t: t.get('date') or (0, 0, 0))

    book = {}
    for t in trades_sorted:
        p = book.setdefault(t['ticker'], {'shares': 0, 'cost_basis': 0.0})
        if t['action'] == 'buy':
            p['shares'] += t['shares']
            p['cost_basis'] += t['shares'] * t['price']
        else:  # sell
            if p['shares'] > 0:
                avg = p['cost_basis'] / p['shares']
                sold = min(t['shares'], p['shares'])
                p['shares'] -= sold
                p['cost_basis'] -= avg * sold
    portfolio = {}
    for ticker, p in book.items():
        if p['shares'] > 0:
            portfolio[ticker] = {
                'shares': p['shares'],
                'avg_cost': p['cost_basis'] / p['shares'],
            }
    return portfolio


# ════════════════════════════════════════════════════════════
# Gmail 抓信
# ════════════════════════════════════════════════════════════

def _download_email_items():
    """
    抓 fbs.com.tw 過去 3 個月所有對帳單/成交回報，
    回傳每封信的 {subject, date_hint, body_text, pdf_paths}。
    """
    service = get_gmail_service()
    query = 'from:fbs.com.tw (對帳單 OR 委託 OR 成交) newer_than:3m'
    results = service.users().messages().list(
        userId='me', q=query, maxResults=60
    ).execute()
    messages = results.get('messages', [])
    if not messages:
        print(f"找不到符合條件的郵件（query: {query}）")
        return []

    print(f"找到 {len(messages)} 封 fbs.com.tw 郵件")
    items = []
    for idx, msg in enumerate(messages):
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        payload = msg_data.get('payload', {})
        subject = ""
        for h in payload.get('headers', []):
            if h.get('name', '').lower() == 'subject':
                subject = h.get('value', '')
                break

        body_text = _get_email_body(payload)

        pdf_paths = []
        for part in _iter_pdf_parts(payload):
            attachment_id = part['body']['attachmentId']
            attachment = service.users().messages().attachments().get(
                userId='me', messageId=msg['id'], id=attachment_id
            ).execute()
            pdf_data = base64.urlsafe_b64decode(attachment['data'])
            pdf_path = _tempfile(f'fbs_{idx}_{len(pdf_paths)}.pdf')
            with open(pdf_path, 'wb') as f:
                f.write(pdf_data)
            pdf_paths.append(pdf_path)

        items.append({
            'subject': subject,
            'date_hint': _subject_to_date(subject),
            'body_text': body_text,
            'pdf_paths': pdf_paths,
        })
    return items


def _is_tw_daily(subject):
    return '成交回報' in (subject or '')


def _is_tw_monthly_text(subject, body_text):
    """有價證券月對帳單若以內文寄出（非 PDF），靠主旨判斷。"""
    s = subject or ''
    return '有價證券' in s and '對帳單' in s


# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

def get_portfolio_from_gmail():
    """
    雙 pass 策略：
      Pass 1: 解析所有台股日成交回報（內文），同步建立「名稱↔代號」對照
      Pass 2: 解析所有 PDF（複委託 / 台股月對帳單）與內文型月對帳單，套用對照表回填代號
    回傳 {ticker: {shares, avg_cost}}。
    """
    try:
        items = _download_email_items()
        if not items:
            return {}

        all_trades = []
        tw_code_to_name = {}

        # ── Pass 1：台股日成交回報（內文） ──
        for it in items:
            if not _is_tw_daily(it['subject']):
                continue
            trades = extract_trades_from_text(
                it['body_text'], fallback_date=it['date_hint'], daily=True
            )
            for t in trades:
                if t.get('name'):
                    tw_code_to_name[t['ticker']] = t['name']
            print(f"  [日報] {it['subject'][:50]} → {len(trades)} 筆")
            all_trades.extend(trades)

        name_to_code = {v: k for k, v in tw_code_to_name.items()}

        # ── 同步把代號↔名稱對照塞進 stock_news.STOCK_NAMES，下游顯示用 ──
        if tw_code_to_name:
            try:
                import stock_news
                for code, name in tw_code_to_name.items():
                    stock_news.STOCK_NAMES.setdefault(code, name)
            except Exception as e:
                print(f"  名稱對照注入失敗（忽略）：{e}")

        # ── Pass 2：PDF 對帳單（複委託 / 台股月）+ 內文型月對帳單 ──
        for idx, it in enumerate(items):
            if _is_tw_daily(it['subject']):
                continue  # 已在 Pass 1 處理

            # 2a. PDF 附件
            for path in it['pdf_paths']:
                trades = extract_trades_from_pdf(
                    path, fallback_date=it['date_hint'], name_to_code=name_to_code
                )
                print(f"  [PDF] {it['subject'][:50]} → {len(trades)} 筆")
                all_trades.extend(trades)

            # 2b. 內文型月對帳單（沒 PDF 但內文有交易表）
            if not it['pdf_paths'] and _is_tw_monthly_text(it['subject'], it['body_text']):
                trades = extract_trades_from_text(
                    it['body_text'],
                    fallback_date=it['date_hint'],
                    daily=False,
                    name_to_code=name_to_code,
                )
                print(f"  [內文月報] {it['subject'][:50]} → {len(trades)} 筆")
                all_trades.extend(trades)

        # ── 去重：日對帳單 vs 月對帳單常重複同筆交易 ──
        seen = set()
        deduped = []
        for t in all_trades:
            key = (t.get('date'), t['ticker'], t['action'], t['shares'], round(t['price'], 4))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(t)
        if len(deduped) != len(all_trades):
            print(f"  去重後 {len(all_trades)} → {len(deduped)} 筆交易")

        portfolio = _aggregate_portfolio(deduped)

        print("===== 持倉累計結果 =====")
        if not portfolio:
            print("  無持倉")
        else:
            for ticker, p in sorted(
                portfolio.items(),
                key=lambda x: x[1]['shares'] * x[1]['avg_cost'],
                reverse=True,
            ):
                cost_value = p['shares'] * p['avg_cost']
                print(f"  {ticker}: {p['shares']} 股 @ 均價 {p['avg_cost']:.2f}（成本 {cost_value:,.0f}）")
        print("========================")
        return portfolio
    except Exception as e:
        print(f"Gmail 讀取失敗：{e}")
        import traceback; traceback.print_exc()
        return {}


def get_stocks_from_gmail():
    """回傳前 3 大持倉 ticker（按成本金額），給 news/分析追蹤用。"""
    portfolio = get_portfolio_from_gmail()
    if not portfolio:
        return []
    sorted_tickers = sorted(
        portfolio.items(),
        key=lambda x: x[1]['shares'] * x[1]['avg_cost'],
        reverse=True,
    )
    top3 = [ticker for ticker, _ in sorted_tickers[:3]]
    print(f"取前 3 大（按成本金額）：{top3}")
    return top3
