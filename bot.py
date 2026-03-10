import os
import re
import json
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FMP_API_KEY = "GBzfIZThj87JwZgdGYdPmuGsg39PFUmz"

STATE_FILE = "seen_splits.json"

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

# Если хочешь, можно добавлять ручные тикеры для контроля
PRIORITY_TICKERS = {"ELPW", "KIDZ", "SKYQ"}

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


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


def safe_get(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        return r
    except Exception as e:
        send_telegram(f"❌ REQUEST ERROR\n{type(e).__name__}: {str(e)}")
        return None


def safe_get_json(url: str):
    r = safe_get(url)
    if r is None:
        return None

    if r.status_code != 200:
        send_telegram(f"❌ API ERROR {r.status_code}\n{url}")
        return None

    try:
        return r.json()
    except Exception as e:
        send_telegram(f"❌ JSON ERROR\n{type(e).__name__}: {str(e)}")
        return None


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


def normalize_ratio(raw_ratio=None, numerator=None, denominator=None):
    if raw_ratio not in (None, ""):
        r = str(raw_ratio).strip()
        if "/" in r:
            parts = r.split("/")
            if len(parts) == 2:
                return f"{parts[0]}:{parts[1]}"
        if "-for-" in r:
            parts = r.split("-for-")
            if len(parts) == 2:
                return f"{parts[0]}:{parts[1]}"
        return r

    if numerator and denominator:
        return f"{numerator}:{denominator}"

    return "N/A"


def is_allowed(exchange: str, symbol: str = ""):
    ex = str(exchange or "").upper().strip()
    sym = str(symbol or "").upper().strip()

    if "OTC" in ex:
        return False

    if ex in ALLOWED_EXCHANGES:
        return True

    if sym in PRIORITY_TICKERS:
        return True

    return False


def normalize_split(symbol, company, exchange, date, ratio, source):
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return None

    if not is_allowed(exchange, symbol):
        return None

    date = str(date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return None

    left = days_left(date)
    if left is None or left < 0 or left > 30:
        return None

    return {
        "symbol": symbol,
        "company": str(company or symbol).strip() or symbol,
        "exchange": str(exchange or "N/A").strip(),
        "date": date,
        "ratio": str(ratio or "N/A").strip(),
        "days_left": left,
        "source": source,
    }


def fetch_fmp():
    url = f"https://financialmodelingprep.com/stable/splits-calendar?apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if data is None or not isinstance(data, list):
        return []

    out = []

    for item in data:
        if not isinstance(item, dict):
            continue

        out_item = normalize_split(
            symbol=item.get("symbol"),
            company=item.get("companyName"),
            exchange=item.get("exchange"),
            date=item.get("date"),
            ratio=normalize_ratio(
                raw_ratio=item.get("ratio"),
                numerator=item.get("numerator"),
                denominator=item.get("denominator"),
            ),
            source="FMP",
        )
        if out_item:
            out.append(out_item)

    return out


def fetch_tipranks():
    url = "https://www.tipranks.com/calendars/stock-splits/upcoming"
    r = safe_get(url)
    if r is None or r.status_code != 200:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n", strip=True)

    out = []

    # Пытаемся вытащить строки типа:
    # Mar 10, 2026 KIDZ 1-for-50
    # Mar 12, 2026 ELPW 1-for-80
    # Mar 16, 2026 SKYQ 1-for-8
    pattern = re.compile(
        r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}).{0,80}?([A-Z]{1,6}).{0,80}?(\d+\s*[-/]?\s*for\s*[-/]?\s*\d+|\d+:\d+)",
        re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(text):
        raw_date, symbol, raw_ratio = match.groups()

        try:
            dt = datetime.strptime(raw_date, "%b %d, %Y").strftime("%Y-%m-%d")
        except Exception:
            continue

        ratio = raw_ratio.replace(" ", "").replace("for", ":").replace("-:", ":").replace(":-", ":")
        ratio = ratio.replace("-for-", ":").replace("for", ":").replace("/", ":")
        ratio = ratio.replace("--", "-")
        ratio = ratio.replace("-", "")
        # Если получилось 1:50 или 10:1 — оставляем
        if ":" not in ratio:
            continue

        symbol = symbol.upper()

        out_item = normalize_split(
            symbol=symbol,
            company=symbol,
            exchange="NASDAQ" if symbol in PRIORITY_TICKERS else "N/A",
            date=dt,
            ratio=ratio,
            source="TipRanks",
        )
        if out_item:
            out.append(out_item)

    # Дедуп внутри источника
    seen = set()
    unique = []
    for x in out:
        key = f"{x['symbol']}|{x['date']}|{x['ratio']}"
        if key not in seen:
            seen.add(key)
            unique.append(x)

    return unique


def merge_splits(*sources):
    merged = {}

    for source_list in sources:
        for item in source_list:
            key = f"{item['symbol']}|{item['date']}|{item['ratio']}|{item['exchange']}"
            if key not in merged:
                merged[key] = item
            else:
                # Предпочитаем запись, где есть company не просто ticker
                current = merged[key]
                if current["company"] == current["symbol"] and item["company"] != item["symbol"]:
                    merged[key] = item

    result = list(merged.values())
    result.sort(key=lambda x: (x["date"], x["symbol"]))
    return result


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

    if not next_7 and not next_30:
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

    return "\n".join(lines).strip()


def main():
    state = load_state()
    state.setdefault("announced", {})

    fmp_items = fetch_fmp()
    tipranks_items = fetch_tipranks()
    splits = merge_splits(fmp_items, tipranks_items)

    new_items = []

    for s in splits:
        key = f"{s['symbol']}|{s['date']}|{s['ratio']}|{s['exchange']}"
        if key not in state["announced"]:
            state["announced"][key] = s
            new_items.append(s)
        else:
            state["announced"][key] = s

    for s in new_items:
        send_telegram(format_announcement(s))

    send_telegram(format_daily(splits))

    save_state(state)

    print(
        f"Done. New: {len(new_items)}. "
        f"FMP: {len(fmp_items)}. TipRanks: {len(tipranks_items)}. "
        f"Merged upcoming: {len(splits)}"
    )


if __name__ == "__main__":
    main()