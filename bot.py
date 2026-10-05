import os
import re
import json
import html
import csv
import io
from functools import lru_cache
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
GITHUB_EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "")

STATE_FILE = "seen_splits.json"
SOURCE_ERRORS = []
CALENDAR_OK = False

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/html,application/xml,*/*",
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
        raise RuntimeError("TELEGRAM_TOKEN / CHAT_ID not set")
    if len(text) > 4000:
        chunk = ""
        for line in text.splitlines():
            if len(chunk) + len(line) + 1 > 4000:
                send_telegram(chunk.rstrip())
                chunk = ""
            chunk += line + "\n"
        if chunk:
            send_telegram(chunk.rstrip())
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=30)
    print("Telegram status:", r.status_code)
    if r.status_code != 200 or not r.json().get("ok"):
        raise RuntimeError(f"Telegram delivery failed (HTTP {r.status_code})")


def safe_get(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            SOURCE_ERRORS.append(f"{url.split('?')[0]}: HTTP {r.status_code}")
            print("SOURCE ERROR:", SOURCE_ERRORS[-1])
        return r
    except Exception as e:
        SOURCE_ERRORS.append(f"{url.split('?')[0]}: {type(e).__name__}")
        print("REQUEST ERROR:", SOURCE_ERRORS[-1])
        return None


def safe_get_json(url: str):
    r = safe_get(url)
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        SOURCE_ERRORS.append(f"{url.split('?')[0]}: invalid JSON")
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
        ("%m/%d/%Y", r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
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


def effective_date(text):
    # Never use the publication/record date as the effective split date.
    date_pattern = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.? \d{1,2}, \d{4})"
    for cue in [r"(?:begin|commence|start)\s+trading", r"(?:become|be|will be)?\s*effective", r"split[- ]adjusted basis"]:
        for match in re.finditer(cue + r"[^.;]{0,180}?(" + date_pattern + r")", text, re.I):
            return parse_date_any(match.group(1))
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


@lru_cache(maxsize=512)
def get_fmp_profile(symbol: str):
    directory = get_symbol_directory()
    if symbol in directory:
        return directory[symbol]
    if not FMP_API_KEY:
        return {"company": symbol, "exchange": ""}
    url = f"https://financialmodelingprep.com/stable/profile?symbol={symbol}&apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        p = data[0]
        return {
            "company": p.get("companyName") or symbol,
            "exchange": p.get("exchangeShortName") or p.get("exchange") or "",
        }

    if isinstance(data, dict) and "symbol" in data:
        return {
            "company": data.get("companyName") or symbol,
            "exchange": data.get("exchangeShortName") or data.get("exchange") or "",
        }

    return {
        "company": symbol,
        "exchange": "",
    }


@lru_cache(maxsize=1)
def get_symbol_directory():
    directory = {}
    exchange_names = {"A": "NYSE American", "N": "NYSE", "P": "NYSE ARCA", "Z": "BATS", "V": "IEX"}
    for filename in ["nasdaqlisted.txt", "otherlisted.txt"]:
        r = safe_get("https://www.nasdaqtrader.com/dynamic/SymDir/" + filename)
        if r is None or r.status_code != 200:
            continue
        rows = list(csv.DictReader(io.StringIO(r.text), delimiter="|"))
        if not rows or "Security Name" not in rows[0]:
            SOURCE_ERRORS.append(filename + ": invalid symbol directory")
            continue
        for row in rows:
            symbol = row.get("Symbol") or row.get("ACT Symbol")
            if not symbol or symbol.startswith("File Creation") or row.get("Test Issue") == "Y":
                continue
            exchange = "NASDAQ" if filename == "nasdaqlisted.txt" else exchange_names.get(row.get("Exchange"), "")
            directory[symbol] = {"company": row["Security Name"], "exchange": exchange}
    return directory


def fetch_nasdaq_calendar():
    global CALENDAR_OK
    data = safe_get_json("https://api.nasdaq.com/api/calendar/splits")
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        SOURCE_ERRORS.append("Nasdaq split calendar unavailable")
        return []
    payload = data["data"]
    rows = payload.get("rows")
    as_of = payload.get("asOf", "")
    try:
        age = (datetime.now(timezone.utc).date() - datetime.strptime(as_of, "%a, %b %d, %Y").date()).days
    except ValueError:
        age = 999
    if age < 0 or age > 3 or (rows is not None and not isinstance(rows, list)) or "rows" not in payload:
        SOURCE_ERRORS.append("Nasdaq split calendar stale or malformed")
        return []
    directory = get_symbol_directory()
    CALENDAR_OK = bool(directory)
    result = []
    for row in rows or []:
        symbol = row.get("symbol", "")
        profile = get_fmp_profile(symbol)
        item = normalize_item(symbol, row.get("name"), profile["exchange"], parse_date_any(row.get("executionDate", "")), row.get("ratio"), "https://www.nasdaq.com/market-activity/stock-splits")
        if item:
            result.append(item)
    print(f"Nasdaq calendar: {len(rows or [])} rows; {len(result)} eligible upcoming splits")
    return result


def fetch_fmp_calendar():
    global CALENDAR_OK
    if not FMP_API_KEY:
        SOURCE_ERRORS.append("FMP_API_KEY missing")
        return []
    today = datetime.now(timezone.utc).date()
    data = safe_get_json(f"https://financialmodelingprep.com/stable/splits-calendar?from={today}&to={today + timedelta(days=60)}&apikey={FMP_API_KEY}")
    if not isinstance(data, list):
        SOURCE_ERRORS.append("FMP split calendar unavailable")
        return []
    directory = get_symbol_directory()
    CALENDAR_OK = CALENDAR_OK or bool(directory)
    result = []
    for row in data:
        profile = get_fmp_profile(row.get("symbol", ""))
        ratio = f"{row['numerator']}:{row['denominator']}" if row.get("numerator") and row.get("denominator") else row.get("ratio")
        item = normalize_item(row.get("symbol"), profile["company"], profile["exchange"], row.get("date"), ratio, "https://site.financialmodelingprep.com/developer/docs/stable/splits-calendar")
        if item:
            result.append(item)
    print(f"FMP calendar: {len(data)} rows; {len(result)} eligible upcoming splits")
    return result


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

    split_date = effective_date(text)
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
        lines.append("No confirmed upcoming splits in the available calendar." if CALENDAR_OK else "Split calendar unavailable. Upcoming splits could not be verified; this does not mean there are none.")
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
    if GITHUB_EVENT_NAME in {"workflow_dispatch", "push"}:
        return True

    now_utc = datetime.now(timezone.utc)
    today_key = now_utc.strftime("%Y-%m-%d")
    last_sent = state.get("daily_reports", {}).get("splits")

    return last_sent != today_key


def main():
    state = load_state()
    state.setdefault("announced", {})
    state.setdefault("daily_reports", {})
    refresh_state(state)

    candidates = fetch_nasdaq_calendar() + fetch_fmp_calendar() + fetch_wire_sources()
    # Calendar and news can describe the same event. Send it once.
    unique = {}
    for item in candidates:
        key = f"{item['symbol']}|{item['date']}|{item['ratio']}"
        unique.setdefault(key, item)
    items = list(unique.values())
    new_items = []

    for item in items:
        key = f"{item['symbol']}|{item['date']}|{item['ratio']}"
        if key not in state["announced"]:
            new_items.append(item)
        else:
            state["announced"][key] = item

    for item in new_items:
        send_telegram(format_announcement(item))
        key = f"{item['symbol']}|{item['date']}|{item['ratio']}"
        state["announced"][key] = item
        save_state(state)

    current = list(state["announced"].values())
    current.sort(key=lambda x: (x["date"], x["symbol"]))

    if should_send_daily_report(state):
        report = format_daily(current)
        if current and not CALENDAR_OK:
            report += "\n\nCalendar unavailable; showing previously confirmed events and news only."
        send_telegram(report)
        state["daily_reports"]["splits"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    save_state(state)
    print(f"Done. New: {len(new_items)}. Total tracked: {len(current)}")
    print(f"Calendar healthy: {CALENDAR_OK}. Source errors: {len(SOURCE_ERRORS)}")
    if not CALENDAR_OK:
        raise SystemExit("No healthy split calendar; check source diagnostics above")


if __name__ == "__main__":
    main()
