import os
import json
import requests
from datetime import datetime, timezone

# ===============================
# CONFIG
# ===============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Временно захардкожен ключ
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
}

DAILY_REPORT_HOUR_UTC = 22  # 18:00 DR time


# ===============================
# TELEGRAM
# ===============================

def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or CHAT_ID")
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


# ===============================
# STATE
# ===============================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"announced": {}, "daily_reports": {}}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"announced": {}, "daily_reports": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ===============================
# HELPERS
# ===============================

def days_left(date_str):
    try:
        split_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        return (split_date - today).days
    except Exception:
        return None


def normalize_ratio(item):
    ratio = item.get("ratio")

    if ratio:
        r = str(ratio)
        if "/" in r:
            parts = r.split("/")
            if len(parts) == 2:
                return f"{parts[0]}:{parts[1]}"
        return r

    n = item.get("numerator")
    d = item.get("denominator")
    if n and d:
        return f"{n}:{d}"

    return "N/A"


def is_allowed(exchange):
    if not exchange:
        return False
    exchange = exchange.upper()
    if "OTC" in exchange:
        return False
    return exchange in ALLOWED_EXCHANGES


# ===============================
# DATA FETCH
# ===============================

def get_splits():
    url = f"https://financialmodelingprep.com/stable/splits-calendar?apikey={FMP_API_KEY}"
    r = requests.get(url, timeout=30)

    if r.status_code != 200:
        send_telegram(f"❌ FMP API ERROR {r.status_code}")
        return []

    data = r.json()

    if not isinstance(data, list):
        return []

    cleaned = []

    for item in data:
        if not isinstance(item, dict):
            continue

        exchange = item.get("exchange")
        if not is_allowed(exchange):
            continue

        date = item.get("date")
        left = days_left(date)

        if left is None:
            continue

        if left < 0 or left > 30:
            continue

        cleaned.append({
            "symbol": item.get("symbol"),
            "company": item.get("companyName") or item.get("symbol"),
            "exchange": exchange,
            "date": date,
            "ratio": normalize_ratio(item),
            "days_left": left,
        })

    cleaned.sort(key=lambda x: (x["date"], x["symbol"]))
    return cleaned


# ===============================
# FORMATTING
# ===============================

def format_announcement(item):
    return (
        "📢 NEW SPLIT ANNOUNCEMENT\n\n"
        f"{item['company']} ({item['symbol']})\n"
        f"Exchange: {item['exchange']}\n"
        f"Ratio: {item['ratio']}\n"
        f"Split Date: {item['date']}\n"
        f"Days Left: {item['days_left']}"
    )


def format_daily(items):
    lines = ["🗓 UPCOMING SPLITS", ""]

    if not items:
        lines.append("No upcoming stock splits found.")
        return "\n".join(lines)

    for i in items:
        lines.append(
            f"{i['date']} — {i['symbol']} — {i['ratio']} — {i['days_left']} days"
        )

    return "\n".join(lines)


# ===============================
# MAIN
# ===============================

def main():
    state = load_state()
    state.setdefault("announced", {})
    state.setdefault("daily_reports", {})

    splits = get_splits()

    new = []

    for s in splits:
        key = f"{s['symbol']}|{s['date']}|{s['ratio']}"

        if key not in state["announced"]:
            state["announced"][key] = s
            new.append(s)

    # New announcements
    for s in new:
        send_telegram(format_announcement(s))

    # Daily report (always send once per day after hour)
    now = datetime.now(timezone.utc)
    today_key = now.strftime("%Y-%m-%d")

    if now.hour >= DAILY_REPORT_HOUR_UTC:
        if state["daily_reports"].get("splits") != today_key:
            send_telegram(format_daily(splits))
            state["daily_reports"]["splits"] = today_key

    save_state(state)

    print(f"Done. New: {len(new)}. Total upcoming: {len(splits)}")


if __name__ == "__main__":
    main()