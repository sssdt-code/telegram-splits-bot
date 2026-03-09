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
    "ARCA"
}

BASE_URL = "https://financialmodelingprep.com/api/v3"


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    requests.post(url, json=payload)


def get_recent_splits():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    url = f"{BASE_URL}/stock_split_calendar?from={today}&to={today}&apikey={FMP_API_KEY}"
    response = requests.get(url)
    return response.json()


def get_company_profile(symbol):
    url = f"{BASE_URL}/profile/{symbol}?apikey={FMP_API_KEY}"
    response = requests.get(url)
    data = response.json()
    if data:
        return data[0]
    return None


def format_message(split, profile):
    symbol = split["symbol"]
    ratio = split["ratio"]
    company = profile.get("companyName", "")
    exchange = profile.get("exchangeShortName", "")

    message = (
        f"📊 <b>Stock Split Detected</b>\n\n"
        f"<b>{company}</b> ({symbol})\n"
        f"Exchange: {exchange}\n"
        f"Ratio: {ratio}\n"
        f"Date: {split['date']}"
    )
    return message


def main():
    try:
        splits = get_recent_splits()

        if not splits:
            print("No splits today.")
            return

        for split in splits:
            symbol = split["symbol"]
            profile = get_company_profile(symbol)

            if not profile:
                continue

            exchange = profile.get("exchangeShortName", "")

            if exchange not in ALLOWED_EXCHANGES:
                continue

            message = format_message(split, profile)
            send_telegram(message)

    except Exception as e:
        send_telegram(f"❌ ERROR:\n{str(e)}")


if __name__ == "__main__":
    main()
