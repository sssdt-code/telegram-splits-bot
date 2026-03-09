import os
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=payload, timeout=30)
    print("Telegram status:", response.status_code)
    print("Telegram response:", response.text)


def main():
    if not TELEGRAM_TOKEN:
        print("Missing TELEGRAM_TOKEN")
        return

    if not CHAT_ID:
        print("Missing CHAT_ID")
        return

    send_telegram("✅ TEST: Telegram from GitHub Actions works")


if __name__ == "__main__":
    main()