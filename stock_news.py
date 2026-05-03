"""
股票情報模組 v3
- 新聞：Yahoo Finance + 鉅亨網（英文標題用 AI 翻譯成中文）
- 論壇：PTT / Reddit r/stocks & r/wallstreetbets / StockTwits / Dcard 股票版
- AI 分析：結合新聞 + 論壇資料做深度分析
"""

import json
import os
import re
import requests
import feedparser
import anthropic
from bs4 import BeautifulSoup
from prompts import STOCK_ANALYSIS_PROMPT, FORUM_SUMMARY_PROMPT


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    import config
    return getattr(config, name)


ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")

AI_MODEL = "claude-sonnet-4-5"

STOCK_NAMES = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科",
    "2308": "台達電", "3008": "大立光", "2412": "中華電",
    "AAPL": "Apple", "TSLA": "Tesla", "NVDA": "NVIDIA",
    "GOOGL": "Google", "MSFT": "Microsoft", "AMD": "AMD",
    "TSM": "台積電ADR", "META": "Meta",
}

_CJK_RE = re.compile(r'[一-鿿぀-ヿ]')
# 台股代號：4-6 位數字 + 可選 1 個英文字母（槓桿/反向 ETF 如 00631L）
_TW_TICKER_RE = re.compile(r'^\d{4,6}[A-Z]?$')


def is_tw_ticker(stock_id):
    return bool(_TW_TICKER_RE.fullmatch(stock_id or ''))


def _twstock_name(stock_id):
    """從 twstock 內建上市櫃對照表查中文名；找不到回 None。"""
    try:
        import twstock
        info = twstock.codes.get(stock_id)
        if info and info.name:
            return info.name
    except Exception:
        pass
    return None


def get_stock_name(stock_id):
    """先查 hardcode，沒有再查 twstock，最後 fallback 回 stock_id 本身。"""
    if stock_id in STOCK_NAMES:
        return STOCK_NAMES[stock_id]
    name = _twstock_name(stock_id)
    if name:
        return name
    return stock_id


def _has_cjk(text):
    return bool(_CJK_RE.search(text or ""))


