import os
import requests

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
        send_telegram(f"❌ API ERROR {response.status_code}\n{url}")
        return None

    try:
        return response.json()
    except Exception as e:
        send_telegram(f"❌ JSON decode error:\n{str(e)}")
        return None


def get_recent_splits():
    url = f"https://financialmodelingprep.com/stable/splits-calendar?apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if data is None:
        return []

    if not isinstance(data, list):
        send_telegram(f"❌ Unexpected splits format: {type(data).__name__}")
        return []

    cleaned = []
    for item in data:
        if isinstance(item, dict):
            cleaned.append(item)
        else:
            send_telegram(f"⚠️ Split item skipped:\n{repr(item)[:300]}")

    return cleaned


def get_company_profile(symbol: str):
    url = f"https://financialmodelingprep.com/api/v3/profile/{symbol}?apikey={FMP_API_KEY}"
    data = safe_get_json(url)

    if data is None:
        return None

    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        return data[0]

    if isinstance(data, dict):
        return data

    return None


def is_allowed_exchange(profile: dict) -> bool:
    exchange_short = str(profile.get("exchangeShortName", "")).upper().strip()
    exchange_full = str(profile.get("exchange", "")).upper().strip()

    if "OTC" in exchange_short or "OTC" in exchange_full:
        return False

    return exchange_short in ALLOWED_EXCHANGES or exchange_full in ALLOWED_EXCHANGES


def format_ratio(split: dict) -> str:
    if split.get("ratio"):
        return str(split["ratio"])

    numerator = split.get("numerator")
    denominator = split.get("denominator")

    if numerator and denominator:
        return f"{numerator}:{denominator}"

    return "N/A"


def format_message(split: dict, profile: dict) -> str:
    symbol = str(split.get("symbol", "N/A"))
    ratio = format_ratio(split)
    company = str(profile.get("companyName", symbol))
    exchange = str(profile.get("exchangeShortName", profile.get("exchange", "N/A")))
    split_date = str(split.get("date", "N/A"))

    return (
        f"📊 STOCK SPLIT\n\n"
        f"Company: {company}\n"
        f"Ticker: {symbol}\n"
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
        send_telegram("❌ Missing env vars: " + ", ".join(missing))
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

            profile = get_company_profile(str(symbol))
            if not profile or not isinstance(profile, dict):
                continue

            if not is_allowed_exchange(profile):
                continue

            message = format_message(split, profile)
            send_telegram(message)
            sent_count += 1

        print(f"Done. Sent: {sent_count}")

    except Exception as e:
        send_telegram(f"❌ ERROR:\n{type(e).__name__}: {str(e)}")


if __name__ == "__main__":
    main()