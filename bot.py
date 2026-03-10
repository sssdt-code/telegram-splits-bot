import os
import json
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timedelta, timezone

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

HEADERS = {"User-Agent": "Mozilla/5.0"}


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


def safe_get(url: str):
    try:
        return requests.get(url, headers=HEADERS, timeout=30)
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


def parse_date(value):
    if value is None:
        return None

    s = str(value).strip()
    if not s:
        return None

    formats = [
        "%Y-%m-%d",
        "%b %d, %Y",
        "%B %d, %Y",
        "%m/%d/%Y",
        "%d %b %Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
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
        return "N/A"

    s = str(raw).strip()
    if not s:
        return "N/A"

    s = s.replace(" ", "")

    # Yahoo often shows 80.00 - 1.00 -> 80:1
    if "-" in s and ":" not in s and "/" not in s:
        parts = [p.strip() for p in s.split("-") if p.strip()]
        if len(parts) == 2:
            left = parts[0].replace(".00", "")
            right = parts[1].replace(".00", "")
            return f"{left}:{right}"

    if "/" in s:
        parts = [p.strip() for p in s.split("/") if p.strip()]
        if len(parts) == 2:
            return f"{parts[0]}:{parts[1]}"

    if "-for-" in s.lower():
        parts = s.lower().split("-for-")
        if len(parts) == 2:
            return f"{parts[0]}:{parts[1]}".upper()

    return s.upper()


def is_allowed_exchange(exchange: str):
    ex = str(exchange or "").upper().strip()
    if not ex:
        return False
    if "OTC" in ex:
        return False
    return any(k in ex for k in ["NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"])


def get_fmp_exchange(symbol: str):
    url = f"https://financialmodelingprep.com/api/v3/profile/{symbol}?apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        p = data[0]
        return p.get("exchangeShortName") or p.get("exchange") or ""

    if isinstance(data, dict):
        return data.get("exchangeShortName") or data.get("exchange") or ""

    return ""


def normalize_item(symbol, company, exchange, date, ratio, source):
    symbol = str(symbol or "").upper().strip()
    if not symbol:
        return None

    date = parse_date(date)
    if not date:
        return None

    left = days_left(date)
    if left is None or left < 0 or left > 30:
        return None

    exchange = str(exchange or "").strip()
    if not is_allowed_exchange(exchange):
        return None

    return {
        "symbol": symbol,
        "company": str(company or symbol).strip() or symbol,
        "exchange": exchange,
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

        out_item = normalize_item(
            symbol=item.get("symbol"),
            company=item.get("companyName"),
            exchange=item.get("exchange"),
            date=item.get("date"),
            ratio=normalize_ratio(item.get("ratio")),
            source="FMP",
        )
        if out_item:
            out.append(out_item)

    return out


def fetch_yahoo():
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=30)

    # Main Yahoo page + regional mirrors
    urls = [
        f"https://finance.yahoo.com/calendar/splits?from={today}&to={end}",
        f"https://ca.finance.yahoo.com/calendar/splits?from={today}&to={end}",
        f"https://nz.finance.yahoo.com/calendar/splits?from={today}&to={end}",
    ]

    out = []
    seen = set()

    for url in urls:
        r = safe_get(url)
        if r is None or r.status_code != 200:
            continue

        try:
            tables = pd.read_html(StringIO(r.text))
        except Exception:
            continue

        for df in tables:
            # Expected columns usually like:
            # Symbol | Company | Date | Split Ratio
            colmap = {str(c).strip().lower(): c for c in df.columns}

            symbol_col = None
            company_col = None
            date_col = None
            ratio_col = None

            for low, orig in colmap.items():
                if "symbol" in low or "ticker" in low:
                    symbol_col = orig
                elif "company" in low or "name" in low:
                    company_col = orig
                elif "date" in low:
                    date_col = orig
                elif "ratio" in low:
                    ratio_col = orig

            if not symbol_col or not date_col or not ratio_col:
                continue

            for _, row in df.iterrows():
                symbol = row.get(symbol_col)
                company = row.get(company_col) if company_col else row.get(symbol_col)
                date = row.get(date_col)
                ratio = row.get(ratio_col)

                if pd.isna(symbol) or pd.isna(date) or pd.isna(ratio):
                    continue

                symbol = str(symbol).upper().strip()
                date = parse_date(date)
                ratio = normalize_ratio(ratio)

                if not symbol or not date:
                    continue

                key = f"{symbol}|{date}|{ratio}"
                if key in seen:
                    continue
                seen.add(key)

                exchange = get_fmp_exchange(symbol)
                item = normalize_item(
                    symbol=symbol,
                    company=company,
                    exchange=exchange,
                    date=date,
                    ratio=ratio,
                    source="Yahoo",
                )
                if item:
                    out.append(item)

    return out


def merge_items(a, b):
    merged = {}

    for item in a + b:
        key = f"{item['symbol']}|{item['date']}|{item['ratio']}"
        if key not in merged:
            merged[key] = item
        else:
            current = merged[key]
            if current["source"] == "FMP" and item["source"] == "Yahoo":
                merged[key] = item
            elif current["company"] == current["symbol"] and item["company"] != item["symbol"]:
                merged[key] = item

    result = list(merged.values())
    result.sort(key=lambda x: (x["date"], x["symbol"]))
    return result


def format_announcement(item):
    return (
        "📢 NEW SPLIT ALERT\n\n"
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

    fmp_items = fetch_fmp()
    yahoo_items = fetch_yahoo()
    splits = merge_items(fmp_items, yahoo_items)

    new_items = []

    for s in splits:
        key = f"{s['symbol']}|{s['date']}|{s['ratio']}"
        if key not in state["announced"]:
            state["announced"][key] = s
            new_items.append(s)
        else:
            state["announced"][key] = s

    for s in new_items:
        send_telegram(format_announcement(s))

    current = list(state["announced"].values())
    current.sort(key=lambda x: (x["date"], x["symbol"]))
    send_telegram(format_daily(current))

    save_state(state)

    print(
        f"Done. New: {len(new_items)}. "
        f"FMP: {len(fmp_items)}. Yahoo: {len(yahoo_items)}. "
        f"Tracked: {len(current)}"
    )


if __name__ == "__main__":
    main()