def translate_titles(items):
    """把沒有中文的標題批次送去 AI 翻譯，直接 mutate items，加上 title_zh 欄位。"""
    pending = [(i, it['title']) for i, it in enumerate(items) if it.get('title') and not _has_cjk(it['title'])]
    if not pending:
        return items

    numbered = "\n".join(f"{i+1}. {t}" for i, (_, t) in enumerate(pending))
    prompt = (
        "請把以下英文新聞標題逐行翻譯成精煉的繁體中文標題（每個 15 字內），"
        "只輸出 JSON 陣列字串，按輸入順序，不要任何額外文字：\n\n"
        f"{numbered}"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=AI_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            text = match.group(0)
        translations = json.loads(text)
        for (idx, _), zh in zip(pending, translations):
            if isinstance(zh, str) and zh.strip():
                items[idx]['title_zh'] = zh.strip()
    except Exception as e:
        print(f"標題翻譯失敗：{e}")
    return items


def get_yahoo_news(stock_id):
    try:
        ticker = f"{stock_id}.TW" if is_tw_ticker(stock_id) else stock_id
        url = f"https://finance.yahoo.com/rss/headline?s={ticker}"
        feed = feedparser.parse(url)
        news = []
        for entry in feed.entries[:5]:
            news.append({"title": entry.get('title', ''), "link": entry.get('link', '')})
        return news
    except Exception as e:
        print(f"Yahoo 新聞失敗：{e}")
        return []


def get_cnyes_news(stock_id):
    try:
        url = "https://news.cnyes.com/api/v3/news/category/tw_stock"
        params = {"keyword": stock_id, "limit": 5}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        items = data.get('items', {}).get('data', [])
        news = []
        for item in items[:5]:
            news_id = item.get('newsId', '')
            news.append({
                "title": item.get('title', ''),
                "link": f"https://news.cnyes.com/news/id/{news_id}" if news_id else "",
            })
        return news
    except Exception as e:
        print(f"鉅亨新聞失敗：{e}")
        return []


def get_ptt_articles(stock_id, pages=3):
    articles = []
    headers = {"User-Agent": "Mozilla/5.0", "Cookie": "over18=1"}
    try:
        base_url = "https://www.ptt.cc/bbs/Stock/index.html"
        resp = requests.get(base_url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        current_page = 1
        for b in soup.select('div.btn-group-paging a'):
            if '上頁' in b.text:
                match = re.search(r'index(\d+)', b.get('href', ''))
                if match:
                    current_page = int(match.group(1)) + 1
                break

        for page_offset in range(pages):
            page_num = current_page - page_offset
            if page_num < 1:
                break
            url = f"https://www.ptt.cc/bbs/Stock/index{page_num}.html"
            resp = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(resp.text, 'html.parser')
            for entry in soup.select('div.r-ent'):
                title_tag = entry.select_one('div.title a')
                if not title_tag:
                    continue
                title = title_tag.text.strip()
                link = f"https://www.ptt.cc{title_tag.get('href', '')}"
                push_tag = entry.select_one('div.nrec span')
                push_count = 0
                if push_tag:
                    t = push_tag.text.strip()
                    if t == '爆':
                        push_count = 100
                    elif t == 'XX':
                        push_count = -100
                    elif t.lstrip('-').isdigit():
                        push_count = int(t)
                stock_name = get_stock_name(stock_id)
                if (stock_id in title or stock_name in title or stock_id.lower() in title.lower()):
                    articles.append({"title": title, "link": link, "heat": push_count})

        articles.sort(key=lambda x: x['heat'], reverse=True)
        return articles[:8]
    except Exception as e:
        print(f"PTT 爬取失敗：{e}")
        return []


def get_reddit_posts(stock_id, subreddit):
    """從 Reddit 搜尋熱門文章，按 score 排序。"""
    ticker = stock_id if not stock_id.isdigit() else stock_id
    headers = {"User-Agent": "cheng.robot/1.0 (stock-news)"}
    try:
        url = f"https://www.reddit.com/r/{subreddit}/search.json"
        params = {"q": ticker, "sort": "top", "t": "month", "restrict_sr": "on", "limit": 10}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        posts = []
        for child in data.get('data', {}).get('children', []):
            d = child.get('data', {})
            posts.append({
                "title": d.get('title', ''),
                "link": f"https://www.reddit.com{d.get('permalink', '')}",
                "heat": int(d.get('score', 0)),
                "comments": int(d.get('num_comments', 0)),
                "subreddit": subreddit,
            })
        posts.sort(key=lambda x: x['heat'] + x['comments'], reverse=True)
        return posts[:5]
    except Exception as e:
        print(f"Reddit r/{subreddit} 爬取失敗：{e}")
        return []


def get_stocktwits_messages(stock_id):
    """StockTwits 只支援英文 ticker，台股代號（含 ETF 如 00631L）跳過。"""
    if is_tw_ticker(stock_id):
        return []
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{stock_id}.json"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        msgs = []
        for m in data.get('messages', []):
            likes = m.get('likes', {}).get('total', 0) or 0
            msgs.append({
                "title": (m.get('body', '') or '')[:120],
                "link": f"https://stocktwits.com/{m.get('user', {}).get('username', '')}/message/{m.get('id', '')}",
                "heat": int(likes),
            })
        msgs.sort(key=lambda x: x['heat'], reverse=True)
        return msgs[:5]
    except Exception as e:
        print(f"StockTwits 失敗：{e}")
        return []


def get_dcard_posts(stock_id):
    """從 Dcard 股票版抓熱門，標題含 stock_id 或中文名的篩出來。"""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        url = "https://www.dcard.tw/service/api/v2/forums/stock/posts"
        params = {"popular": "true", "limit": 30}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        stock_name = get_stock_name(stock_id)
        posts = []
        for p in data:
            title = p.get('title', '') or ''
            excerpt = p.get('excerpt', '') or ''
            blob = f"{title} {excerpt}"
            if (stock_id in blob or stock_name in blob or stock_id.lower() in blob.lower()):
                posts.append({
                    "title": title,
                    "link": f"https://www.dcard.tw/f/stock/p/{p.get('id', '')}",
                    "heat": int(p.get('likeCount', 0)) + int(p.get('commentCount', 0)),
                })
        posts.sort(key=lambda x: x['heat'], reverse=True)
        return posts[:5]
    except Exception as e:
        print(f"Dcard 爬取失敗：{e}")
        return []


def format_news_html(news_list):
    """只回傳內容（或空字串），由呼叫端決定是否顯示區塊。"""
    if not news_list:
        return ""
    lines = []
    for n in news_list[:3]:
        title = n['title']
        zh = n.get('title_zh')
        display = f"{title}（{zh}）" if zh else title
        link = n.get('link', '')
        if link:
            lines.append(f'  • <a href="{link}">{display}</a>')
        else:
            lines.append(f'  • {display}')
    return "\n".join(lines)


def format_forum_html(articles, limit=5):
    if not articles:
        return ""
    lines = []
    for a in articles[:limit]:
        heat = a['heat']
        if heat >= 50:
            icon = "🔥"
        elif heat >= 20:
            icon = "👍"
        elif heat < 0:
            icon = "👎"
        else:
            icon = "💬"

        source = a.get('source')
        if source:
            # 英文論壇合併區塊：標 r/<sub> 或 StockTwits + score↑/comments💬
            comments = a.get('comments') or 0
            meta = f"[{heat}↑" + (f" {comments}💬" if comments else "") + "]"
            prefix = f"{source} "
        else:
            # PTT / Dcard 等中文論壇：簡單標 [heat]
            meta = f"[{heat}]"
            prefix = ""

        # 標題：英文有翻譯就在後面附 (中文)
        title = a['title']
        zh = a.get('title_zh')
        display = f"{title}（{zh}）" if zh else title

        lines.append(f'  {icon} {meta} {prefix}<a href="{a["link"]}">{display}</a>')
    return "\n".join(lines)


def get_ai_analysis(stock_id, news_summary, forum_summary):
    stock_name = get_stock_name(stock_id)
    prompt = STOCK_ANALYSIS_PROMPT.format(
        stock_id=stock_id,
        stock_name=stock_name,
        news_summary=news_summary,
        forum_summary=forum_summary,
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=AI_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as e:
        return f"AI 分析暫時無法取得：{e}"


def _to_yahoo_symbol(stock_id):
    """台股上市 .TW、上櫃 .TWO；用 twstock 對照表判斷，找不到預設 .TW。"""
    if not is_tw_ticker(stock_id):
        return stock_id
    try:
        import twstock
        info = twstock.codes.get(stock_id)
        if info and info.market and "上櫃" in info.market:
            return f"{stock_id}.TWO"
    except Exception:
        pass
    return f"{stock_id}.TW"


def _format_pct(pct):
    if pct is None:
        return "N/A"
    sign = "+" if pct >= 0 else "-"
    a = abs(pct)
    if a >= 100:
        body = f"{a:.0f}%"
    elif a >= 10:
        body = f"{a:.1f}%"
    else:
        body = f"{a:.2f}%"
    return f"{sign}{body}"


def get_stock_quote_with_history(stock_id):
    """回 dict 含當前價、日漲跌、5 日 / 1 月漲跌；失敗回 None。"""
    symbol = _to_yahoo_symbol(stock_id)
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = requests.get(
            url,
            params={"interval": "1d", "range": "3mo"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        data = r.json()
        result = (data.get("chart", {}) or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta", {}) or {}
        price = meta.get("regularMarketPrice")
        prev = meta.get("previousClose") or meta.get("chartPreviousClose")
        if price is None or prev is None:
            return None

        change = price - prev
        pct = (change / prev * 100) if prev else None

        # 從歷史 close 算 5 日 / ~1 月（22 交易日）漲跌
        closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0]
                  .get("close") or [])
        valid = [c for c in closes if c is not None]
        pct_5d = pct_1mo = None
        if len(valid) >= 6:
            pct_5d = (price - valid[-6]) / valid[-6] * 100  # -6 因為 -1 是今天
        if len(valid) >= 23:
            pct_1mo = (price - valid[-23]) / valid[-23] * 100
        return {
            "price": price, "change": change, "pct": pct,
            "pct_5d": pct_5d, "pct_1mo": pct_1mo,
        }
    except Exception as e:
        print(f"股價歷史抓取失敗 {symbol}: {e}")
        return None


def get_security_intro(stock_id, name):
    """AI 用內建知識生成 1-2 句中文簡介；不熟回空字串、整段就不顯示。"""
    label = name if name and name != stock_id else stock_id
    prompt = (
        f"你是金融分析助理。請用 1-2 句繁體中文簡介這檔標的：\n"
        f"代號：{stock_id}\n"
        f"名稱：{label}\n\n"
        f"嚴格規則：\n"
        f"- 重點放在「是什麼公司或 ETF、做什麼產業/追蹤什麼指數」\n"
        f"- 1-2 句即可，不要開場白、不要結語、不要 Markdown\n"
        f"- 如果不熟悉這個代號或無法確定，**只回三個字**：無資料"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",  # haiku 4.5 便宜快速
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip() if msg.content else ""
        # AI 表示不熟悉就跳過
        if text in ("無資料", "無資料。", "無", "") or "不熟悉" in text or "無法確定" in text:
            return ""
        return text
    except Exception as e:
        print(f"簡介生成失敗 {stock_id}: {e}")
        return ""


def get_etf_top_holdings(stock_id, top_n=5):
    """ETF 前 N 大持股（透過 yfinance.funds_data.top_holdings）。
    回 list of {symbol, name, weight}；不是 ETF 或新上市無歷史資料就回 None。"""
    symbol = _to_yahoo_symbol(stock_id)
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        try:
            top = ticker.funds_data.top_holdings
        except Exception:
            # 不是 ETF 或 yfinance 認為沒 funds_data 都會 raise
            return None
        if top is None or top.empty:
            return None
        out = []
        for sym, row in top.head(top_n).iterrows():
            weight_raw = row.get("Holding Percent")
            weight = weight_raw * 100 if isinstance(weight_raw, (int, float)) else None
            out.append({
                "symbol": str(sym) if sym else "",
                "name": str(row.get("Name", "") or ""),
                "weight": weight,
            })
        return out
    except Exception as e:
        print(f"ETF 持股抓取失敗 {stock_id}: {e}")
        return None


def _format_holdings_block(holdings):
    lines = []
    for h in holdings:
        sym = (h.get("symbol") or "").strip()
        name = (h.get("name") or "").strip()
        weight = h.get("weight")
        weight_s = f"{weight:.2f}%" if weight is not None else "N/A"
        if sym and name and sym != name:
            display = f"{sym} {name}"
        elif name:
            display = name
        elif sym:
            display = sym
        else:
            display = "?"
        lines.append(f"  {weight_s}｜{display}")
    return "\n".join(lines)


def _format_quote_block(stock_id, quote):
    """組成股價 HTML 區塊。"""
    is_us = not is_tw_ticker(stock_id)
    prefix = "$" if is_us else ""
    price = quote["price"]
    if abs(price) >= 1000:
        price_s = f"{prefix}{price:,.0f}"
    else:
        price_s = f"{prefix}{price:,.2f}"
    pct = quote["pct"]
    emoji = "🟢" if pct is not None and pct >= 0 else "🔴"

    lines = [
        f"{emoji} 現價｜{price_s}",
        f"日漲跌｜{_format_pct(pct)}",
    ]
    if quote.get("pct_5d") is not None:
        lines.append(f"五日漲跌｜{_format_pct(quote['pct_5d'])}")
    if quote.get("pct_1mo") is not None:
        lines.append(f"月漲跌｜{_format_pct(quote['pct_1mo'])}")
    return "\n".join(lines)


def get_stock_report(stock_id):
    print(f"處理股票：{stock_id}")
    stock_name = get_stock_name(stock_id)

    yahoo_news = get_yahoo_news(stock_id)
    cnyes_news = get_cnyes_news(stock_id)
    translate_titles(yahoo_news + cnyes_news)

    ptt_articles = get_ptt_articles(stock_id)
    reddit_stocks = get_reddit_posts(stock_id, "stocks")
    reddit_wsb = get_reddit_posts(stock_id, "wallstreetbets")
    stocktwits_msgs = get_stocktwits_messages(stock_id)
    dcard_posts = get_dcard_posts(stock_id)

    # ── 英文論壇（Reddit + StockTwits）合併，按綜合熱度（score+留言）取前 3 ──
    for p in reddit_stocks + reddit_wsb:
        p['source'] = f"r/{p.get('subreddit', 'reddit')}"
    for m in stocktwits_msgs:
        m['source'] = 'StockTwits'
        m.setdefault('comments', 0)
    english_forum_top = sorted(
        reddit_stocks + reddit_wsb + stocktwits_msgs,
        key=lambda x: int(x.get('heat', 0)) + int(x.get('comments', 0)),
        reverse=True,
    )[:3]
    # 把英文標題翻成中文（直接在 dict 上補 title_zh 欄位）
    translate_titles(english_forum_top)

    news_titles = [f"{n['title']}（{n['title_zh']}）" if n.get('title_zh') else n['title']
                   for n in yahoo_news + cnyes_news]
    news_summary = "\n".join(f"• {t}" for t in news_titles) or "暫無新聞"

    forum_lines = []
    forum_lines += [f"[PTT {a['heat']}推] {a['title']}" for a in ptt_articles]
    forum_lines += [
        f"[{a.get('source','EN')} {a['heat']}↑/{a.get('comments',0)}💬] "
        f"{a['title']}" + (f"（{a['title_zh']}）" if a.get('title_zh') else "")
        for a in english_forum_top
    ]
    forum_lines += [f"[Dcard {a['heat']}熱] {a['title']}" for a in dcard_posts]
    forum_summary = "\n".join(forum_lines) or "暫無相關討論"

    ai_analysis = get_ai_analysis(stock_id, news_summary, forum_summary)

    # 標題：name 跟 id 一樣（沒在 STOCK_NAMES 找到）就只顯示 id 一次
    header = stock_id if stock_name == stock_id else f"{stock_id} {stock_name}"
    sections = [f"<b>📌 {header}</b>"]

    # 簡介（AI 不熟就回空字串、整段不顯示）
    intro = get_security_intro(stock_id, stock_name)
    if intro:
        sections.append(f"<b>📖 簡介</b>\n{intro}")

    # 股價區塊（方便快速掃）
    quote = get_stock_quote_with_history(stock_id)
    if quote:
        sections.append(f"<b>💰 股價</b>\n{_format_quote_block(stock_id, quote)}")

    # ETF 前五大持股（一般個股 yfinance 不會回 funds_data，自動 None 略過）
    holdings = get_etf_top_holdings(stock_id)
    if holdings:
        sections.append(f"<b>📦 ETF 前五大持股</b>\n{_format_holdings_block(holdings)}")

    news_blocks = []
    yahoo_block = format_news_html(yahoo_news)
    if yahoo_block:
        news_blocks.append(f"<i>Yahoo Finance</i>\n{yahoo_block}")
    cnyes_block = format_news_html(cnyes_news)
    if cnyes_block:
        news_blocks.append(f"<i>鉅亨網</i>\n{cnyes_block}")
    if news_blocks:
        sections.append("<b>📰 最新新聞</b>\n" + "\n\n".join(news_blocks))

    # 各論壇都取前 3；format_forum_html 沒文章會回空字串，下面 if body 會把整段砍掉
    forum_specs = [
        ("🗣️ PTT Stock", "按推文數", ptt_articles, 3),
        ("🌐 英文論壇", "Reddit + StockTwits 綜合熱度", english_forum_top, 3),
        ("🎴 Dcard 股票版", "按熱度", dcard_posts, 3),
    ]
    for title, hint, items, limit in forum_specs:
        body = format_forum_html(items, limit=limit)
        if body:
            sections.append(f"<b>{title}</b>（{hint}）\n{body}")

    if ai_analysis and ai_analysis.strip():
        sections.append(f"<b>🤖 新聞 + 論壇解讀</b>\n{ai_analysis}")

    return "\n\n".join(sections) + "\n"
