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

DAILY_REPORT_HOUR_UTC = 22  # 18:00 в Доминикане при UTC-4


def send_telegram(text: str, parse_mode: str | None = None) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    response = requests.post(url, json=payload, timeout=30)
    print("Telegram:", response.status_code, response.text[:300])


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
        return {"seen": [], "daily_reports": {}}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"seen": [], "daily_reports": {}}
            data.setdefault("seen", [])
            data.setdefault("daily_reports", {})
            return data
    except Exception:
        return {"seen": [], "daily_reports": {}}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_allowed_exchange(exchange: str) -> bool:
    exchange = str(exchange or "").upper().strip()
    if "OTC" in exchange:
        return False
    return exchange in ALLOWED_EXCHANGES


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
    ratio = item.get("ratio")
    if ratio:
        ratio = str(ratio).strip()
    else:
        numerator = item.get("numerator")
        denominator = item.get("denominator")
        ratio = f"{numerator}:{denominator}" if numerator and denominator else "N/A"

    company = str(item.get("companyName", "")).strip()
    link = f"https://financialmodelingprep.com/financial-summary/{symbol}"

    return {
        "symbol": symbol,
        "company": company or symbol,
        "exchange": exchange or "N/A",
        "date": date or "N/A",
        "ratio": ratio,
        "link": link,
    }


def get_upcoming_splits(days_ahead: int = 7) -> list[dict]:
    url = f"https://financialmodelingprep.com/stable/splits-calendar?apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if data is None:
        return []

    if not isinstance(data, list):
        send_telegram(f"❌ Unexpected splits format: {type(data).__name__}")
        return []

    today = datetime.now(timezone.utc).date()
    end_date = today + timedelta(days=days_ahead)

    cleaned = []
    for raw in data:
        item = normalize_split_item(raw)
        if not item:
            continue

        try:
            item_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
        except Exception:
            continue

        if today <= item_date <= end_date:
            cleaned.append(item)

    cleaned.sort(key=lambda x: (x["date"], x["symbol"]))
    return cleaned


def split_key(item: dict) -> str:
    return f"{item['symbol']}|{item['date']}|{item['ratio']}|{item['exchange']}"


def format_split_message(item: dict) -> str:
    return (
        "📊 <b>New Stock Split</b>\n\n"
        f"<b>{item['company']}</b>\n"
        f"Ticker: <b>{item['symbol']}</b>\n"
        f"Exchange: {item['exchange']}\n"
        f"Ratio: <b>{item['ratio']}</b>\n"
        f"Date: {item['date']}\n"
        f"Link: {item['link']}"
    )


def should_send_daily_report(state: dict) -> bool:
    now_utc = datetime.now(timezone.utc)
    today_key = now_utc.strftime("%Y-%m-%d")
    last_sent = state.get("daily_reports", {}).get("splits")

    if now_utc.hour < DAILY_REPORT_HOUR_UTC:
        return False

    return last_sent != today_key


def format_daily_report(items: list[dict]) -> str:
    if not items:
        return "🗓 Daily Split Report\n\nNo upcoming stock splits in the next 7 days on the tracked US exchanges."

    lines = ["🗓 <b>Daily Split Report</b>", "", "Next 7 days:"]
    for item in items[:30]:
        lines.append(
            f"• {item['date']} — <b>{item['symbol']}</b> ({item['exchange']}) — {item['ratio']}"
        )

    if len(items) > 30:
        lines.append("")
        lines.append(f"... and {len(items) - 30} more")

    return "\n".join(lines)


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
        seen = set(state.get("seen", []))

        upcoming = get_upcoming_splits(days_ahead=7)
        new_items = []

        for item in upcoming:
            key = split_key(item)
            if key not in seen:
                seen.add(key)
                new_items.append(item)

        for item in new_items:
            send_telegram(format_split_message(item), parse_mode="HTML")

        if should_send_daily_report(state):
            send_telegram(format_daily_report(upcoming), parse_mode="HTML")
            state["daily_reports"]["splits"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        state["seen"] = sorted(list(seen))[-5000:]
        save_state(state)

        print(f"Done. New sent: {len(new_items)}. Upcoming found: {len(upcoming)}")

    except Exception as e:
        send_telegram(f"❌ ERROR:\n{type(e).__name__}: {str(e)}")


if __name__ == "__main__":
    main()