import os
import re
import json
import html
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FMP_API_KEY = os.getenv("FMP_API_KEY") or "GBzfIZThj87JwZgdGYdPmuGsg39PFUmz"
GITHUB_EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "")

STATE_FILE = "seen_splits.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

ALLOWED_EXCHANGES = {
    "NASDAQ",
    "NYSE",
    "AMEX",
    "ARCA",
    "NYSEARCA",
    "NYSE AMERICAN",
    "BATS",
    "NASDAQCM",
    "NASDAQGM",
    "NASDAQGS",
}

NEWS_QUERIES = [
    '("stock split" OR "reverse stock split") (site:nasdaq.com OR site:businesswire.com OR site:globenewswire.com OR site:prnewswire.com)',
    '("share consolidation" OR "reverse split") (site:nasdaq.com OR site:businesswire.com OR site:globenewswire.com OR site:prnewswire.com)',
]


def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=30)
    print("Telegram status:", r.status_code)
    print("Telegram body:", r.text[:300])


def safe_get(url: str):
    try:
        return requests.get(url, headers=HEADERS, timeout=30)
    except Exception as e:
        print("REQUEST ERROR:", type(e).__name__, str(e))
        return None


def safe_get_json(url: str):
    r = safe_get(url)
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"announced": {}, "daily_reports": {}}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"announced": {}, "daily_reports": {}}
            data.setdefault("announced", {})
            data.setdefault("daily_reports", {})
            return data
    except Exception:
        return {"announced": {}, "daily_reports": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def parse_date_any(text: str):
    patterns = [
        ("%Y-%m-%d", r"\b\d{4}-\d{2}-\d{2}\b"),
        ("%B %d, %Y", r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}\b"),
        ("%b %d, %Y", r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.? \d{1,2}, \d{4}\b"),
    ]

    for fmt, pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        raw = m.group(0).replace(".", "")
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass

    return None


def days_left(date_str):
    try:
        split_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        return (split_date - today).days
    except Exception:
        return None


def format_days_left(days):
    return "1 day left" if days == 1 else f"{days} days left"


def normalize_ratio(raw):
    if raw is None:
        return None

    s = str(raw).strip()
    if not s:
        return None

    s = s.replace(" ", "")
    s = s.replace("-for-", ":").replace("/", ":").replace("for", ":").replace("FOR", ":")
    s = s.replace(".00", "")

    # 1-50 -> 1:50
    m = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", s)
    if m:
        return f"{m.group(1)}:{m.group(2)}"

    # 1:50
    m = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", s)
    if m:
        return f"{m.group(1)}:{m.group(2)}"

    return s


def find_ratio(text: str):
    patterns = [
        r"(\d+(?:\.\d+)?)\s*[- ]?for[- ]?(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return f"{m.group(1)}:{m.group(2)}"

    return None


def find_symbol(text: str):
    # (NASDAQ: KIDZ) / (NYSE: ABCD)
    patterns = [
        r"\((?:NASDAQ|NYSE|AMEX|ARCA|NYSE American|BATS)\s*[:\-]\s*([A-Z]{1,6})\)",
        r"ticker\s*[:\-]\s*([A-Z]{1,6})\b",
        r"symbol\s*[:\-]\s*([A-Z]{1,6})\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).upper()

    # запасной: первый подходящий тикер в скобках
    for m in re.finditer(r"\(([A-Z]{1,6})\)", text):
        sym = m.group(1).upper()
        if 1 <= len(sym) <= 6:
            return sym

    return None


def get_fmp_profile(symbol: str):
    url = f"https://financialmodelingprep.com/api/v3/profile/{symbol}?apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        p = data[0]
        return {
            "company": p.get("companyName") or symbol,
            "exchange": p.get("exchangeShortName") or p.get("exchange") or "",
        }

    if isinstance(data, dict):
        return {
            "company": data.get("companyName") or symbol,
            "exchange": data.get("exchangeShortName") or data.get("exchange") or "",
        }

    return {
        "company": symbol,
        "exchange": "",
    }


def is_allowed_exchange(exchange: str):
    ex = str(exchange or "").upper().strip()
    if not ex:
        return False
    if "OTC" in ex:
        return False
    return any(k in ex for k in ["NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"])


def normalize_item(symbol, company, exchange, date, ratio, source):
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return None

    left = days_left(date)
    if left is None or left < 0 or left > 60:
        return None

    exchange = str(exchange or "").strip()
    if not is_allowed_exchange(exchange):
        return None

    return {
        "symbol": symbol,
        "company": str(company or symbol).strip() or symbol,
        "exchange": exchange,
        "date": date,
        "ratio": normalize_ratio(ratio) or "N/A",
        "days_left": left,
        "source": source,
    }


def extract_article_text(url: str):
    r = safe_get(url)
    if r is None or r.status_code != 200:
        return ""

    text = r.text
    text = html.unescape(text)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_google_news_rss(query: str):
    rss_url = "https://news.google.com/rss/search?q=" + requests.utils.quote(query)
    r = safe_get(rss_url)
    if r is None or r.status_code != 200:
        return []

    try:
        root = ET.fromstring(r.text)
    except Exception:
        return []

    items = []
    channel = root.find("channel")
    if channel is None:
        return items

    for item in channel.findall("item"):
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        pub_date = item.findtext("pubDate", default="")
        items.append({
            "title": title,
            "link": link,
            "pub_date": pub_date,
        })

    return items


def parse_news_item(news_item):
    url = news_item.get("link", "")
    title = news_item.get("title", "")
    body = extract_article_text(url)
    text = f"{title} {body}"

    if "stock split" not in text.lower() and "reverse stock split" not in text.lower() and "share consolidation" not in text.lower():
        return None

    symbol = find_symbol(text)
    if not symbol:
        return None

    ratio = find_ratio(text)
    if not ratio:
        return None

    split_date = parse_date_any(text)
    if not split_date:
        return None

    profile = get_fmp_profile(symbol)
    exchange = profile.get("exchange", "")
    company = profile.get("company", symbol)

    return normalize_item(
        symbol=symbol,
        company=company,
        exchange=exchange,
        date=split_date,
        ratio=ratio,
        source=url,
    )


def fetch_wire_sources():
    raw_news = []
    seen_links = set()

    for query in NEWS_QUERIES:
        for item in fetch_google_news_rss(query):
            link = item.get("link", "")
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            raw_news.append(item)

    parsed = []
    seen_keys = set()

    for item in raw_news:
        parsed_item = parse_news_item(item)
        if not parsed_item:
            continue

        key = f"{parsed_item['symbol']}|{parsed_item['date']}|{parsed_item['ratio']}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        parsed.append(parsed_item)

    parsed.sort(key=lambda x: (x["date"], x["symbol"]))
    return parsed


def format_announcement(item):
    return (
        "📢 NEW SPLIT ANNOUNCEMENT\n\n"
        f"{item['company']} ({item['symbol']})\n"
        f"Exchange: {item['exchange']}\n"
        f"Ratio: {item['ratio']}\n"
        f"Split Date: {item['date']}\n"
        f"{format_days_left(item['days_left'])}\n"
        f"Source: {item['source']}"
    )


def format_daily(items):
    lines = ["🗓 UPCOMING SPLITS", ""]

    next_7 = [i for i in items if 0 <= i["days_left"] <= 7]
    next_30 = [i for i in items if 8 <= i["days_left"] <= 30]
    next_60 = [i for i in items if 31 <= i["days_left"] <= 60]

    if not next_7 and not next_30 and not next_60:
        lines.append("No upcoming stock splits found.")
        return "\n".join(lines)

    if next_7:
        lines.append("Next 7 days:")
        for i in next_7:
            lines.append(
                f"{i['date']} — {i['symbol']} — {i['ratio']} — {format_days_left(i['days_left'])}"
            )
        lines.append("")

    if next_30:
        lines.append("8–30 days:")
        for i in next_30:
            lines.append(
                f"{i['date']} — {i['symbol']} — {i['ratio']} — {format_days_left(i['days_left'])}"
            )
        lines.append("")

    if next_60:
        lines.append("31–60 days:")
        for i in next_60:
            lines.append(
                f"{i['date']} — {i['symbol']} — {i['ratio']} — {format_days_left(i['days_left'])}"
            )

    return "\n".join(lines).strip()


def refresh_state(state):
    keep = {}
    for key, item in state.get("announced", {}).items():
        left = days_left(item.get("date", ""))
        if left is None or left < 0:
            continue
        item["days_left"] = left
        keep[key] = item
    state["announced"] = keep


def should_send_daily_report(state):
    if GITHUB_EVENT_NAME == "workflow_dispatch":
        return True

    now_utc = datetime.now(timezone.utc)
    today_key = now_utc.strftime("%Y-%m-%d")
    last_sent = state.get("daily_reports", {}).get("splits")

    if now_utc.hour < 22:
        return False

    return last_sent != today_key


def main():
    state = load_state()
    state.setdefault("announced", {})
    state.setdefault("daily_reports", {})
    refresh_state(state)

    items = fetch_wire_sources()
    new_items = []

    for item in items:
        key = f"{item['symbol']}|{item['date']}|{item['ratio']}"
        if key not in state["announced"]:
            state["announced"][key] = item
            new_items.append(item)
        else:
            state["announced"][key] = item

    for item in new_items:
        send_telegram(format_announcement(item))

    current = list(state["announced"].values())
    current.sort(key=lambda x: (x["date"], x["symbol"]))

    if should_send_daily_report(state):
        send_telegram(format_daily(current))
        state["daily_reports"]["splits"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    save_state(state)
    print(f"Done. New: {len(new_items)}. Total tracked: {len(current)}")


if __name__ == "__main__":
    main()