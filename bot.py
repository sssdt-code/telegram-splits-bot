import os
import json
import requests
from datetime import datetime, timedelta, timezone

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FMP_API_KEY = os.getenv("FMP_API_KEY")

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

DAILY_REPORT_HOUR_UTC = 22  # можно потом поменять


def send_telegram(text: str) -> None:
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


def safe_get_json(url: str):
    response = requests.get(url, timeout=30)

    if response.status_code != 200:
        send_telegram(f"❌ FMP API ERROR {response.status_code}\n{url}")
        return None

    try:
        return response.json()
    except Exception as e:
        send_telegram(f"❌ JSON decode error:\n{type(e).__name__}: {str(e)}")
        return None


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {
            "announced": {},
            "daily_reports": {}
        }

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


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_allowed_exchange(exchange: str) -> bool:
    exchange = str(exchange or "").upper().strip()
    if "OTC" in exchange:
        return False
    return exchange in ALLOWED_EXCHANGES


def days_left(split_date_str: str) -> int | None:
    try:
        split_date = datetime.strptime(split_date_str, "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        return (split_date - today).days
    except Exception:
        return None


def normalize_split_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None

    symbol = str(item.get("symbol", "")).strip()
    if not symbol:
        return None

    exchange = str(item.get("exchange", "")).strip()
    if not is_allowed_exchange(exchange):
        return None

    date = str(item.get("date", "")).strip()
    if not date:
        return None

    ratio = item.get("ratio")
    if ratio:
        ratio = str(ratio).strip()
    else:
        numerator = item.get("numerator")
        denominator = item.get("denominator")
        ratio = f"{numerator}:{denominator}" if numerator and denominator else "N/A"

    company = str(item.get("companyName", "")).strip() or symbol

    left = days_left(date)
    if left is None:
        return None

    return {
        "symbol": symbol,
        "company": company,
        "exchange": exchange,
        "date": date,
        "ratio": ratio,
        "days_left": left,
    }


def get_upcoming_splits(days_ahead: int = 30) -> list[dict]:
    url = f"https://financialmodelingprep.com/stable/splits-calendar?apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if data is None:
        return []

    if not isinstance(data, list):
        send_telegram(f"❌ Unexpected splits format: {type(data).__name__}")
        return []

    cleaned = []
    for raw in data:
        item = normalize_split_item(raw)
        if not item:
            continue

        if 0 <= item["days_left"] <= days_ahead:
            cleaned.append(item)

    cleaned.sort(key=lambda x: (x["date"], x["symbol"]))
    return cleaned


def split_key(item: dict) -> str:
    return f"{item['symbol']}|{item['date']}|{item['ratio']}|{item['exchange']}"


def format_announcement(item: dict) -> str:
    return (
        "📢 NEW SPLIT ANNOUNCEMENT\n\n"
        f"Company: {item['company']}\n"
        f"Ticker: {item['symbol']}\n"
        f"Exchange: {item['exchange']}\n"
        f"Ratio: {item['ratio']}\n"
        f"Split Date: {item['date']}\n"
        f"Days Left: {item['days_left']}"
    )


def should_send_daily_report(state: dict) -> bool:
    now_utc = datetime.now(timezone.utc)
    today_key = now_utc.strftime("%Y-%m-%d")
    last_sent = state.get("daily_reports", {}).get("splits")

    if now_utc.hour < DAILY_REPORT_HOUR_UTC:
        return False

    return last_sent != today_key


def format_daily_report(items: list[dict]) -> str:
    lines = ["🗓 UPCOMING SPLITS", ""]

    if not items:
        lines.append("No upcoming stock splits found.")
        return "\n".join(lines)

    for item in items[:50]:
        lines.append(
            f"{item['date']} — {item['symbol']} — {item['ratio']} — {item['days_left']} days left"
        )

    if len(items) > 50:
        lines.append("")
        lines.append(f"... and {len(items) - 50} more")

    return "\n".join(lines)


def remove_expired(state: dict) -> None:
    announced = state.get("announced", {})
    keep = {}

    for key, item in announced.items():
        try:
            if int(item.get("days_left", -999)) >= 0:
                keep[key] = item
        except Exception:
            continue

    state["announced"] = keep


def refresh_days_left_in_state(state: dict) -> None:
    announced = state.get("announced", {})

    for key, item in list(announced.items()):
        left = days_left(item.get("date", ""))
        if left is None:
            continue
        item["days_left"] = left
        announced[key] = item


def validate_env() -> bool:
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")
    if not FMP_API_KEY:
        missing.append("FMP_API_KEY")

    if missing:
        send_telegram("❌ Missing env vars: " + ", ".join(missing))
        return False

    return True


def main():
    if not validate_env():
        return

    try:
        state = load_state()
        state.setdefault("announced", {})
        state.setdefault("daily_reports", {})

        refresh_days_left_in_state(state)
        remove_expired(state)

        upcoming = get_upcoming_splits(days_ahead=30)
        new_announcements = []

        for item in upcoming:
            key = split_key(item)
            if key not in state["announced"]:
                state["announced"][key] = item
                new_announcements.append(item)
            else:
                state["announced"][key] = item

        for item in new_announcements:
            send_telegram(format_announcement(item))

        current_items = list(state["announced"].values())
        current_items = [x for x in current_items if x.get("days_left", -1) >= 0]
        current_items.sort(key=lambda x: (x["date"], x["symbol"]))

        if should_send_daily_report(state):
            send_telegram(format_daily_report(current_items))
            state["daily_reports"]["splits"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        save_state(state)

        print(
            f"Done. New announcements: {len(new_announcements)}. "
            f"Tracked future splits: {len(current_items)}"
        )

    except Exception as e:
        send_telegram(f"❌ ERROR:\n{type(e).__name__}: {str(e)}")


if __name__ == "__main__":
    main()