import os
import re
import json
import html
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

STATE_FILE = "seen_splits.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# Открытые источники новостей/релизов по сплитам
SOURCES = [
    {
        "name": "BusinessWire",
        "url": "https://www.businesswire.com/newsroom/subject/stock-split",
    },
    {
        "name": "PRNewswire",
        "url": "https://www.prnewswire.com/news-releases/financial-services-latest-news/stock-split-list/",
    },
    {
        "name": "GlobeNewswire",
        "url": "https://www.globenewswire.com/search/keyword/Reverse%2520Stock%2520Split",
    },
    {
        "name": "NasdaqPress",
        "url": "https://www.nasdaq.com/search?q=reverse%20stock%20split",
    },
]

ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "NYSEARCA", "NYSE AMERICAN", "BATS"}


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
    requests.post(url, json=payload, timeout=30)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"announced": {}}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"announced": {}}
            data.setdefault("announced", {})
            return data
    except Exception:
        return {"announced": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def days_left(date_str):
    try:
        split_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        return (split_date - today).days
    except Exception:
        return None


def format_days_left(days):
    return "1 day left" if days == 1 else f"{days} days left"


def parse_date_from_text(text: str):
    patterns = [
        r"([A-Z][a-z]+ \d{1,2}, \d{4})",
        r"(\d{4}-\d{2}-\d{2})",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        raw = m.group(1)

        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass

    return None


def parse_ratio_from_text(text: str):
    text = text.replace("&nbsp;", " ")
    patterns = [
        r"(\d+(?:\.\d+)?)[-\s]*for[-\s]*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return f"{m.group(1)}:{m.group(2)}"

    return None


def parse_symbol_from_text(text: str):
    # пытаемся найти (NASDAQ: TICKER) / (NYSE: TICKER) / ticker
    patterns = [
        r"\((NASDAQ|NYSE|AMEX|ARCA|NYSE American|BATS)[:\s]+([A-Z]{1,6})\)",
        r"ticker[:\s]+([A-Z]{1,6})",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            if len(m.groups()) == 2:
                return m.group(2).upper()
            return m.group(1).upper()

    # запасной вариант — короткий тикер в скобках
    m = re.search(r"\(([A-Z]{1,6})\)", text)
    if m:
        return m.group(1).upper()

    return None


def parse_exchange_from_text(text: str):
    for ex in ALLOWED_EXCHANGES:
        if ex.lower() in text.lower():
            return ex
    return None


def clean_text(s: str):
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_source(source):
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return []
        text = clean_text(r.text)

        # режем текст на крупные куски вокруг упоминаний split
        chunks = re.split(r"(?i)(?=reverse stock split|stock split)", text)

        results = []
        for chunk in chunks:
            lower = chunk.lower()
            if "stock split" not in lower:
                continue

            symbol = parse_symbol_from_text(chunk)
            ratio = parse_ratio_from_text(chunk)
            split_date = parse_date_from_text(chunk)
            exchange = parse_exchange_from_text(chunk)

            if not symbol or not ratio or not split_date:
                continue

            left = days_left(split_date)
            if left is None or left < 0 or left > 60:
                continue

            if exchange and "OTC" in exchange.upper():
                continue

            item = {
                "symbol": symbol,
                "company": symbol,
                "exchange": exchange or "N/A",
                "date": split_date,
                "ratio": ratio,
                "days_left": left,
                "source": source["name"],
            }
            results.append(item)

        return results
    except Exception:
        return []


def merge_items(items):
    merged = {}
    for item in items:
        key = f"{item['symbol']}|{item['date']}|{item['ratio']}"
        if key not in merged:
            merged[key] = item
    result = list(merged.values())
    result.sort(key=lambda x: (x["date"], x["symbol"]))
    return result


def format_announcement(item):
    return (
        "📢 NEW SPLIT ANNOUNCEMENT\n\n"
        f"Ticker: {item['symbol']}\n"
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
            lines.append(f"{i['date']} — {i['symbol']} — {i['ratio']} — {format_days_left(i['days_left'])}")
        lines.append("")

    if next_30:
        lines.append("8–30 days:")
        for i in next_30:
            lines.append(f"{i['date']} — {i['symbol']} — {i['ratio']} — {format_days_left(i['days_left'])}")
        lines.append("")

    if next_60:
        lines.append("31–60 days:")
        for i in next_60:
            lines.append(f"{i['date']} — {i['symbol']} — {i['ratio']} — {format_days_left(i['days_left'])}")

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


def main():
    state = load_state()
    state.setdefault("announced", {})
    refresh_state(state)

    found = []
    for source in SOURCES:
        found.extend(fetch_source(source))

    items = merge_items(found)
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
    send_telegram(format_daily(current))

    save_state(state)
    print(f"Done. New: {len(new_items)}. Total tracked: {len(current)}")


if __name__ == "__main__":
    main()