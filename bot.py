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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    requests.post(url, json=payload, timeout=30)


def get_splits():
    url = f"https://financialmodelingprep.com/stable/splits-calendar?apikey={FMP_API_KEY}"
    response = requests.get(url, timeout=30)

    if response.status_code != 200:
        send_telegram(f"❌ FMP API ERROR {response.status_code}")
        return []

    try:
        data = response.json()
    except Exception:
        send_telegram("❌ FMP JSON decode error")
        return []

    if not isinstance(data, list):
        send_telegram("❌ Unexpected FMP format")
        return []

    return data


def is_allowed_exchange(exchange: str) -> bool:
    exchange = str(exchange).upper()
    if "OTC" in exchange:
        return False
    return exchange in ALLOWED_EXCHANGES


def main():
    if not FMP_API_KEY:
        send_telegram("❌ Missing FMP_API_KEY")
        return

    splits = get_splits()

    if not splits:
        print("No splits found.")
        return

    for split in splits:
        if not isinstance(split, dict):
            continue

        symbol = split.get("symbol")
        exchange = split.get("exchange")

        if not symbol or not exchange:
            continue

        if not is_allowed_exchange(exchange):
            continue

        ratio = split.get("ratio") or f"{split.get('numerator')}:{split.get('denominator')}"
        date = split.get("date")

        message = (
            f"📊 STOCK SPLIT\n\n"
            f"Ticker: {symbol}\n"
            f"Exchange: {exchange}\n"
            f"Ratio: {ratio}\n"
            f"Date: {date}"
        )

        send_telegram(message)


if __name__ == "__main__":
    main()