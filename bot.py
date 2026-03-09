import os
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FMP_API_KEY = os.getenv("FMP_API_KEY")


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=payload, timeout=30)
    print("Telegram status:", response.status_code)
    print("Telegram response:", response.text[:300])


def main():
    if not TELEGRAM_TOKEN:
        print("Missing TELEGRAM_TOKEN")
        return

    if not CHAT_ID:
        print("Missing CHAT_ID")
        return

    if not FMP_API_KEY:
        send_telegram("❌ Missing FMP_API_KEY")
        return

    send_telegram("🧪 DEBUG START: workflow reached bot.py")

    url = f"https://financialmodelingprep.com/stable/splits-calendar?apikey={FMP_API_KEY}"
    response = requests.get(url, timeout=30)

    send_telegram(
        "🧪 DEBUG FMP\n"
        f"status={response.status_code}\n"
        f"body={response.text[:500]}"
    )

    send_telegram("🧪 DEBUG END: bot.py finished")


if __name__ == "__main__":
    main()