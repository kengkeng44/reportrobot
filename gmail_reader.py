"""
Gmail 讀取模組 v4
- 搜尋最近 3 個月 from:fbs.com.tw 的所有對帳單
- 把每筆買賣交易累計成持倉：shares + avg_cost
- 回傳 portfolio 或 top3 ticker
- 測試模式：印出找到的交易清單與累計持倉
"""

import os
import base64
import pickle
import re
import tempfile
import pdfplumber
import pikepdf
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
    """從郵件主旨抽日期：日對帳單取當天，月對帳單取該月月底。"""
    m = re.search(r'(\d{4})/(\d{1,2})/(\d{1,2})', subject or '')
    if m:
        return tuple(int(x) for x in m.groups())
    m = re.search(r'(\d{4})~(\d{1,2})', subject or '')
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return (y, mo, 28)  # 同月統一到 28 日（足夠當 key）
    return None


def _parse_trade_record(line, fallback_date=None):
    """
    解析富邦複委託買賣記錄行。支援兩種格式：
      日對帳單：`NASD TSLA 買進 1 386.72 386.72 ...`
      月對帳單：`115/03/11 NASD AAPL 買進 2 260.22 520.44 ...`
    回傳 {'ticker','action','shares','price','date'} 或 None。
    """
    tokens = line.split()
    # 抓行首日期（若有）
    line_date = None
    if tokens:
        line_date = _parse_roc_date(tokens[0])

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
            'action': 'buy' if action in _BUY else 'sell',
            'shares': shares,
            'price': price,
            'date': line_date or fallback_date,
        }
    return None


def extract_trades_from_pdf(pdf_path, fallback_date=None):
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
                    trade = _parse_trade_record(line, fallback_date=fallback_date)
                    if trade:
                        trades.append(trade)
    except Exception as e:
        print(f"  PDF 解析失敗：{e}")
        return []
    return trades


def _aggregate_portfolio(trades):
    """依時間順序累計：買入加股數加成本；賣出按均價減成本。最終 shares<=0 的剔除。"""
    book = {}
    for t in trades:
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


def _download_pdfs_from_gmail():
    """抓 Gmail 裡最近 3 個月的對帳單 PDF，回傳 [(subject, filename, local_path)]。"""
    service = get_gmail_service()
    query = 'from:fbs.com.tw (對帳單 OR 委託 OR 成交) newer_than:3m'
    results = service.users().messages().list(
        userId='me', q=query, maxResults=30
    ).execute()
    messages = results.get('messages', [])
    if not messages:
        print(f"找不到符合條件的郵件（query: {query}）")
        return []

    print(f"找到 {len(messages)} 封 fbs.com.tw 對帳單郵件")
    items = []
    for idx, msg in enumerate(messages):
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        subject = ""
        for h in msg_data.get('payload', {}).get('headers', []):
            if h.get('name', '').lower() == 'subject':
                subject = h.get('value', '')
                break

        pdf_parts = list(_iter_pdf_parts(msg_data.get('payload', {})))
        if not pdf_parts:
            continue

        for part in pdf_parts:
            filename = part.get('filename', '')
            attachment_id = part['body']['attachmentId']
            attachment = service.users().messages().attachments().get(
                userId='me', messageId=msg['id'], id=attachment_id
            ).execute()
            pdf_data = base64.urlsafe_b64decode(attachment['data'])
            pdf_path = _tempfile(f'fbs_{idx}.pdf')
            with open(pdf_path, 'wb') as f:
                f.write(pdf_data)
            items.append((subject, filename, pdf_path))
            break
    return items


def get_portfolio_from_gmail():
    """累計 Gmail 所有 PDF 的買賣記錄 → 回傳 {ticker: {shares, avg_cost}}。"""
    try:
        items = _download_pdfs_from_gmail()
        if not items:
            return {}

        all_trades = []
        for subject, filename, path in items:
            fallback = _subject_to_date(subject)
            trades = extract_trades_from_pdf(path, fallback_date=fallback)
            print(f"  {subject[:40]} → {len(trades)} 筆交易")
            all_trades.extend(trades)

        # 去重：日對帳單 vs 月對帳單常會重複同一筆交易
        # key = (date, ticker, action, shares, price)
        seen = set()
        deduped = []
        for t in all_trades:
            key = (t.get('date'), t['ticker'], t['action'], t['shares'], t['price'])
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
