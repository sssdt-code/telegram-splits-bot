import os
import requests
from datetime import datetime

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FMP_API_KEY = os.getenv("FMP_API_KEY")

ALLOWED_EXCHANGES = {
    "NASDAQ",
    "NYSE",
    "AMEX",
    "ARCA",
    "NYSEARCA",
    "NYSE AMERICAN",
    "BATS",
}

BASE_URL = "https://financialmodelingprep.com/api/v3"


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    requests.post(url, json=payload, timeout=30)


def safe_get_json(url: str):
    response = requests.get(url, timeout=30)

    if response.status_code != 200:
        send_telegram(f"❌ API ERROR {response.status_code}\n{url}")
        return None

    try:
        return response.json()
    except Exception:
        send_telegram("❌ Ошибка декодирования JSON")
        return None


def get_recent_splits():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    url = f"{BASE_URL}/stock_split_calendar?from={today}&to={today}&apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if data is None:
        return []

    if isinstance(data, dict):
        if "historical" in data and isinstance(data["historical"], list):
            return data["historical"]
        send_telegram("❌ API вернул dict вместо списка по сплитам")
        return []

    if not isinstance(data, list):
        send_telegram("❌ API вернул неожиданный формат данных по сплитам")
        return []

    return data


def get_company_profile(symbol: str):
    url = f"{BASE_URL}/profile/{symbol}?apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if data is None:
        return None

    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        return data[0]

    return None


def is_allowed_exchange(profile: dict) -> bool:
    exchange = str(profile.get("exchangeShortName", "")).upper().strip()
    full_exchange = str(profile.get("exchange", "")).upper().strip()

    if "OTC" in exchange or "OTC" in full_exchange:
        return False

    return exchange in ALLOWED_EXCHANGES or full_exchange in ALLOWED_EXCHANGES


def format_ratio(split: dict) -> str:
    if split.get("ratio"):
        return str(split["ratio"])

    numerator = split.get("numerator")
    denominator = split.get("denominator")

    if numerator and denominator:
        return f"{numerator}:{denominator}"

    return "N/A"


def format_message(split: dict, profile: dict) -> str:
    symbol = split.get("symbol", "N/A")
    ratio = format_ratio(split)
    company = profile.get("companyName", symbol)
    exchange = profile.get("exchangeShortName", profile.get("exchange", "N/A"))
    split_date = split.get("date", "N/A")

    return (
        f"📊 <b>Stock Split Detected</b>\n\n"
        f"<b>{company}</b> ({symbol})\n"
        f"Exchange: {exchange}\n"
        f"Ratio: {ratio}\n"
        f"Date: {split_date}"
    )


def validate_env() -> bool:
    missing = []

    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")
    if not FMP_API_KEY:
        missing.append("FMP_API_KEY")

    if missing:
        print("Missing env vars:", ", ".join(missing))
        return False

    return True


def main():
    if not validate_env():
        return

    try:
        splits = get_recent_splits()

        if not splits:
            print("No splits found.")
            return

        sent_count = 0

        for split in splits:
            if not isinstance(split, dict):
                continue

            symbol = split.get("symbol")
            if not symbol:
                continue

            profile = get_company_profile(symbol)
            if not profile:
                continue

            if not is_allowed_exchange(profile):
                continue

            message = format_message(split, profile)
            send_telegram(message)
            sent_count += 1

        print(f"Done. Sent: {sent_count}")

    except Exception as e:
        send_telegram(f"❌ ERROR:\n{str(e)}")


if __name__ == "__main__":
    main()